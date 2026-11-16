import json
from unittest.mock import patch

from django.test import Client, TestCase

from leakscope_app.models import Elastic, FindingReview, Search


class IntelligenceAPITests(TestCase):
    @patch('leakscope_app.intelligence._config')
    def test_self_hosted_firecrawl_settings_distinguish_endpoint_from_key(self, config):
        config.return_value = {'FIRECRAWL_API_URL': 'http://firecrawl:3002/v2', 'FIRECRAWL_API_KEY': ''}
        value = self.client.get('/api/intelligence/settings').json()['firecrawl']
        self.assertTrue(value['configured'])
        self.assertFalse(value['key_configured'])
        self.assertEqual(value['base_url'], 'http://firecrawl:3002/v2')

    def setUp(self):
        search = Search.objects.create(type='elastic', keyword='fixture', network='', country='')
        self.finding = Elastic.objects.create(search=search, ip='192.0.2.12', port='9200', name='Fixture "archive"')
        self.ref = {'type': 'elastic', 'id': self.finding.pk}

    def post(self, path, body):
        return self.client.post('/api/intelligence/' + path, json.dumps(body), content_type='application/json')

    def test_context_reads_server_values_and_rejects_forged_selection(self):
        r = self.post('context', {'references': [self.ref]})
        self.assertEqual(r.status_code, 200)
        fields = r.json()['fields']
        title = next(f for f in fields if f['field'] == 'title')
        self.assertEqual(title['value'], self.finding.name)
        self.assertEqual(self.post('similar', {'references': [self.ref], 'selected_fields': ['forged']} ).status_code, 400)

    def test_hidden_and_unknown_references_are_not_context(self):
        FindingReview.objects.create(type='elastic', object_id=self.finding.pk, hidden=True)
        self.assertEqual(self.post('context', {'references': [self.ref]}).status_code, 404)
        self.assertEqual(self.post('context', {'references': [{'type': 'bogus', 'id': 1}]}).status_code, 400)

    def test_intelligence_requires_post_and_csrf(self):
        self.assertEqual(self.client.get('/api/intelligence/similar').status_code, 405)
        client = Client(enforce_csrf_checks=True)
        r = client.post('/api/intelligence/context', json.dumps({'references': [self.ref]}), content_type='application/json')
        self.assertEqual(r.status_code, 403)

    @patch('leakscope_app.intelligence._config')
    def test_settings_exposes_flags_not_keys_or_url_credentials(self, config):
        config.return_value = {'LLM_API_BASE': 'https://person:private@example.invalid/v1?token=private', 'LLM_MODEL': 'fixture', 'LLM_API_KEY': 'private', 'FIRECRAWL_API_KEY': 'another-private'}
        r = self.client.get('/api/intelligence/settings')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('private', r.content.decode())
        self.assertEqual(r.json()['llm']['model'], 'fixture')

    @patch('leakscope_app.intelligence.compile_queries')
    def test_similar_persists_draft_without_provider_execution(self, compile):
        compile.return_value = {'shodan': {'query': 'http.title:"fixture"', 'supported': True, 'warnings': [], 'url': ''}}
        context = self.post('context', {'references': [self.ref]}).json()
        field = next(f['id'] for f in context['fields'] if f['field'] == 'title')
        r = self.post('similar', {'references': [self.ref], 'selected_fields': [field], 'mode': 'all', 'scope': {'network': '192.0.2.0/24'}})
        self.assertEqual(r.status_code, 200)
        item = r.json()['suggestion']
        self.assertEqual(item['status'], 'draft')
        self.assertEqual(compile.call_args.kwargs['scope']['network'], '192.0.2.0/24')
        self.assertEqual(self.client.get('/api/intelligence/suggestions').json()['count'], 1)
        r = self.post(f"suggestions/{item['id']}/review", {'status': 'adopted'})
        self.assertEqual(r.json()['suggestion']['status'], 'adopted')

    @patch('leakscope_app.intelligence._config')
    @patch('leakscope_app.intelligence.ask_endpoint')
    def test_ask_sends_selected_bounded_evidence_without_configured_secrets(self, ask, config):
        config.return_value = {'LLM_API_BASE': 'http://example.invalid/v1', 'LLM_MODEL': 'fixture', 'LLM_API_KEY': 'fixture-secret'}
        self.finding.name = 'fixture-secret'; self.finding.save()
        ask.return_value = {'answer': 'fixture-secret response', 'model': 'fixture', 'usage': {}}
        r = self.post('ask', {'references': [self.ref], 'question': 'Explain this'})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('fixture-secret', json.dumps(ask.call_args.args[1]))
        self.assertNotIn('fixture-secret', r.content.decode())

    @patch('leakscope_app.intelligence.ask_endpoint')
    def test_ask_can_omit_findings_but_rejects_empty_question(self, ask):
        ask.return_value = {'answer': 'An answer', 'model': 'fixture', 'usage': {}}
        self.assertEqual(self.post('ask', {'question': 'Explain database signatures'}).status_code, 200)
        self.assertEqual(self.post('ask', {'question': ''}).status_code, 400)

    @patch('leakscope_app.intelligence.research_queries')
    @patch('leakscope_app.intelligence.compile_queries')
    def test_research_preserves_sources_and_ask_receives_saved_suggestion(self, compile, research):
        compile.return_value = {'shodan': {'query': 'fixture', 'supported': True, 'warnings': [], 'url': ''}}
        research.return_value = {'sources': [{'id': 'S1', 'url': 'https://example.invalid/docs', 'title': 'Fixture docs', 'excerpt': 'Fixture Server'}], 'suggestions': [{'title': 'Fixture', 'technology': 'Fixture', 'rationale': 'Source S1', 'signals': [{'field': 'title', 'value': 'Fixture Server', 'source_ids': ['S1']}]}], 'warnings': []}
        r = self.post('research', {'topic': 'Fixture storage'})
        self.assertEqual(r.status_code, 200)
        item = r.json()['suggestions'][0]
        self.assertEqual(item['sources'][0]['id'], 'S1')
        with patch('leakscope_app.intelligence.ask_endpoint', return_value={'answer': 'Evidence-backed response', 'model': 'fixture', 'usage': {}}) as ask:
            r = self.post('ask', {'question': 'Explain', 'suggestion_id': item['id']})
            self.assertEqual(r.status_code, 200)
            self.assertIn('Fixture docs', json.dumps(ask.call_args.args[1]))

    def test_limits_references_and_invalid_review_status(self):
        self.assertEqual(self.post('context', {'references': [self.ref] * 11}).status_code, 400)
        self.assertEqual(self.post('ask', {'question': 'x' * 4001}).status_code, 400)
        self.assertEqual(self.post('research', {'topic': 'x' * 301}).status_code, 400)

    @patch('leakscope_app.intelligence.compile_queries')
    def test_origin_scope_preserved_unless_user_explicitly_changes_it(self, compile):
        source = self.finding.search
        source.network = '192.0.2.0/24'; source.country = 'US'; source.save()
        compile.return_value = {}
        context = self.post('context', {'references': [self.ref]}).json()
        self.assertEqual(context['scope']['network'], source.network)
        field = next(f['id'] for f in context['fields'] if f['field'] == 'title')
        body = {'references': [self.ref], 'selected_fields': [field]}
        self.assertEqual(self.post('similar', body).status_code, 200)
        self.assertEqual(compile.call_args.kwargs['scope']['network'], source.network)
        body['scope'] = {'network': '', 'country': ''}
        self.assertEqual(self.post('similar', body).status_code, 400)
        body['scope_override'] = True
        self.assertEqual(self.post('similar', body).status_code, 200)

    @patch('leakscope_app.intelligence.research_queries')
    @patch('leakscope_app.intelligence.compile_queries')
    def test_repeated_research_preserves_adoption_without_duplicate_suggestions(self, compile, research):
        compile.return_value = {}
        research.return_value = {'sources': [{'id': 'S1', 'url': 'https://example.invalid/docs'}], 'suggestions': [{'title': 'Fixture', 'technology': 'Fixture', 'rationale': 'Source S1', 'signals': [{'field': 'title', 'value': 'Fixture', 'source_ids': ['S1']}]}], 'warnings': []}
        first = self.post('research', {'topic': 'Fixture'}).json()['suggestions'][0]
        self.post(f"suggestions/{first['id']}/review", {'status': 'adopted'})
        second = self.post('research', {'topic': 'Fixture'}).json()['suggestions'][0]
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(second['status'], 'adopted')

    @patch('leakscope_app.intelligence.research_queries')
    @patch('leakscope_app.intelligence.compile_queries')
    def test_deduplication_does_not_reassign_stored_citation_ids(self, compile, research):
        compile.return_value = {}
        sources = [{'id': 'S1', 'url': 'https://a.invalid/docs', 'excerpt': 'Fixture'}, {'id': 'S2', 'url': 'https://b.invalid/docs', 'excerpt': 'Other'}]
        candidate = {'title': 'Fixture', 'technology': 'Fixture', 'rationale': 'S1 documents it', 'signals': [{'field': 'title', 'value': 'Fixture', 'source_ids': ['S1']}]}
        research.return_value = {'sources': sources, 'suggestions': [candidate], 'warnings': []}
        first = self.post('research', {'topic': 'Fixture'}).json()['suggestions'][0]
        research.return_value = {'sources': [dict(sources[1], id='S1'), dict(sources[0], id='S2')], 'suggestions': [dict(candidate, signals=[{'field': 'title', 'value': 'Fixture', 'source_ids': ['S2']}])], 'warnings': []}
        second = self.post('research', {'topic': 'Fixture'}).json()['suggestions'][0]
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['sources'], second['sources'])

    @patch('leakscope_app.intelligence.compile_queries')
    def test_manual_query_edits_return_draft_and_correct_provider_link(self, compile):
        compile.return_value = {'shodan': {'query': 'old', 'supported': True, 'warnings': [], 'url': ''}}
        first = self.post('similar', {'references': [self.ref]}).json()['suggestion']
        edited = self.post(f"suggestions/{first['id']}/queries", {'provider': 'shodan', 'query': 'port:443'}).json()['suggestion']
        self.assertEqual(edited['queries']['shodan']['query'], 'port:443')
        self.assertIn('query=port%3A443', edited['queries']['shodan']['url'])
        self.assertIn('unvalidated', ' '.join(edited['queries']['shodan']['warnings']).lower())

    def test_normalized_observations_support_uuid_evidence_references(self):
        from leakscope.phase3.state import ingest_observation
        item = ingest_observation('netlas', {'address': '192.0.2.8', 'port': 443, 'service_type': 'https', 'title': 'Fixture web'})
        r = self.post('context', {'references': [{'type': 'endpoint', 'id': str(item.pk)}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['references'][0]['id'], str(item.pk))

    def test_observed_fingerprints_and_headers_are_selectable_with_provenance(self):
        from leakscope.phase3.state import ingest_observation
        item = ingest_observation('netlas', {'address': '192.0.2.8', 'port': 443, 'service_type': 'https',
            'metadata': {'http': {'headers': {'server': 'FixtureHTTP'}, 'favicon': {'hash_sha256': 'a' * 64}},
                         'certificate': {'fingerprint_sha256': 'b' * 64}}})
        body = {'references': [{'type': 'endpoint', 'id': str(item.pk)}]}
        r = self.post('context', body)
        self.assertEqual(r.status_code, 200)
        fields = r.json()['fields']
        self.assertIn('server', [x['field'] for x in fields])
        self.assertIn('favicon.sha256', [x['field'] for x in fields])
        self.assertIn('certificate.sha256', [x['field'] for x in fields])
        field = next(x for x in fields if x['field'] == 'favicon.sha256')
        self.assertEqual(field['provenance']['provider'], 'netlas')
        with patch('leakscope_app.intelligence.compile_queries', return_value={}):
            response = self.post('similar', dict(body, selected_fields=[field['id']]))
            self.assertEqual(response.status_code, 200, response.content)
