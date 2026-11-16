import json
from unittest.mock import patch
from django.test import TestCase


class OrganizationDomainTests(TestCase):
    def setUp(self):
        from leakscope.phase3 import relationships as store
        self.org=store.node('organization','Fixture Corp',{'aliases':['Fixture'],'keep':'original'})

    def add(self,domains,**extra):
        return self.client.post(f'/api/assets/organizations/{self.org.pk}/domains',
            json.dumps({'domains':domains,**extra}),content_type='application/json')

    @patch('socket.getaddrinfo',side_effect=AssertionError('No external lookup when adding scope'))
    def test_append_normalizes_deduplicates_and_preserves_existing_organization(self,network):
        from leakscope_app.models import AssetRelationship
        first=self.add(['EXAMPLE.com.','api.example.com'])
        self.assertEqual(first.status_code,200,first.content)
        self.assertEqual(first.json()['added'],['example.com','api.example.com'])
        again=self.add(['example.com','*.example.com','example.com'])
        self.assertEqual(again.json()['added'],['*.example.com'])
        self.assertEqual(again.json()['already_linked'],['example.com'])
        self.org.refresh_from_db()
        self.assertEqual(self.org.attributes,{'aliases':['Fixture'],'keep':'original'})
        self.assertEqual(AssetRelationship.objects.filter(source=self.org,relation='user_scope').count(),3)
        detail=self.client.get(f'/api/assets/organizations/{self.org.pk}')
        self.assertEqual(detail.status_code,200)
        self.assertEqual({d['name'] for d in detail.json()['domains']},{'example.com','api.example.com','*.example.com'})
        self.assertTrue(all(d['source']=='user_scope' for d in detail.json()['domains']))
        network.assert_not_called()

    def test_creation_and_later_addition_share_membership_identity(self):
        from leakscope_app.models import AssetRelationship
        created=self.client.post('/api/assets/organizations',json.dumps({'name':'Fixture Corp','aliases':['Fixture'],
            'domains':['example.com']}),content_type='application/json')
        self.assertEqual(created.status_code,200)
        self.assertEqual(self.add(['example.com']).json()['already_linked'],['example.com'])
        self.assertEqual(AssetRelationship.objects.filter(source=self.org,relation='user_scope').count(),1)

    def test_invalid_batch_is_atomic_and_does_not_add_valid_prefix(self):
        from leakscope_app.models import AssetRelationship
        for invalid in ['https://example.com','192.0.2.1','com','co.uk','<script>','bad name.example',None]:
            result=self.add(['valid.example.com',invalid])
            self.assertEqual(result.status_code,400,(invalid,result.content))
        self.assertFalse(AssetRelationship.objects.filter(source=self.org,relation='user_scope').exists())
        self.assertEqual(self.add([]).status_code,400)
        self.assertEqual(self.add(['api'+str(n)+'.example.com' for n in range(31)]).status_code,400)

    def test_result_names_are_validated_and_provenance_is_saved(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.models import AssetRelationship
        ep=ingest_observation('shodan',{'address':'192.0.2.7','port':8443,'metadata':{'hostnames':['api.example.com']}})
        ref={'type':'endpoint','id':str(ep.pk)}
        self.assertEqual(self.add(['invented.example.com'],reference=ref).status_code,400)
        result=self.add(['api.example.com'],reference=ref)
        self.assertEqual(result.status_code,200,result.content)
        proof=AssetRelationship.objects.get(source=self.org,relation='user_scope',target__kind='domain').evidence
        self.assertEqual(proof.source,'user_scope')
        self.assertEqual(proof.data['reference'],ref)
        self.assertEqual(proof.data['origin'],'result_selection')
        self.assertIn('ownership unverified',proof.data['assertion'])
        ep.observations.update(hidden=True)
        self.assertEqual(self.add(['api.example.com'],reference=ref).status_code,404)

    def test_certificate_name_can_be_added_literally_without_lookup(self):
        from leakscope.phase3.certificate_store import store_certificate
        cert=store_certificate({'certificate':{'domains':['*.example.com','example.com']}},'ab'*32)
        result=self.add(['*.example.com'],reference={'type':'certificate','id':str(cert.pk)})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['domains'][0]['name'],'*.example.com')

    def test_only_existing_organizations_are_editable(self):
        from uuid import uuid4
        from leakscope.phase3 import relationships as store
        for key in [uuid4(),store.node('domain','example.com').pk]:
            response=self.client.post(f'/api/assets/organizations/{key}/domains',json.dumps({'domains':['api.example.com']}),content_type='application/json')
            self.assertEqual(response.status_code,404)

    def test_ip_only_result_can_be_added_without_domains_and_keeps_its_port(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.models import AssetRelationship
        ep=ingest_observation('zoomeye',{'address':'192.0.2.9','port':2227,'metadata':{}})
        result=self.add([],reference={'type':'endpoint','id':str(ep.pk)})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['linked_result']['kind'],'service')
        self.assertIn(':2227',result.json()['linked_result']['value'])
        again=self.add([],reference={'type':'endpoint','id':str(ep.pk)})
        self.assertTrue(again.json()['result_already_linked'])
        self.assertEqual(AssetRelationship.objects.filter(source=self.org,relation='user_scope').count(),1)

    def test_legacy_non_host_finding_is_linked_by_its_saved_identity(self):
        from leakscope_app.models import Search,Github
        row=Github.objects.create(commit='fixture',path='src/app.py',secret='fixture',keyword='fixture',provider='unknown')
        result=self.add([],reference={'type':'github','id':row.pk})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['linked_result']['kind'],'finding')
        self.assertEqual(result.json()['linked_result']['value'],f'github:{row.pk}')
        from leakscope.services.identity import set_hidden
        set_hidden('github',row,True)
        detail=self.client.get(f'/api/assets/organizations/{self.org.pk}').json()
        self.assertEqual(detail['members'],[])
        self.assertEqual(self.add([],reference={'type':'github','id':row.pk}).status_code,404)

    def test_saved_asset_and_literal_ip_can_be_associated(self):
        from leakscope.phase3 import relationships as store
        node=store.node('ip','192.0.2.12')
        self.assertEqual(self.add([],reference={'type':'asset','id':str(node.pk)}).status_code,200)
        self.assertTrue(self.add([],target='192.0.2.12').json()['result_already_linked'])

    def test_other_port_hostnames_are_not_attributed_to_selected_result(self):
        from leakscope.phase3.state import ingest_observation
        ep=ingest_observation('shodan',{'address':'192.0.2.19','port':1111,'metadata':{}})
        ingest_observation('zoomeye',{'address':'192.0.2.19','port':2222,'metadata':{'hostnames':['other.example.com']}})
        result=self.add(['other.example.com'],reference={'type':'endpoint','id':str(ep.pk)})
        self.assertEqual(result.status_code,400,result.content)

    def test_distinct_cloud_resources_keep_their_references_on_shared_endpoint(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.models import CloudResource
        ep=ingest_observation('netlas',{'address':'192.0.2.21','port':443,'metadata':{}})
        self.assertEqual(self.add([],reference={'type':'endpoint','id':str(ep.pk)}).status_code,200)
        for name in ['one','two']:
            resource=CloudResource.objects.create(url=f'https://example.com/{name}',provider='aws',name=name,kind='bucket',state='public',endpoint=ep)
            result=self.add([],reference={'type':'cloud','id':str(resource.pk)})
            self.assertEqual(result.status_code,200,result.content)
            self.assertFalse(result.json()['result_already_linked'])
        self.assertEqual(len(self.client.get(f'/api/assets/organizations/{self.org.pk}').json()['members']),3)

    def test_domain_provenance_uses_the_observation_that_supplied_the_name(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.models import AssetRelationship
        ep=ingest_observation('shodan',{'address':'192.0.2.22','port':443,'metadata':{'hostnames':['secret.example.com']}})
        source=ep.observations.get(provider='shodan')
        ingest_observation('netlas',{'address':ep.address,'port':443,'metadata':{}})
        result=self.add(['secret.example.com'],reference={'type':'endpoint','id':str(ep.pk)})
        self.assertEqual(result.status_code,200,result.content)
        proof=AssetRelationship.objects.get(source=self.org,relation='user_scope',target__kind='domain').evidence
        self.assertEqual(proof.data['observation_pk'],source.pk.hex)
        source.hidden=True;source.save(update_fields=['hidden'])
        self.assertEqual(self.client.get(f'/api/assets/organizations/{self.org.pk}').json()['domains'],[])

    def test_graph_only_name_proof_retains_original_hidden_source_dependency(self):
        from leakscope.phase3.state import ingest_observation
        ep=ingest_observation('shodan',{'address':'192.0.2.23','port':443,'metadata':{'hostnames':['historic.example.com']}})
        source=ep.observations.get(provider='shodan')
        for version in range(31):
            ingest_observation('netlas',{'address':ep.address,'port':443,'metadata':{'version':version}})
        response=self.add(['historic.example.com'],reference={'type':'endpoint','id':str(ep.pk)})
        self.assertEqual(response.status_code,200,response.content)
        source.hidden=True;source.save(update_fields=['hidden'])
        self.assertEqual(self.client.get(f'/api/assets/organizations/{self.org.pk}').json()['domains'],[])

    def test_result_identity_remains_linked_while_any_endpoint_source_is_visible(self):
        from leakscope.phase3.state import ingest_observation
        ep=ingest_observation('shodan',{'address':'192.0.2.24','port':443,'metadata':{}})
        ingest_observation('netlas',{'address':ep.address,'port':443,'metadata':{}})
        response=self.add([],reference={'type':'endpoint','id':str(ep.pk)})
        self.assertEqual(response.status_code,200,response.content)
        ep.observations.filter(provider='netlas').update(hidden=True)
        self.assertEqual(len(self.client.get(f'/api/assets/organizations/{self.org.pk}').json()['members']),1)
        ep.observations.update(hidden=True)
        self.assertEqual(self.client.get(f'/api/assets/organizations/{self.org.pk}').json()['members'],[])
