"""Durable enumeration regression tests. All source responses are offline fixtures."""
import json
from unittest.mock import patch
from celery.exceptions import Ignore
from django.test import TestCase
from leakscope_app.models import DomainExpansion, DomainCandidate
from leakscope.phase3 import domain_expansion as engine


def candidate(name):
    return {'domain':name, 'evidence':[{'source':'fixture','signal':'passive_dns_record','strength':25}]}


class ExpansionEngineV2Tests(TestCase):
    def start(self, **options):
        data={'domains':['example.com'],'sources':['saved'],'max_depth':0,**options}
        with patch.object(engine.run_expansion,'apply_async'), self.captureOnCommitCallbacks(execute=True):
            return engine.start(data)

    def execute(self, exp, collect):
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=collect), patch.object(engine.run_expansion,'apply_async'):
            engine.run_expansion(str(exp.pk))
        exp.refresh_from_db()

    def test_defaults_separate_storage_from_network_spending(self):
        spec=engine.normalize_spec({'domains':['example.com']})
        self.assertEqual(spec['max_candidates'],0)
        self.assertGreaterEqual(engine.normalize_spec({'domains':['example.com'],'sources':['dns']})['budgets']['dns'],1000)
        self.assertEqual(spec['budgets']['netlas'],0)
        self.assertGreaterEqual(spec['runtime_seconds'],1800)
        self.assertIn('passive_dns',spec['sources'])

    def test_ip_network_and_asn_seeds_are_normalized(self):
        spec=engine.normalize_spec({'ips':['192.0.2.1'],'networks':['198.51.100.8/24'],'asns':['AS64500']})
        self.assertEqual({(x['kind'],x['value']) for x in spec['seeds']},{('ip','192.0.2.1'),('network','198.51.100.0/24'),('asn','64500')})
        for data in [{'ips':['not an IP']},{'asns':['AS0']},{'networks':['bogus']},{'domains':['example.com'],'dns_rate':0}]:
            with self.assertRaises(ValueError):engine.normalize_spec(data)

    def test_over_500_returned_names_are_saved_and_later_sources_run(self):
        exp=self.start(sources=['saved','web'],budgets={'web':1})
        calls=[]
        def collect(source,*args,**kwargs):
            calls.append(source)
            return {'candidates':[candidate(f'host{i}.example.com') for i in range(610)]} if source=='saved' else {'candidates':[candidate('other.net')]}
        self.execute(exp,collect)
        self.assertEqual(exp.candidates.count(),611)
        self.assertEqual(calls,['saved','web'])
        self.assertEqual(exp.state,'succeeded')
        data=engine.detail(exp,{'page':'2','page_size':'100','category':'all'})
        self.assertEqual(len(data['candidates']),100)
        self.assertEqual(data['counts']['total'],611)
        self.assertEqual(data['pages'],7)

    def test_storage_pause_retains_page_and_resume_does_not_repeat_collection(self):
        exp=self.start(max_candidates=2)
        collect=lambda *a,**kw:{'candidates':[candidate(f'host{i}.example.com') for i in range(4)]}
        self.execute(exp,collect)
        self.assertEqual(exp.state,'paused')
        self.assertEqual(exp.candidates.count(),2)
        self.assertTrue(engine.serialize(exp)['resumable'])
        with patch.object(engine.run_expansion,'apply_async'),self.captureOnCommitCallbacks(execute=True):engine.resume(exp.pk,{'max_candidates':0})
        self.execute(exp,AssertionError('persisted page must not be recollected'))
        self.assertEqual(exp.candidates.count(),4)
        self.assertEqual(exp.state,'succeeded')

    def test_source_continuation_collects_all_pages(self):
        exp=self.start(sources=['web'],budgets={'web':3})
        cursors=[]
        def collect(source,seed,*,context,spend,**kwargs):
            cursor=context.get('cursor') or {};cursors.append(cursor)
            spend('web')
            return {'candidates':[candidate('a.example.com' if not cursor else 'b.example.com')],**({'continuation':{'after':'page1'}} if not cursor else {})}
        self.execute(exp,collect)
        self.assertEqual(cursors,[{}, {'after':'page1'}])
        self.assertEqual(exp.candidates.count(),2)
        self.assertEqual(exp.usage['web'],2)
        with self.assertRaises(Ignore):engine.run_expansion(str(exp.pk))

    def test_exhausted_source_preserves_other_sources_and_can_resume(self):
        exp=self.start(sources=['web','dns'],budgets={'web':0,'dns':2})
        def collect(source,seed,*,spend,context,**kwargs):
            bucket='web' if source=='web' else 'dns'
            if not spend(bucket):return {'candidates':[], 'blocked':'budget'}
            return {'candidates':[candidate('web.example.com' if source=='web' else 'dns.example.com')]}
        self.execute(exp,collect)
        self.assertEqual(exp.state,'paused')
        self.assertTrue(exp.candidates.filter(domain='dns.example.com').exists())
        with patch.object(engine.run_expansion,'apply_async'),self.captureOnCommitCallbacks(execute=True):engine.resume(exp.pk,{'budgets':{'web':1}})
        self.execute(exp,collect)
        self.assertEqual(exp.state,'succeeded')
        self.assertEqual(exp.candidates.count(),2)
        self.assertEqual(exp.usage['dns'],1)
        self.assertEqual(exp.usage['web'],1)

    def test_paginated_filters_apply_to_all_saved_candidates(self):
        exp=self.start()
        exp.state='running';exp.save()
        for i in range(8):engine.save_candidate(exp.pk,candidate(f'host{i}.example.com'),exp.spec['seeds'][0])
        response=self.client.get('/api/assets/expansions/'+str(exp.pk),{'page':2,'page_size':3,'category':'domains','q':'host'})
        self.assertEqual(response.status_code,200,response.content)
        body=response.json()
        self.assertEqual(body['count'],8)
        self.assertEqual([c['domain'] for c in body['candidates']],['host3.example.com','host4.example.com','host5.example.com'])
        self.assertEqual(body['counts']['total'],8)
        self.assertEqual(self.client.get('/api/assets/expansions/'+str(exp.pk),{'page_size':'bogus'}).status_code,400)

    def test_demo_remains_one_candidate_and_no_recursive_expansion(self):
        exp=self.start(demo=True,max_candidates=0,max_depth=8)
        self.execute(exp,lambda *a,**k:{'candidates':[candidate('a.example.com'),candidate('b.example.com')],'continuation':{'after':'more'}})
        self.assertEqual(exp.candidates.count(),1)
        self.assertEqual(exp.state,'succeeded')
        self.assertEqual(exp.spec['max_depth'],0)
        self.assertFalse(engine.serialize(exp)['resumable'])

    def test_cancel_resume_fences_old_in_flight_worker_and_its_spending(self):
        exp=self.start(sources=['web','dns'],budgets={'web':2,'dns':2})
        stale_spend=[]
        def collect(source,seed,*,spend,**kwargs):
            if source=='dns':
                self.assertTrue(spend('dns'));return {'candidates':[candidate('new.example.com')]}
            self.assertTrue(spend('web'))
            engine.finish(exp.pk,'cancelled','User cancelled')
            with self.captureOnCommitCallbacks(execute=True):engine.resume(exp.pk,{})
            engine.run_expansion(str(exp.pk))
            stale_spend.append(spend('web'))
            return {'candidates':[candidate('stale.example.com')]}
        self.execute(exp,collect)
        self.assertEqual(stale_spend,[False])
        self.assertFalse(exp.candidates.filter(domain='stale.example.com').exists())
        self.assertTrue(exp.candidates.filter(domain='new.example.com').exists())
        self.assertEqual(exp.state,'succeeded')
        self.assertEqual(exp.usage['web'],1)

    def test_old_worker_cannot_write_after_replacement_claims_running_state(self):
        exp=self.start(sources=['web'],budgets={'web':3})
        allowed=[]
        def collect(source,seed,*,spend,**kwargs):
            self.assertTrue(spend('web'))
            engine.finish(exp.pk,'cancelled','User cancelled')
            engine.resume(exp.pk,{})
            # Model the replacement delivery claiming the resumed row while this
            # HTTP call is still returning. Both workers now see state=running.
            replacement={'state':'running','checkpoint':{'queue':[],'replacement_marker':True}}
            if hasattr(exp,'execution_token'):replacement['execution_token']='replacement-worker'
            DomainExpansion.objects.filter(pk=exp.pk).update(**replacement)
            allowed.append(spend('web'))
            return {'candidates':[candidate('stale.example.com')]}
        self.execute(exp,collect)
        self.assertEqual(allowed,[False])
        self.assertEqual(exp.state,'running')
        self.assertTrue(exp.checkpoint['replacement_marker'])
        self.assertEqual(exp.candidates.count(),0)

    def test_interrupted_local_source_without_http_reservation_retries_safely(self):
        from leakscope.phase3.expansion_worker import checkpoint_seed,enqueue
        exp=self.start()
        cp=checkpoint_seed(exp.spec);enqueue(cp,exp.spec['seeds'][0],exp.spec)
        cp['in_flight']='unissued-local-page';cp['in_flight_usage']=dict(exp.usage)
        exp.checkpoint=cp;exp.state='failed';exp.save()
        with patch.object(engine.run_expansion,'apply_async'):engine.resume(exp.pk,{})
        self.execute(exp,lambda *a,**k:{'candidates':[candidate('local.example.com')]})
        self.assertTrue(exp.candidates.filter(domain='local.example.com').exists())
        self.assertEqual(exp.state,'succeeded')

    def test_interrupted_reserved_paid_page_is_not_requested_again(self):
        from leakscope.phase3.expansion_worker import checkpoint_seed,enqueue
        exp=self.start(sources=['web'],budgets={'web':3})
        cp=checkpoint_seed(exp.spec);enqueue(cp,exp.spec['seeds'][0],exp.spec)
        cp['in_flight']='uncertain-paid-page';cp['in_flight_usage']=dict(exp.usage)
        exp.usage={**exp.usage,'web':1};exp.checkpoint=cp;exp.state='failed';exp.save()
        with patch.object(engine.run_expansion,'apply_async'):engine.resume(exp.pk,{})
        self.execute(exp,AssertionError('ambiguous paid request must not replay'))
        self.assertEqual(exp.usage['web'],1)
        self.assertEqual(exp.state,'succeeded')
        self.assertTrue(any('unknown' in str(c.get('notes')) for c in exp.coverage.values()))

    def test_worker_slice_yields_then_continues_same_saved_job(self):
        from leakscope.phase3 import expansion_worker
        from leakscope.services.runs import write_state
        exp=self.start();old_task=exp.run.task_id
        with patch.object(expansion_worker,'SLICE_SECONDS',0),patch.object(engine.run_expansion,'apply_async') as dispatch,self.captureOnCommitCallbacks(execute=True):
            engine.run_expansion(str(exp.pk))
        exp.refresh_from_db();exp.run.refresh_from_db()
        self.assertEqual(exp.state,'queued');self.assertNotEqual(exp.run.task_id,old_task)
        self.assertEqual(dispatch.call_count,1)
        write_state(exp.run.task_id,'SUCCESS',{'expansion_id':str(exp.pk),'state':'queued'})
        exp.run.refresh_from_db();self.assertEqual(exp.run.state,'queued')
        self.execute(exp,lambda *a,**k:{'candidates':[candidate('continued.example.com')]})
        self.assertEqual(exp.state,'succeeded');self.assertEqual(exp.candidates.count(),1)

    def test_runtime_ceiling_pauses_without_losing_frontier(self):
        from leakscope.phase3.expansion_worker import checkpoint_seed,enqueue
        exp=self.start(runtime_seconds=60)
        cp=checkpoint_seed(exp.spec);enqueue(cp,exp.spec['seeds'][0],exp.spec);cp['elapsed']=60
        exp.checkpoint=cp;exp.save()
        self.execute(exp,AssertionError('time allowance is exhausted'))
        self.assertEqual(exp.state,'paused')
        with self.assertRaises(ValueError):engine.resume(exp.pk,{})
        with patch.object(engine.run_expansion,'apply_async'):engine.resume(exp.pk,{'runtime_seconds':120})
        self.execute(exp,lambda *a,**k:{'candidates':[candidate('later.example.com')]})
        self.assertEqual(exp.state,'succeeded');self.assertEqual(exp.candidates.count(),1)
