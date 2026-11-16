"""Expansion progress and activity remain useful before a run finishes."""
from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3 import domain_expansion as engine
from leakscope_app.models import DomainExpansion,Run


class ExpansionActivityTests(TestCase):
    def start(self,**opts):
        with patch.object(engine.run_expansion,'apply_async'),self.captureOnCommitCallbacks(execute=True):
            return engine.start({'domains':['example.com'],'sources':['saved'],'max_depth':0,**opts})
    def test_sources_emit_real_activity_and_active_output_is_available(self):
        from leakscope_app.models import RunEvent
        exp=self.start(sources=['saved','web'],budgets={'web':1});snapshots=[]
        def collect(source,seed,*,spend,**kwargs):
            if source=='web':
                snapshots.append(self.client.get('/api/runs/'+str(exp.run_id)+'/events').json())
                self.assertTrue(spend('web'))
                return {'candidates':[]}
            return {'candidates':[{'domain':'api.example.com','evidence':[]},{'domain':'docs.example.com','evidence':[]}]}
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=collect):engine.run_expansion(str(exp.pk))
        data=snapshots[0]
        self.assertEqual(data['run']['progress_mode'],'indeterminate')
        self.assertEqual(data['snapshot']['expansion']['saved_candidates'],2)
        self.assertEqual(data['snapshot']['expansion']['current_source'],'web')
        self.assertEqual(data['snapshot']['expansion']['current_seed'],'example.com')
        events=list(RunEvent.objects.filter(run_id=exp.run_id).values('event','data','message'))
        self.assertTrue(any(e['event']=='source.started' and e['data'].get('source')=='saved' for e in events))
        self.assertTrue(any(e['event']=='source.completed' and e['data'].get('source')=='web' for e in events))
        self.assertTrue(any(e['event']=='request.reserved' and e['data'].get('bucket')=='web' for e in events))
        self.assertTrue(any(e['event']=='candidates.saved' for e in events))
        exp.refresh_from_db()
        self.assertEqual(exp.checkpoint['pending_queries'],0)
        self.assertGreaterEqual(exp.checkpoint['source_queries_completed'],2)
    def test_source_failure_is_logged_without_leaking_exception_content(self):
        from leakscope_app.models import RunEvent
        exp=self.start(sources=['web'])
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=RuntimeError('private-secret-runtime-data')):engine.run_expansion(str(exp.pk))
        rows=list(RunEvent.objects.filter(run_id=exp.run_id).values('event','message','data'))
        self.assertTrue(any(x['event']=='source.failed' for x in rows))
        self.assertNotIn('private-secret-runtime-data',str(rows))
    def test_quota_pause_and_resume_events_preserve_usage(self):
        from leakscope_app.models import RunEvent
        exp=self.start(max_candidates=1)
        result={'candidates':[{'domain':'a.example.com','evidence':[]},{'domain':'b.example.com','evidence':[]}]}
        with patch('leakscope.phase3.expansion_sources.collect',return_value=result):engine.run_expansion(str(exp.pk))
        exp.refresh_from_db();self.assertEqual(exp.state,'paused')
        with patch.object(engine.run_expansion,'apply_async'),self.captureOnCommitCallbacks(execute=True):engine.resume(exp.pk,{'max_candidates':0})
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=AssertionError('retained page must not be recollected')):engine.run_expansion(str(exp.pk))
        events=list(RunEvent.objects.filter(run_id=exp.run_id).values_list('event',flat=True))
        self.assertIn('run.paused',events);self.assertIn('run.resumed',events)
        exp.refresh_from_db();self.assertEqual(exp.candidates.count(),2);self.assertEqual(sum(exp.usage.values()),0)
    def test_cancellation_event_is_durable_and_late_worker_cannot_append(self):
        from leakscope_app.models import RunEvent
        exp=self.start(sources=['web'])
        def collect(*args,**kwargs):
            engine.finish(exp.pk,'cancelled','Stopped by user')
            return {'candidates':[{'domain':'stale.example.com','evidence':[]}]}
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=collect):engine.run_expansion(str(exp.pk))
        exp.refresh_from_db();self.assertEqual(exp.state,'cancelled');self.assertEqual(exp.candidates.count(),0)
        events=list(RunEvent.objects.filter(run_id=exp.run_id).values_list('event',flat=True))
        self.assertIn('expansion.cancelled',events);self.assertNotIn('source.completed',events)
