import json
import time
from unittest.mock import patch
from django.test import TestCase, Client
from leakscope.phase3 import netlas_dns_history as history
from leakscope.phase3 import domain_expansion as engine
from leakscope.phase3 import relationships as store
from leakscope_app.models import DomainCandidate, DomainSourceCache


class NetlasDNSHistoryTests(TestCase):
    config = {'NETLAS_API_KEY': 'fixture-secret'}

    def setUp(self):
        self.used = 0
        self.limit = 10
        self.context = {'netlas_indices': ['2024-01-01', '2023-01-01'], 'deadline': time.monotonic()+50}

    def spend(self, bucket, units=1):
        self.assertEqual(bucket, 'netlas')
        if self.used+units > self.limit: return False
        self.used += units
        return True

    def collect(self):
        return history.collect('netlas_dns', {'kind': 'domain', 'value': 'example.com'},
            config=self.config, spend=self.spend, context=self.context)

    @patch.object(history, '_request')
    def test_exact_scope_date_labels_and_source_timestamps(self, request):
        request.return_value = {'items': [{'data': {'domain': 'api.example.com', 'a': ['192.0.2.2'],
            'mx': ['mail.example.com'], '@timestamp': '2024-01-05T00:00:00Z'}},
            {'data': {'domain': 'example.com.evil.test'}}]}
        result = self.collect()
        params = request.call_args.kwargs['params']
        self.assertEqual(params['q'], '(domain:example.com OR domain:*.example.com)')
        self.assertEqual(params['indices'], '2024-01-01')
        self.assertEqual(params['fields'], 'domain,a,aaaa,cname,mx,ns,txt,@timestamp,last_updated')
        self.assertEqual(request.call_args.args[:2], ('netlas', '/domains/'))
        self.assertEqual(len(result['candidates']), 1)
        proof = result['candidates'][0]['evidence'][0]
        self.assertEqual(proof['observed_at'], '2024-01-05T00:00:00+00:00')
        self.assertEqual(proof['dns_records']['A'], ['192.0.2.2'])
        self.assertTrue(proof['historical'])
        self.assertNotIn('first_seen', proof)
        self.assertEqual(result['continuation'], {'snapshot': 1, 'start': 0})

    @patch.object(history, '_request')
    def test_paging_and_budget_pause_resume_without_repeating_page(self, request):
        request.return_value = {'items': [{'data': {'domain': f'h{i}.example.com'}} for i in range(20)]}
        first = self.collect()
        self.assertEqual(first['continuation'], {'snapshot': 0, 'start': 20})
        self.context['cursor'] = first['continuation']; self.limit = 1
        paused = self.collect()
        self.assertEqual(paused['blocked'], 'budget')
        self.assertEqual(paused['continuation'], first['continuation'])
        self.assertEqual(request.call_count, 1)
        self.limit = 2; request.return_value = {'items': []}
        resumed = self.collect()
        self.assertEqual(request.call_args.kwargs['params']['start'], 20)
        self.assertEqual(resumed['continuation'], {'snapshot': 1, 'start': 0})

    @patch.object(history, '_request')
    def test_demo_one_page_unknown_time_not_collection_time(self, request):
        request.return_value = {'items': [{'data': {'domain': 'api.example.com'}}, {'data': {'domain': 'www.example.com'}}]}
        self.context['demo'] = True
        result = self.collect()
        self.assertEqual(self.used, 1)
        self.assertEqual(len(result['candidates']), 1)
        self.assertIsNone(result['candidates'][0]['evidence'][0]['observed_at'])
        self.assertNotIn('continuation', result)
        self.assertTrue(result['notes'])

    @patch.object(history, '_request', side_effect=ValueError('Netlas returned HTTP 403; check permissions and account limits'))
    def test_denied_snapshot_is_partial_not_negative_evidence(self, request):
        result = self.collect()
        self.assertTrue(result['failed']); self.assertFalse(result['cacheable'])
        self.assertIn('403', ' '.join(result['notes']))
        self.assertNotIn('continuation', result)

    @patch.object(history, '_request')
    def test_deadline_does_not_spend(self, request):
        self.context['deadline'] = time.monotonic()-1
        result = self.collect()
        self.assertEqual(result['blocked'], 'deadline')
        self.assertEqual(self.used, 0); request.assert_not_called()

    def test_source_requires_opt_in_budget_and_valid_date_labels(self):
        spec = engine.normalize_spec({'domains': ['example.com'], 'sources': ['netlas_dns'],
            'netlas_indices': ['2024-01-01'], 'budgets': {'netlas': 8}, 'demo': True})
        self.assertEqual(spec['netlas_indices'], ['2024-01-01'])
        self.assertEqual(spec['budgets']['netlas'], 1)
        for extra in ({'budgets': {'netlas': 0}}, {'netlas_indices': ['1234']},
                      {'netlas_indices': ['2024-99-01']}, {'netlas_indices': '2024-01-01'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                engine.normalize_spec({'domains': ['example.com'], 'sources': ['netlas_dns'],
                    'budgets': {'netlas': 1}, **extra})

    @patch('leakscope.phase3.domain_expansion.run_expansion.apply_async')
    def test_history_is_saved_even_for_seeds_and_already_scoped_domains(self, task):
        org=store.node('organization','Example')
        root=store.node('domain','example.com')
        store.relation(org,root,'user_scope',store.evidence(org,'user_scope',{'domain':'example.com'}))
        with self.captureOnCommitCallbacks(execute=True):
            exp=engine.start({'organization_id':str(org.pk),'domains':['example.com'],'sources':['netlas_dns'],'budgets':{'netlas':1}})
        exp.state='running';exp.save()
        candidate=engine.save_candidate(exp.pk,{'domain':'example.com','relation':'historical','evidence':[
            {'source':'netlas_dns','signal':'historical_dns_record','index_label':'2024-01-01','historical':True,'dns_records':{'A':['192.0.2.1']}}]},
            {'kind':'domain','value':'example.com','depth':0})
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.evidence[0]['dns_records']['A'],['192.0.2.1'])

    @patch.object(history, '_request')
    def test_metadata_is_explicit_cached_and_dns_only(self, request):
        request.return_value = [{'id': 7, 'label': '2024-01-01', 'type': 'domains'},
                                {'id': 8, 'label': '2024-02-01', 'type': 'responses'}]
        path = '/api/assets/expansions/netlas-indices'
        self.assertEqual(self.client.get(path).json()['indices'], [])
        request.assert_not_called()
        with patch('leakscope_app.domain_expansion.get_config', return_value={'config': self.config}):
            response = self.client.post(path, '{}', content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([x['label'] for x in response.json()['indices']], ['2024-01-01'])
        self.assertTrue(request.call_args.kwargs['allow_list'])
        self.assertEqual(self.client.get(path).json()['requests'], 0)
        self.assertEqual(Client(enforce_csrf_checks=True).post(path, '{}', content_type='application/json').status_code, 403)

    @patch.object(history, '_request')
    @patch('leakscope.phase3.domain_expansion.run_expansion.apply_async')
    def test_worker_saves_each_snapshot_in_candidate_evidence_and_read_is_free(self, task, request):
        request.side_effect = [
            {'items': [{'data': {'domain': 'api.example.com', 'a': ['192.0.2.2'], '@timestamp': '2024-01-01T00:00:00Z'}}]},
            {'items': [{'data': {'domain': 'api.example.com', 'a': ['192.0.2.3'], '@timestamp': '2023-01-01T00:00:00Z'}}]},
        ]
        with patch('leakscope.phase3.domain_expansion.get_config', return_value={'config': self.config}), self.captureOnCommitCallbacks(execute=True):
            exp = engine.start({'domains': ['example.com'], 'sources': ['netlas_dns'], 'netlas_indices': self.context['netlas_indices'],
                'budgets': {'netlas': 2}, 'max_depth': 0})
        with patch('leakscope.phase3.expansion_worker.get_config', return_value={'config': self.config}):
            engine.run_expansion(str(exp.pk))
        candidate = DomainCandidate.objects.get(expansion=exp)
        self.assertEqual({p['index_label'] for p in candidate.evidence}, {'2024-01-01', '2023-01-01'})
        response = self.client.get('/api/assets/expansions/'+str(exp.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['expansion']['usage']['netlas'], 2)
        self.assertEqual(response.json()['expansion']['netlas_indices'], self.context['netlas_indices'])
        self.assertEqual(request.call_count, 2)
