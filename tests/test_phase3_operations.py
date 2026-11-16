import json
from unittest.mock import patch
from django.test import Client, TestCase
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import SavedLookup, WatchRule, ServiceObservation


class OperationsAPITests(TestCase):
    def post(self, path, body):
        return self.client.post('/api/operations/' + path, json.dumps(body), content_type='application/json')

    def test_create_lookup_schedule_tags_and_manual_run(self):
        body = {'name': 'Fixture storage', 'provider': 'netlas', 'query': 'port:443', 'service_type': 'https', 'interval_seconds': 3600, 'enabled': True, 'tags': ['Storage'], 'auto_fetch': True, 'actions': ['http']}
        r = self.post('lookups', body)
        self.assertEqual(r.status_code, 200, r.content)
        item = r.json()['lookup']
        self.assertEqual(item['tags'], ['storage'])
        self.assertIsNotNone(item['next_run_at'])
        self.assertEqual(self.post('lookups/' + item['id'] + '/toggle', {'enabled': False}).status_code, 200)
        self.assertFalse(SavedLookup.objects.get().enabled)
        self.assertEqual(self.post('lookups', dict(body, interval_seconds=1)).status_code, 400)

    def test_builtin_certstream_default_and_external_explicit(self):
        item = self.client.get('/api/operations/stream').json()
        self.assertEqual(item['mode'], 'builtin')
        self.assertEqual(self.post('stream', {'mode': 'external', 'external_url': 'https://bad.test'}).status_code, 400)
        self.assertEqual(self.post('stream', {'mode': 'external', 'external_url': 'wss://feed.example.test/path'}).status_code, 200)
        self.assertEqual(self.client.get('/api/operations/stream').json()['mode'], 'external')

    def test_watch_auto_fetch_and_domain_validation(self):
        body = {'name': 'Fixture watch', 'domains': ['Example.test'], 'ports': [443], 'actions': ['http'], 'auto_fetch': True}
        r = self.post('watches', body)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(WatchRule.objects.get().domains, ['example.test'])
        self.assertEqual(self.post('watches', dict(body, domains=['https://bad.test/path'])).status_code, 400)

    def test_endpoint_tags_filters_detail_masking_and_analysis(self):
        endpoint = ingest_observation('netlas', {'address': '192.0.2.1', 'port': 443, 'service_type': 'https', 'metadata': {'password': 'private-value'}})
        self.assertEqual(self.post('tags', {'target_kind': 'endpoint', 'target_key': str(endpoint.pk), 'tags': ['Review']}).status_code, 200)
        r = self.client.get('/api/operations/endpoints?tag=review')
        self.assertEqual(r.json()['count'], 1)
        self.assertNotIn('private-value', self.client.get('/api/operations/endpoints/' + str(endpoint.pk)).content.decode())
        r = self.client.get('/api/operations/analysis')
        self.assertEqual(r.json()['counts']['endpoints'], 1)
        self.assertEqual(r.json()['by_provider'][0]['label'], 'netlas')
        self.assertEqual(self.post('tags', {'target_kind': 'endpoint', 'target_key': 'missing', 'tags': ['x']}).status_code, 400)

    def test_mutations_require_csrf_and_catalog_is_read_only(self):
        client = Client(enforce_csrf_checks=True)
        r = client.post('/api/operations/watches', '{}', content_type='application/json')
        self.assertEqual(r.status_code, 403)
        r = self.client.get('/api/operations/catalog')
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()['results']), 60)
        self.assertEqual(self.client.get('/api/operations/tags').status_code, 400)

    def test_legacy_findings_and_custom_queries_expose_saved_tags(self):
        from leakscope_app.models import Search, Gitlab, CustomQuery
        source = Search.objects.create(type='gitlab')
        row = Gitlab.objects.create(search=source, ip='192.0.2.4', port='443')
        self.post('tags', {'target_kind': 'finding', 'target_key': 'gitlab:' + str(row.pk), 'tags': ['Review']})
        self.assertEqual(self.client.get('/api/findings/gitlab/' + str(row.pk)).json()['finding']['tags'], ['review'])
        query = CustomQuery.objects.create(name='Fixture', query_template='port:443')
        self.post('tags', {'target_kind': 'query', 'target_key': str(query.pk), 'tags': ['Saved']})
        self.assertEqual(self.client.get('/api/queries').json()['results'][0]['tags'], ['saved'])

    @patch('leakscope.phase3.jobs.queue_lookup')
    def test_main_discovery_and_custom_query_route_netlas_to_normalized_jobs(self, queue):
        from leakscope_app.models import Run, CustomQuery
        queue.return_value = Run.objects.create(kind='lookup', provider='netlas')
        r = self.client.post('/api/query-preview', json.dumps({'provider': 'netlas', 'types': ['mongo'], 'network': '192.0.2.0/24'}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('ip:', r.json()['queries'][0]['query'])
        r = self.client.post('/api/discover', json.dumps({'provider': 'netlas', 'types': ['mongo']}), content_type='application/json')
        self.assertEqual(r.status_code, 202, r.content)

        self.assertTrue(queue.called)
        query = CustomQuery.objects.create(name='Netlas custom', provider='netlas', query_template='port:443')
        r = self.client.post(f'/api/queries/{query.pk}/run', '{}', content_type='application/json')
        self.assertEqual(r.status_code, 202, r.content)

    def test_invalid_tags_rollback_lookup_and_watch_creation(self):
        r = self.post('lookups', {'name': 'Bad tags', 'provider': 'netlas', 'query': 'port:443', 'enabled': True, 'interval_seconds': 60, 'tags': ['x' * 65]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(SavedLookup.objects.count(), 0)
        r = self.post('watches', {'name': 'Bad tags', 'domains': ['example.test'], 'tags': ['x' * 65]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(WatchRule.objects.count(), 0)

    def test_wildcard_watch_keeps_apex_exclusion(self):
        r = self.post('watches', {'name': 'Subdomains only', 'domains': ['*.Example.test']})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(WatchRule.objects.get().domains, ['*.example.test'])

    def test_tag_editor_separates_inherited_and_manual_tags(self):
        from leakscope.phase3.state import set_tags
        item = ingest_observation('netlas', {'address': '192.0.2.8', 'port': 443})
        set_tags('endpoint', item.pk, ['Inherited'], origin='lookup:fixture')
        r = self.client.get(f'/api/operations/tags?target_kind=endpoint&target_key={item.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['manual_tags'], [])
        self.assertEqual(r.json()['inherited_tags'], ['inherited'])

    def test_classified_metadata_keys_masked_but_token_counts_visible(self):
        values = {key: 'private-fixture' for key in ['token', 'refresh_token', 'access_key', 'private_key', 'api-key']}
        values['token_count'] = 15
        item = ingest_observation('netlas', {'address': '192.0.2.14', 'port': 443, 'metadata': values})
        response = self.client.get('/api/operations/endpoints/' + str(item.pk))
        self.assertNotIn('private-fixture', response.content.decode())
        self.assertEqual(response.json()['observations'][0]['data']['metadata']['token_count'], 15)

    @patch('leakscope.phase3.jobs.enrich_endpoint.apply_async')
    @patch('leakscope.phase3.jobs.enrich_host')
    def test_netlas_enrichment_is_explicit_and_preserves_observed_source(self, enrich, queued):
        enrich.return_value = {'results': {'host': {'domains': ['example.test']}, 'dns': [], 'certificates': []}, 'errors': [], 'partial': False}
        item = ingest_observation('netlas', {'address': '192.0.2.1', 'port': 443})
        r = self.post('endpoints/' + str(item.pk) + '/enrich', {'collections': ['host']})
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.json()['run']['state'], 'queued')
        queued.assert_called_once()
        enrich.assert_not_called()
        from leakscope.phase3.jobs import enrich_endpoint
        from leakscope_app.models import Run
        run = Run.objects.get(pk=r.json()['run']['id'])
        enrich_endpoint.apply(kwargs={'endpoint_id': str(item.pk), 'collections': ['host']}, task_id=run.task_id).get()
        run.refresh_from_db()
        self.assertEqual(run.state, 'succeeded')
        self.assertTrue(ServiceObservation.objects.filter(provider='netlas:enrich', endpoint=item).exists())

    def test_stream_feed_reports_saved_certificate_matches_without_service_claims(self):
        from leakscope_app.models import CertificateRecord, CertificateWatchMatch
        ingest_observation('certstream', {'address': 'legacy.fixture.test', 'port': 443})
        certificate = CertificateRecord.objects.create(key='a' * 64, domains=['*.fixture.test', 'api.fixture.test'])
        watch = WatchRule.objects.create(name='Fixture scope', domains=['fixture.test'])
        match = CertificateWatchMatch.objects.create(certificate=certificate, watch=watch, watch_key=str(watch.pk),
            watch_name=watch.name, matched_names=['*.fixture.test', 'api.fixture.test'])
        r = self.client.get('/api/operations/stream')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['recent_matches']), 1)
        item = r.json()['recent_matches'][0]
        self.assertEqual(item['id'], str(match.pk))
        self.assertEqual(item['certificate_id'], str(certificate.pk))
        self.assertEqual(item['names'], ['*.fixture.test', 'api.fixture.test'])
        self.assertEqual(item['watch_name'], 'Fixture scope')
        self.assertNotIn('endpoint_id', item)
        self.assertNotIn('port', item)
        watch.delete()
        self.assertEqual(self.client.get('/api/operations/stream').json()['recent_matches'][0]['watch_id'], match.watch_key)

    def test_tags_accept_saved_certificate_and_asset_identities(self):
        from leakscope_app.models import AssetNode, CertificateRecord
        for kind, row in [('certificate', CertificateRecord.objects.create(key='b' * 64, domains=['fixture.test'])),
                          ('asset', AssetNode.objects.create(key='c' * 64, kind='domain', value='fixture.test'))]:
            with self.subTest(kind=kind):
                response = self.post('tags', {'target_kind': kind, 'target_key': str(row.pk), 'tags': ['Review']})
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()['manual_tags'], ['review'])
                self.assertEqual(self.post('tags', {'target_kind': kind, 'target_key': 'missing', 'tags': ['x']}).status_code, 400)

    def test_observe_only_watch_requires_no_fetch_configuration(self):
        response = self.post('watches', {'name': 'Certificate scope', 'domains': ['fixture.test']})
        self.assertEqual(response.status_code, 200, response.content)
        watch = response.json()['watch']
        self.assertFalse(watch['auto_fetch'])
        self.assertEqual(watch['ports'], [])
        self.assertEqual(watch['actions'], [])
