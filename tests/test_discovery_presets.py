import importlib.util
import json
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from leakscope.phase3.state import ingest_observation
from leakscope_app.models import Run


class DiscoveryPresetTests(TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('leakscope_app.presets'), 'Discovery preset API is not implemented')
        from leakscope_app import presets
        self.api = presets
        self.factory = RequestFactory()
        self.endpoint = ingest_observation('fixture', {
            'address': '192.0.2.73', 'port': 33502, 'service_type': 'modbus',
            'title': 'Fixture Controller', 'metadata': {'product': 'Acme PLC',
                'collection_scope': {'network': '192.0.2.0/24', 'country': 'US'}}})
        self.reference = {'type': 'endpoint', 'id': str(self.endpoint.pk)}

    def call(self, name, body=None, **kwargs):
        request = self.factory.get('/') if body is None else self.factory.post('/', json.dumps(body), content_type='application/json')
        response = getattr(self.api, name)(request, **kwargs)
        return response.status_code, json.loads(response.content)

    def preview(self, **extra):
        status, data = self.call('preview', {'references': [self.reference], **extra})
        self.assertEqual(status, 200, data)
        return data

    def save_body(self, preview=None, **extra):
        preview = preview or self.preview()
        return {'references': [self.reference], 'selected_fields': preview['selected_fields'],
                'scope': preview['scope'], 'mode': 'all', 'name': 'Acme device', 'description': 'Saved fixture', **extra}

    def test_preview_keeps_all_fields_and_defaults_to_identity_without_network_constraints(self):
        data = self.preview()
        selected = {field['field'] for field in data['fields'] if field['id'] in data['selected_fields']}
        self.assertIn('product', selected)
        self.assertFalse(selected & {'ip', 'port', 'host', 'asn', 'country'})
        self.assertTrue({'ip', 'port'} <= {field['field'] for field in data['fields']})
        self.assertEqual(data['service_type'], 'modbus')
        self.assertEqual(data['scope']['network'], '192.0.2.0/24')
        self.assertNotIn('port:', data['queries']['shodan']['query'])
        self.assertIn('192.0.2.0/24', data['queries']['shodan']['query'])
        one = [data['selected_fields'][0]]
        selected_data = self.preview(selected_fields=one)
        self.assertEqual(selected_data['selected_fields'], one)
        self.assertEqual(len(selected_data['fields']), len(data['fields']))

    def test_save_is_durable_and_duplicate_names_do_not_duplicate_a_rule(self):
        body = self.save_body()
        with patch('leakscope.phase3.jobs.queue_lookup', side_effect=AssertionError('No search')), patch('socket.create_connection', side_effect=AssertionError('No device calls')):
            status, data = self.call('presets', body)
        self.assertEqual(status, 201, data)
        self.assertTrue(data['created'])
        preset = data['preset']
        self.assertEqual(preset['key'], 'preset:' + preset['id'])
        self.assertEqual(preset['service_type'], 'modbus')
        self.assertEqual(preset['references'][0]['id'], str(self.endpoint.pk))
        self.assertEqual(Run.objects.count(), 0)
        status, duplicate = self.call('presets', dict(body, name='Another display name'))
        self.assertEqual(status, 200, duplicate)
        self.assertFalse(duplicate['created'])
        self.assertEqual(duplicate['preset']['id'], preset['id'])
        self.assertEqual(self.call('presets')[1]['results'][0]['id'], preset['id'])

    def test_forged_selection_queries_and_hidden_sources_cannot_create_presets(self):
        body = self.save_body()
        for extra in [{'selected_fields': ['forged']}, {'queries': {'shodan': {'query': 'ip:1.1.1.1'}}},
                      {'signals': [{'field': 'product', 'value': 'forged'}]}, {'selected_fields': []}]:
            self.assertEqual(self.call('presets', dict(body, **extra))[0], 400)
        self.endpoint.observations.update(hidden=True)
        self.assertEqual(self.call('presets', body)[0], 404)

    def test_scope_can_only_be_removed_by_explicit_edit(self):
        body = self.save_body(scope={})
        self.assertEqual(self.call('presets', body)[0], 400)
        status, result = self.call('presets', dict(body, scope_override=True))
        self.assertEqual(status, 201, result)
        self.assertEqual(result['preset']['scope'], {})
        self.assertNotIn('192.0.2.0/24', result['preset']['queries']['shodan']['query'])

    def test_conflicting_scopes_can_open_preview_but_need_resolution_before_save(self):
        second = ingest_observation('fixture', {'address': '192.0.2.74', 'port': 33502, 'service_type': 'modbus',
            'title': 'Fixture Controller', 'metadata': {'product': 'Acme PLC', 'collection_scope': {'network': '198.51.100.0/24'}}})
        refs = [self.reference, {'type': 'endpoint', 'id': str(second.pk)}]
        data = self.preview(references=refs)
        self.assertIn('network', data['scope_conflicts'])
        self.assertFalse(any(query['supported'] for query in data['queries'].values()))
        body = self.save_body(data, references=refs)
        self.assertEqual(self.call('presets', body)[0], 400)
        status, result = self.call('presets', dict(body, scope={'network': '192.0.2.0/24', 'country': '', 'exclude': ''}, scope_override=True))
        self.assertEqual(status, 201, result)

    def test_save_requires_an_explicit_reviewed_selection_and_at_least_one_supported_query(self):
        body = self.save_body()
        body.pop('selected_fields')
        self.assertEqual(self.call('presets', body)[0], 400)
        with patch('leakscope_app.presets.compile_queries', return_value={p: {'supported': False, 'query': '', 'warnings': ['Unsupported']} for p in ['shodan', 'zoomeye', 'netlas']}):
            self.assertEqual(self.call('presets', self.save_body())[0], 400)

    def test_delete_uses_uuid_and_removes_only_the_selected_user_preset(self):
        status, data = self.call('presets', self.save_body())
        self.assertEqual(status, 201, data)
        self.assertEqual(self.call('delete', {}, preset_id='not-a-uuid')[0], 400)
        status, deleted = self.call('delete', {}, preset_id=data['preset']['id'])
        self.assertEqual(status, 200, deleted)
        self.assertEqual(self.call('presets')[1]['results'], [])
        self.assertEqual(self.call('delete', {}, preset_id=data['preset']['id'])[0], 404)

    def test_default_does_not_select_a_synthetic_service_classification(self):
        self.endpoint.observations.update(data={'title': 'Fixture Controller', 'service_type': 'modbus', 'metadata': {}})
        data = self.preview()
        selected = [f for f in data['fields'] if f['id'] in data['selected_fields']]
        self.assertFalse(any(f['label'] == 'Service classification' for f in selected))

    def test_methods_and_name_validation(self):
        self.assertEqual(self.call('preview')[0], 405)
        self.assertEqual(self.call('delete', preset_id=str(self.endpoint.pk))[0], 405)
        for name in ['', 'a' * 256, 'bad\x00name']:
            self.assertEqual(self.call('presets', self.save_body(name=name))[0], 400)

    def test_snapshot_provenance_is_retained_and_saved_rule_survives_later_source_hiding(self):
        preview = self.preview()
        observed = next(field for field in preview['fields'] if field['field'] == 'product' and field.get('provenance'))
        status, result = self.call('presets', self.save_body(preview, selected_fields=[observed['id']]))
        self.assertEqual(status, 201, result)
        self.assertEqual(result['preset']['signals'][0]['provenance'], observed['provenance'])
        self.endpoint.observations.update(hidden=True)
        listed = self.call('presets')[1]['results']
        self.assertEqual(listed[0]['id'], result['preset']['id'])
        self.assertEqual(self.call('preview', {'references': [self.reference]})[0], 404)

    def test_preset_references_must_have_a_single_underlying_service_type(self):
        second = ingest_observation('fixture', {'address': '192.0.2.78', 'port': 47808,
            'transport': 'udp', 'service_type': 'bacnet', 'title': 'Fixture BACnet'})
        refs = [self.reference, {'type': 'endpoint', 'id': str(second.pk)}]
        self.assertEqual(self.call('preview', {'references': refs})[0], 400)

    def test_names_descriptions_and_signal_values_are_redacted(self):
        with patch('leakscope_app.presets._config', return_value={'SHODAN_API_KEY': 'fixture-private-key'}):
            status, data = self.call('presets', self.save_body(name='Fixture fixture-private-key', description='Uses fixture-private-key'))
        self.assertEqual(status, 201, data)
        self.assertNotIn('fixture-private-key', json.dumps(data))

    def test_real_routes_require_csrf_for_mutation(self):
        from django.test import Client, override_settings
        from django.urls import path
        routes = type('PresetTestRoutes', (), {'urlpatterns': [path('presets', self.api.presets), path('preview', self.api.preview)]})
        with override_settings(ROOT_URLCONF=routes):
            client = Client(enforce_csrf_checks=True)
            self.assertEqual(client.get('/presets').status_code, 200)
            self.assertEqual(client.get('/preview').status_code, 405)
            self.assertEqual(client.post('/preview', json.dumps({'references': [self.reference]}), content_type='application/json').status_code, 403)
            self.assertEqual(client.post('/presets', json.dumps(self.save_body()), content_type='application/json').status_code, 403)

    def test_save_rejects_an_evidence_selection_that_changed_after_preview(self):
        preview = self.preview()
        observed = next(field for field in preview['fields'] if field['field'] == 'product' and field.get('provenance'))
        body = self.save_body(preview, selected_fields=[observed['id']])
        observation = self.endpoint.observations.first()
        changed = dict(observation.data)
        changed['metadata'] = dict(changed['metadata'], product='Different controller')
        observation.data = changed
        observation.save(update_fields=['data'])
        self.assertEqual(self.call('presets', body)[0], 400)

    def test_oversized_provider_queries_are_blocked_without_truncating_predicates(self):
        with patch('leakscope_app.presets.compile_queries', return_value={p: {'supported': True, 'query': 'x' * 8001, 'url': '', 'warnings': []} for p in ['shodan', 'zoomeye', 'netlas']}):
            data = self.preview()
            self.assertFalse(any(query['supported'] for query in data['queries'].values()))
            self.assertTrue(all(not query['query'] for query in data['queries'].values()))
            self.assertEqual(self.call('presets', self.save_body(data))[0], 400)

    def test_long_encoded_provider_links_preserve_the_complete_reviewed_query(self):
        from urllib.parse import parse_qs, urlsplit
        observation = self.endpoint.observations.first()
        updated = dict(observation.data)
        updated['metadata'] = dict(updated['metadata'], product='🔥' * 950)
        observation.data = updated
        observation.save(update_fields=['data'])
        preview = self.preview()
        field = next(field for field in preview['fields'] if field['field'] == 'product' and field.get('provenance'))
        data = self.preview(selected_fields=[field['id']])
        query = data['queries']['shodan']
        self.assertTrue(query['supported'])
        self.assertEqual(parse_qs(urlsplit(query['url']).query)['query'][0], query['query'])

    def test_native_identity_defaults_preserve_provider_queries_and_exclude_revision(self):
        ingest_observation('fetch:ot', {'address': self.endpoint.address, 'port': self.endpoint.port,
            'service_type': 'modbus', 'metadata': {'device': {'objects': [
                {'id': 0, 'value': 'Delta Electronics, Inc.'}, {'id': 1, 'value': 'AS228T-A'}, {'id': 2, 'value': 'V1.08.20'}]}}})
        data = self.preview()
        selected = {field['field'] for field in data['fields'] if field['id'] in data['selected_fields']}
        self.assertEqual(selected, {'modbus.vendor', 'modbus.product'})
        for provider, query in data['queries'].items():
            self.assertTrue(query['supported'], (provider, query))
            self.assertIn('AS228T-A', query['query'])
            self.assertNotIn('port:', query['query'])
            self.assertNotIn('port=', query['query'])
        status, saved = self.call('presets', self.save_body(data))
        self.assertEqual(status, 201, saved)
        self.assertEqual({field['provenance']['provider'] for field in saved['preset']['signals']}, {'fetch:ot'})

    def test_resolved_scope_conflicts_clear_the_preview_blocker(self):
        other = ingest_observation('fixture', {'address': '192.0.2.77', 'port': 33502, 'service_type': 'modbus',
            'title': 'Fixture controller', 'metadata': {'product': 'Acme PLC', 'collection_scope': {'network': '198.51.100.0/24'}}})
        references = [self.reference, {'type': 'endpoint', 'id': str(other.pk)}]
        initial = self.preview(references=references)
        self.assertTrue(initial['scope_conflicts'])
        resolved = self.preview(references=references, selected_fields=initial['selected_fields'],
            scope={'network': '192.0.2.0/24', 'country': 'US', 'exclude': ''}, scope_override=True)
        self.assertEqual(resolved['scope_conflicts'], [])
        self.assertTrue(any(query['supported'] for query in resolved['queries'].values()))

    def test_result_without_safe_defaults_can_open_field_picker(self):
        other = ingest_observation('fixture', {'address': '192.0.2.79', 'port': 33502, 'service_type': 'modbus', 'metadata': {}})
        references = [{'type': 'endpoint', 'id': str(other.pk)}]
        initial = self.preview(references=references)
        self.assertTrue(initial['fields'])
        self.assertEqual(initial['selected_fields'], [])
        self.assertFalse(any(query['supported'] for query in initial['queries'].values()))
        self.assertTrue(initial['warnings'])
        self.assertEqual(self.call('preview', {'references': references, 'selected_fields': []})[0], 400)
        body = self.save_body(initial, references=references)
        self.assertEqual(self.call('presets', body)[0], 400)
