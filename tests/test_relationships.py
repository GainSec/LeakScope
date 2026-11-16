"""Fixture-only tests of the evidence-backed asset graph."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from leakscope.phase3 import relationships as store
from leakscope_app.models_relationships import AssetNode, AssetEvidence, AssetRelationship, CertificateRecord
from leakscope_app.models import ServiceEndpoint, ServiceObservation, CloudResource


class RelationshipStoreTests(TestCase):
    def test_evidence_layer_keeps_dns_and_identity_beyond_expansion_candidates(self):
        organization = store.node('organization', 'Fixture Org')
        domain = store.node('domain', 'example.com')
        scope = store.evidence(organization, 'user_scope', {'domain': domain.value})
        store.relation(organization, domain, 'user_scope', scope)
        address = store.node('ip', '192.0.2.9')
        dns = store.evidence(domain, 'dns', {'name': domain.value, 'type': 'A', 'value': address.value})
        store.relation(domain, address, 'resolves_to', dns)
        identifier = store.node('identifier', 'public-app:fixture')
        declaration = store.evidence(domain, 'public_identity:android', {'package': 'fixture'})
        store.relation(domain, identifier, 'declares_app', declaration)
        for n in range(20):
            candidate = store.node('domain', 'lead%d.example.com' % n)
            proof = store.evidence(candidate, 'domain_expansion', {'candidate': candidate.value})
            store.relation(organization, candidate, 'domain_candidate', proof)
        graph = store.graph(root_id=organization.pk, max_nodes=6, max_depth=3, include_candidates=False)
        self.assertEqual({node['value'] for node in graph['nodes']},
            {'Fixture Org', 'example.com', '192.0.2.9', 'public-app:fixture'})
        self.assertEqual({edge['relation'] for edge in graph['edges']}, {'user_scope', 'resolves_to', 'declares_app'})
        self.assertFalse(graph['layer']['include_candidates'])
        self.assertIn('research', ' '.join(graph['caveats']).lower())
        self.assertTrue(any(edge['relation'] == 'domain_candidate' for edge in store.graph(root_id=organization.pk)['edges']))
        self.assertTrue(any(edge['relation'] == 'domain_candidate' for edge in store.graph(root_id=organization.pk, include_candidates=True)['edges']))
        orphan = store.node('domain', 'orphan.net')
        store.evidence(orphan, 'domain_expansion', {'candidate': orphan.value})
        recent = store.graph(include_candidates=False)
        self.assertFalse(any(node['value'].startswith('lead') for node in recent['nodes']))
        self.assertNotIn('orphan.net', {node['value'] for node in recent['nodes']})
        self.assertIn('192.0.2.9', {node['value'] for node in recent['nodes']})

    def test_evidence_layer_preserves_independently_observed_domain_structure(self):
        domain = store.node('domain', 'api.example.com')
        store.evidence(domain, 'domain_expansion', {'candidate': domain.value})
        store.evidence(domain, 'dns', {'name': domain.value, 'type': 'TXT', 'value': 'fixture'})
        graph = store.graph(root_id=domain.pk, include_candidates=False)
        self.assertTrue(any(edge['relation'] == 'subdomain_of' and edge['evidence_source'] == 'dns' for edge in graph['edges']))
        self.assertFalse(any(edge['evidence_source'] == 'domain_expansion' for edge in graph['edges']))

    def test_evidence_layer_still_suppresses_hidden_observation_proof(self):
        from leakscope.phase3.state import ingest_observation
        endpoint = ingest_observation('shodan', {'address': '192.0.2.19', 'port': 1234,
            'metadata': {'hostnames': ['secret.example.com']}})
        endpoint.observations.update(hidden=True)
        self.assertEqual(store.graph(include_candidates=False)['nodes'], [])
        self.assertEqual(store.graph(include_candidates=False)['edges'], [])

    def test_hidden_only_nodes_and_derived_hierarchy_disappear(self):
        from leakscope.phase3.state import ingest_observation
        endpoint=ingest_observation('shodan',{'address':'192.0.2.19','port':1234,'metadata':{'hostnames':['secret.example.com']}})
        self.assertIn('secret.example.com',str(store.graph()))
        endpoint.observations.update(hidden=True)
        graph=store.graph()
        self.assertEqual(graph['nodes'],[])
        self.assertEqual(graph['edges'],[])

    def test_differing_registrant_stays_linked_as_candidate(self):
        root=store.ingest_organization({'organization':'Query Corp','domains':[{'name':'example.com',
            'organization':'Actual Corp','relation':'registrant_organization','confidence':40}]})
        graph=store.graph(root_id=str(root.pk))
        self.assertIn('example.com',[node['value'] for node in graph['nodes']])
        self.assertTrue(any(edge['relation']=='registration_candidate' for edge in graph['edges']))

    def test_normalized_node_identity_is_typed_and_merges_attributes(self):
        first = store.node('domain', 'EXAMPLE.COM.', {'first': True})
        second = store.node('domain', 'example.com', {'second': True})
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.attributes, {'first': True, 'second': True})
        self.assertEqual(store.node('ip', '2001:0db8::1').value, '2001:db8::1')
        self.assertEqual(store.node('network', '192.0.2.8/24').value, '192.0.2.0/24')
        self.assertEqual(store.node('asn', 'AS64500').value, '64500')
        with self.assertRaises(ValueError): store.node('domain', 'https://example.com/path')

    def test_evidence_deduplicates_ttl_churn_and_retains_latest_observation(self):
        subject = store.node('domain', 'example.com')
        earlier = timezone.now() - timedelta(hours=1)
        first = store.evidence(subject, 'dns', {'name': 'example.com', 'type': 'A', 'value': '192.0.2.1', 'ttl': 60}, observed_at=earlier)
        later = earlier + timedelta(minutes=5)
        latest = store.evidence(subject, 'dns', {'name': 'example.com', 'type': 'A', 'value': '192.0.2.1', 'ttl': 300}, observed_at=later, expires_at=later + timedelta(seconds=300), source_url='https://resolver.example/evidence')
        self.assertEqual(first.pk, latest.pk)
        self.assertEqual(AssetEvidence.objects.count(), 1)
        self.assertEqual(latest.observed_at, later)
        self.assertEqual(latest.data['ttl'], 300)
        self.assertEqual(latest.source_url, 'https://resolver.example/evidence')
        store.evidence(subject, 'dns', {'name': 'example.com', 'type': 'A', 'value': '192.0.2.1', 'ttl': 60}, observed_at=earlier)
        latest.refresh_from_db()
        self.assertEqual(latest.observed_at, later)
        self.assertEqual(latest.data['ttl'], 300)

    def test_secrets_are_redacted_in_evidence_and_labels(self):
        with patch('leakscope.phase3.state.get_config', return_value={'config': {'netlas': {'api_key': 'configured-secret'}}}):
            subject = store.node('organization', 'Example', {'note': 'configured-secret'}, label='configured-secret')
            proof = store.evidence(subject, 'fixture', {'token': 'configured-secret'})
        self.assertNotIn('configured-secret', str(subject.attributes) + subject.label + str(proof.data))

    def test_dns_record_and_service_edges_do_not_claim_ownership(self):
        root = store.ingest_dns({'target': 'example.com', 'records': [
            {'name': 'example.com', 'type': 'A', 'value': '192.0.2.1', 'ttl': 30},
            {'name': 'example.com', 'type': 'CNAME', 'value': 'edge.vendor.example.'},
            {'name': 'example.com', 'type': 'MX', 'value': '10 mail.vendor.example.'}],
            'services': [{'provider': 'Vendor', 'category': 'email', 'relation': 'uses_mail', 'confidence': 90, 'evidence': [{'type': 'MX', 'value': '10 mail.vendor.example.'}]}]})
        self.assertEqual(root.value, 'example.com')
        self.assertTrue(AssetRelationship.objects.filter(source=root, relation='resolves_to', target__value='192.0.2.1').exists())
        self.assertTrue(AssetRelationship.objects.filter(source=root, relation='cname_to', target__value='edge.vendor.example').exists())
        self.assertTrue(AssetRelationship.objects.filter(source=root, relation='uses_mail', target__kind='organization').exists())
        self.assertFalse(AssetRelationship.objects.filter(relation__in=['owns', 'owned_by', 'registered_to']).exists())
        self.assertEqual(AssetRelationship.objects.filter(evidence__isnull=True).count(), 0)

    def test_name_sources_and_forward_confirmation_remain_distinct(self):
        root = store.ingest_names({'target': '192.0.2.1', 'names': [
            {'name': 'ptr.example.com', 'source': 'ptr', 'forward_confirmed': True},
            {'name': '*.example.com', 'source': 'certificate'},
            {'name': 'indexed.example.com', 'source': 'provider'}]})
        labels = set(AssetRelationship.objects.filter(source=root).values_list('relation', flat=True))
        self.assertEqual(labels, {'ptr_name', 'certificate_name', 'provider_name'})
        proof = AssetRelationship.objects.get(source=root, relation='ptr_name').evidence
        self.assertTrue(proof.data['forward_confirmed'])
        self.assertTrue(AssetNode.objects.filter(value='*.example.com').exists())

    def test_registration_edges_are_limited_to_evidenced_registered_holder(self):
        root = store.ingest_netblock({'target': '192.0.2.1', 'networks': [{'cidr': '192.0.2.0/24', 'organization': 'Registered Holder', 'source': 'rdap', 'source_url': 'https://rdap.example/net/1'}]})
        network = AssetNode.objects.get(kind='network')
        self.assertTrue(AssetRelationship.objects.filter(source=root, target=network, relation='within_allocation').exists())
        self.assertTrue(AssetRelationship.objects.filter(source=network, target__value='Registered Holder', relation='registered_to').exists())
        self.assertFalse(AssetRelationship.objects.filter(source=root, relation='registered_to').exists())

    def test_organization_candidates_and_seed_scope_use_precise_relations(self):
        organization = store.ingest_organization({'organization': 'Acme', 'domains': [{'name': 'example.com', 'organization': 'Acme', 'relation': 'registrant_organization', 'source': 'netlas', 'source_url': 'https://netlas.example/domain'}], 'networks': [{'cidr': '192.0.2.0/24', 'organization': 'Different Holder', 'relation': 'registered_to', 'source': 'netlas'}], 'seed_domains': ['seed.example.com']})
        self.assertTrue(AssetRelationship.objects.filter(source__value='example.com', target=organization, relation='registrant_organization').exists())
        self.assertTrue(AssetRelationship.objects.filter(source=organization, target__value='seed.example.com', relation='user_scope').exists())
        self.assertFalse(AssetRelationship.objects.filter(source__kind='network', target=organization, relation='registered_to').exists())

    def test_saved_certificate_has_name_edges_without_service_claims(self):
        record = CertificateRecord.objects.create(key='a'*64, domains=['*.example.com', 'example.com'], issuer={'O': 'Certificate Authority'}, data={})
        certificate = store.ingest_certificate_graph(record)
        self.assertEqual(certificate.kind, 'certificate')
        self.assertEqual(AssetRelationship.objects.filter(source=certificate, relation='certificate_name').count(), 2)
        self.assertFalse(AssetNode.objects.filter(kind__in=['service', 'organization']).exists())
        graph = store.graph(root_id=str(certificate.pk))
        self.assertIn('historical', ' '.join(graph['caveats']).lower())
        self.assertTrue(all(edge['evidence_source'] in {'certstream','domain_structure'} for edge in graph['edges']))

    def test_saved_observation_and_cloud_links_reference_existing_records(self):
        endpoint = ServiceEndpoint.objects.create(key='b'*64, address='example.com', port=8443, transport='tcp', scheme='https')
        observation = ServiceObservation.objects.create(endpoint=endpoint, provider='netlas', fingerprint='c'*64, data={'metadata': {}})
        service = store.ingest_observation_graph(observation)
        self.assertEqual(service.kind, 'service')
        self.assertTrue(AssetRelationship.objects.filter(source__value='example.com', target=service, relation='observed_service', evidence__data__observation_id=str(observation.pk)).exists())
        cloud = CloudResource.objects.create(url='https://bucket.example.com', provider='aws', name='bucket', kind='bucket', state='public', evidence={}, endpoint=endpoint)
        store.ingest_cloud_graph(cloud)
        self.assertTrue(AssetEvidence.objects.filter(data__cloud_resource_id=str(cloud.pk)).exists())
        observation.hidden = True
        observation.save(update_fields=['hidden'])
        graph = store.graph(root_id=str(service.pk))
        self.assertFalse(any(e['evidence_source'] == 'netlas' for e in graph['edges']))

    def test_graph_limits_connected_traversal_without_dangling_edges(self):
        root = store.node('domain', 'example.com')
        proof = store.evidence(root, 'fixture', {})
        for index in range(12):
            target = store.node('ip', '192.0.2.' + str(index + 1))
            store.relation(root, target, 'resolves_to', proof)
        store.node('organization', 'Unrelated')
        with self.assertNumQueries(3):
            result = store.graph(root_id=str(root.pk), max_nodes=4, max_depth=1)
        self.assertEqual(len(result['nodes']), 4)
        self.assertTrue(result['limited'])
        ids = {n['id'] for n in result['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in result['edges']))
        self.assertNotIn('Unrelated', {n['value'] for n in result['nodes']})

    def test_evidence_expiry_is_visible_and_graph_reads_do_not_write(self):
        root = store.node('domain', 'example.com')
        proof = store.evidence(root, 'dns', {}, expires_at=timezone.now() - timedelta(seconds=1))
        store.relation(root, store.node('ip', '192.0.2.1'), 'resolves_to', proof, confidence=90)
        with self.assertNumQueries(3):
            result = store.graph(root_id=str(root.pk), max_depth=1)
        self.assertTrue(result['edges'][0]['expired'])
        self.assertEqual(result['edges'][0]['confidence'], 90)

    def test_adapter_ptr_source_retains_indexed_dns_distinction(self):
        root = store.ingest_names({'target': '192.0.2.1', 'names': [
            {'name': 'ptr.example.com', 'source': 'dns:ptr', 'forward_confirmed': False},
            {'name': 'indexed.example.com', 'source': 'netlas:indexed_dns'}]})
        self.assertTrue(AssetRelationship.objects.filter(source=root, relation='ptr_name').exists())
        self.assertTrue(AssetRelationship.objects.filter(source=root, relation='indexed_dns_name').exists())

    def test_dns_policy_records_keep_provenance_and_signal_owner_expiry(self):
        expiry = timezone.now() + timedelta(seconds=60)
        record = {'name': '_dmarc.example.com', 'type': 'TXT', 'value': 'v=DMARC1; p=reject', 'expires_at': expiry.isoformat()}
        mail = {'name': 'example.com', 'type': 'MX', 'value': '10 mail.vendor.example.', 'expires_at': expiry.isoformat()}
        root = store.ingest_dns({'target': 'www.example.com', 'records': [record, mail], 'services': [
            {'provider': 'Vendor', 'relation': 'uses_mail', 'evidence': [mail], 'confidence': 85}]})
        self.assertTrue(AssetEvidence.objects.filter(data__name='_dmarc.example.com').exists())
        signal = AssetRelationship.objects.get(relation='uses_mail')
        self.assertEqual(signal.source.value, 'example.com')
        self.assertNotEqual(signal.source, root)
        self.assertEqual(signal.evidence.expires_at, expiry)

    def test_registry_confidence_remains_attached_to_registration_fact(self):
        store.ingest_netblock({'target': '192.0.2.1', 'networks': [
            {'cidr': '192.0.2.0/24', 'organization': 'Holder', 'confidence': 80, 'source': 'rdap'}]})
        self.assertEqual(AssetRelationship.objects.get(relation='registered_to').confidence, 80)

    def test_invalid_root_returns_empty_graph(self):
        self.assertEqual(store.graph(root_id='invalid')['nodes'], [])
        with self.assertRaises(ValueError): store.node('domain', None)
        with self.assertRaises(ValueError): store.node('organization', 'a' * 501)

    def test_saved_observation_links_use_existing_endpoint_route(self):
        endpoint = ServiceEndpoint.objects.create(key='e'*64, address='example.com', port=443, transport='tcp', scheme='https')
        observation = ServiceObservation.objects.create(endpoint=endpoint, provider='netlas', fingerprint='f'*64, data={})
        service = store.ingest_observation_graph(observation)
        self.assertEqual(AssetEvidence.objects.get(subject=service).source_url, '/api/operations/endpoints/' + str(endpoint.pk))
