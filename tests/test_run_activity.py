"""Run activity contracts; all requests and tasks are offline."""
import json
from unittest.mock import patch,Mock
from django.test import TestCase
from leakscope_app.models import Run,DomainExpansion,DomainCandidate


class RunActivityTests(TestCase):
    def run_row(self,**kw):return Run.objects.create(task_id='task-fixture',kind='lookup',**kw)
    def get(self,run,query=''):return self.client.get('/api/runs/'+str(run.pk)+'/events'+query)

    def test_events_are_ordered_and_incrementally_paginated(self):
        from leakscope.services.run_events import emit
        run=self.run_row()
        for message in ['first','second','third']:emit(run,message,event='source')
        first=self.get(run,'?limit=2').json()
        self.assertEqual([e['message'] for e in first['events']],['first','second'])
        self.assertTrue(first['has_more'])
        second=self.get(run,'?after='+str(first['next_cursor'])+'&limit=2').json()
        self.assertEqual([e['message'] for e in second['events']],['third'])
        self.assertFalse(second['has_more'])
        self.assertTrue(all(e['created_at'] for e in first['events']))

    def test_redaction_applies_before_storage_and_to_old_snapshot(self):
        from leakscope.services.run_events import emit
        from leakscope_app.models import RunEvent
        run=self.run_row(result=json.dumps({'Authorization':'Bearer old-private','nested':{'api_key':'old-secret'},'safe':'https://user:private@example.com/?token=url-secret'}))
        with patch('leakscope.services.config.get_config',return_value={'config':{'NETLAS_API_KEY':'configured-secret'}}):
            emit(run,'configured-secret Bearer opaque-secret https://user:pass@example.com/?api_key=query-secret',data={'password':'hidden-password','safe':'text'})
            data=self.get(run).json()
        raw=str(data)+str(RunEvent.objects.get().data)+RunEvent.objects.get().message
        for secret in ['configured-secret','opaque-secret','hidden-password','query-secret','old-private','old-secret','url-secret','user:pass','user:private']:
            self.assertNotIn(secret,raw)
        self.assertIn('REDACTED',raw)

    def test_dedupe_keys_are_per_run(self):
        from leakscope.services.run_events import emit
        from leakscope_app.models import RunEvent
        first=self.run_row();second=self.run_row()
        emit(first,'started',dedupe_key='started');emit(first,'started',dedupe_key='started');emit(second,'started',dedupe_key='started')
        self.assertEqual(RunEvent.objects.count(),2)

    def test_lifecycle_and_progress_events_do_not_repeat_on_poll(self):
        from leakscope.services.runs import write_state
        from leakscope_app.models import RunEvent
        run=self.run_row()
        write_state(run.task_id,'STARTED',{});write_state(run.task_id,'STARTED',{})
        write_state(run.task_id,'PROGRESS',{'current':1,'total':5,'status':'page retained'})
        write_state(run.task_id,'SUCCESS',{'stored':2})
        self.assertEqual(RunEvent.objects.filter(event='started').count(),1)
        self.assertEqual(RunEvent.objects.filter(event='progress').count(),1)
        self.assertEqual(RunEvent.objects.filter(event='completed').count(),1)
        self.assertEqual(self.get(run).json()['snapshot']['result']['stored'],2)

    def test_old_expansion_has_saved_output_without_fabricated_events(self):
        run=self.run_row(state='running');run.kind='domain_expansion';run.save()
        exp=DomainExpansion.objects.create(run=run,spec={'demo':False},scope_key='fixture',state='running',usage={'web':2},checkpoint={'current_source':'web','pending_queries':30,'processed':3,'elapsed':99},coverage={'x':{'source':'web','state':'completed','candidates':8}})
        DomainCandidate.objects.create(expansion=exp,domain='example.net',root='example.net')
        data=self.get(run).json()
        self.assertEqual(data['events'],[])
        self.assertEqual(data['snapshot']['expansion']['saved_candidates'],1)
        self.assertEqual(data['snapshot']['expansion']['pending_queries'],30)
        self.assertEqual(data['run']['progress_mode'],'indeterminate')
        self.assertEqual(data['run']['activity']['saved_candidates'],1)

    def test_worker_execution_token_blocks_stale_events(self):
        from leakscope.services.run_events import emit
        from leakscope_app.models import RunEvent
        run=self.run_row(state='running');run.kind='domain_expansion';run.save()
        exp=DomainExpansion.objects.create(run=run,spec={},scope_key='fixture',state='running',execution_token='current')
        emit(run,'stale',execution_token='old');emit(run,'live',execution_token='current')
        exp.state='cancelled';exp.save()
        emit(run,'late',execution_token='current')
        self.assertEqual(list(RunEvent.objects.values_list('message',flat=True)),['live'])

    @patch('leakscope.celery.app.control.revoke')
    def test_stop_expansion_retains_results_and_checkpoint(self,revoke):
        run=self.run_row(state='running',result='{"stored":3}');run.kind='domain_expansion';run.save()
        exp=DomainExpansion.objects.create(run=run,spec={},scope_key='fixture',state='running',checkpoint={'queue':[{'source':'web'}]})
        DomainCandidate.objects.create(expansion=exp,domain='saved.net',root='saved.net')
        response=self.client.post('/api/runs/'+str(run.pk)+'/cancel','{}',content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        exp.refresh_from_db();run.refresh_from_db()
        self.assertEqual(exp.state,'cancelled');self.assertEqual(run.state,'cancelled')
        self.assertTrue(exp.checkpoint['queue']);self.assertEqual(exp.candidates.count(),1)
        revoke.assert_called_once_with(run.task_id,terminate=False)
        self.assertEqual(self.get(run).json()['events'][-1]['event'],'cancelled')

    @patch('leakscope.celery.app.control.revoke')
    def test_stop_cloud_run_preserves_partial_output_and_rejects_late_state(self,revoke):
        from leakscope.services.runs import write_state
        run=self.run_row(state='running',result='{"stored":2}');run.kind='cloud';run.save()
        response=self.client.post('/api/runs/'+str(run.pk)+'/cancel','{}',content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        write_state(run.task_id,'SUCCESS',{'stored':99});run.refresh_from_db()
        self.assertEqual(run.state,'cancelled');self.assertEqual(json.loads(run.result)['stored'],2)

    def test_bad_event_cursors_and_other_run_events_are_rejected_or_isolated(self):
        from leakscope.services.run_events import emit
        first=self.run_row();other=self.run_row();emit(other,'private to other run')
        self.assertEqual(self.get(first).json()['events'],[])
        for query in ['?after=-1','?after=no','?limit=0','?limit=501']:
            self.assertEqual(self.get(first,query).status_code,400,query)

    def test_missing_event_table_is_nonfatal_to_collection(self):
        from django.db import OperationalError
        from leakscope.services.run_events import emit
        run=self.run_row()
        with patch('leakscope_app.models.RunEvent.objects.create',side_effect=OperationalError('unavailable')):
            self.assertIsNone(emit(run,'diagnostic'))
        self.assertEqual(Run.objects.count(),1)

    def test_late_worker_updates_do_not_append_after_terminal_state(self):
        from leakscope.services.runs import write_state
        from leakscope.services.run_events import emit
        from leakscope_app.models import RunEvent
        run=self.run_row(state='cancelled')
        write_state(run.task_id,'STARTED',{});write_state(run.task_id,'PROGRESS',{'current':2})
        emit(run,'late progress',event='progress',task_id=run.task_id)
        self.assertEqual(RunEvent.objects.count(),0)

    def test_json_shaped_credentials_inside_messages_are_masked(self):
        from leakscope.services.run_events import sanitize
        value=sanitize('error {"password":"json-password", "api_key": "json-key", "Cookie":"session=cookie-secret"} and Authorization: Bearer header-secret at postgres://user:db-secret@example.com/db')
        for secret in ['json-password','json-key','cookie-secret','header-secret','db-secret']:self.assertNotIn(secret,value)

    def test_old_task_state_write_cannot_overwrite_replacement_delivery(self):
        from leakscope.services.runs import write_state
        run=self.run_row(state='running');original=Run.objects.filter;rotated=[]
        def intercept(*args,**kwargs):
            query=original(*args,**kwargs)
            if kwargs.get('pk')==run.pk and 'state__in' in kwargs and not rotated:
                rotated.append(True);original(pk=run.pk).update(task_id='replacement',state='queued',progress_current=7,progress_total=8)
            return query
        with patch('leakscope.services.runs.Run.objects.filter',side_effect=intercept):write_state(run.task_id,'PROGRESS',{'current':1,'total':10})
        run.refresh_from_db()
        self.assertEqual(run.task_id,'replacement');self.assertEqual(run.state,'queued')
        self.assertEqual(run.progress_current,7)

    def test_old_expiry_read_cannot_mark_replacement_or_fresh_heartbeat_lost(self):
        from datetime import timedelta
        from django.utils import timezone
        from leakscope.services.runs import get_run
        for rotate_task in (False,True):
            with self.subTest(rotate_task=rotate_task):
                run=self.run_row(state='running');run.kind='domain_expansion';run.save()
                old=timezone.now()-timedelta(hours=2);Run.objects.filter(pk=run.pk).update(updated_at=old,started_at=old)
                original=Run.objects.filter;updated=[]
                def intercept(*args,**kwargs):
                    query=original(*args,**kwargs)
                    if kwargs.get('pk')==run.pk and 'state__in' in kwargs and not updated:
                        updated.append(True);fields={'updated_at':timezone.now()}
                        if rotate_task:fields.update(task_id='replacement',state='queued')
                        original(pk=run.pk).update(**fields)
                    return query
                result=Mock();result.backend.get_task_meta.return_value={'status':'PENDING'}
                with patch('leakscope.services.runs.AsyncResult',return_value=result),patch('leakscope.services.runs.Run.objects.filter',side_effect=intercept):get_run(run.pk)
                run.refresh_from_db();self.assertEqual(run.state,'queued' if rotate_task else 'running')
                Run.objects.filter(pk=run.pk).delete()

    def test_emit_preserves_celery_task_control_exceptions(self):
        from celery.exceptions import SoftTimeLimitExceeded,Ignore,Retry,Reject
        from leakscope.services.run_events import emit
        run=self.run_row()
        for exception in (SoftTimeLimitExceeded(),Ignore(),Retry(),Reject()):
            with self.subTest(exception=type(exception).__name__),patch('leakscope.services.run_events.sanitize',side_effect=exception):
                with self.assertRaises(type(exception)):emit(run,'control flow')

    @patch('leakscope.celery.app.control.revoke')
    def test_unsupported_stop_does_not_claim_cancel_or_mutate_output(self,revoke):
        from leakscope.services.runs import serialize_run
        for state in ('queued','running'):
            with self.subTest(state=state):
                run=self.run_row(state=state,result='{"stored":2}')
                response=self.client.post('/api/runs/'+str(run.pk)+'/cancel','{}',content_type='application/json')
                self.assertEqual(response.status_code,400,response.content)
                run.refresh_from_db();self.assertEqual(run.state,state);self.assertEqual(json.loads(run.result)['stored'],2)
                self.assertFalse(serialize_run(run)['can_cancel'])
        revoke.assert_not_called()

    def test_url_credentials_preserved_schemes_and_long_plain_text(self):
        from leakscope.services.run_events import sanitize
        import time
        cases=['https://user:pass@example.test/path','(http://user:pass@example.test)',
               'postgresql+psycopg://user:pass@db.example.test/db','custom.v2-scheme://user:pass@example.test']
        for text in cases:
            self.assertNotIn('user:pass',sanitize(text))
            self.assertIn('[REDACTED]@',sanitize(text))
        start=time.monotonic()
        plain='a'*50000
        self.assertEqual(sanitize(plain),plain)
        self.assertLess(time.monotonic()-start,2,'URL credential scanning must not retry from every character')

    def test_url_without_userinfo_has_bounded_colon_heavy_authority_scan(self):
        from leakscope.services.run_events import sanitize
        import time
        text='https://' + ':'*50000 + 'example.test/no-userinfo'
        start=time.monotonic()
        self.assertEqual(sanitize(text),text)
        self.assertLess(time.monotonic()-start,2,'A no-userinfo URL must not backtrack across overlapping authority groups')

    def test_evidence_metric_opt_in_preserves_only_numeric_recognized_usage(self):
        from leakscope.services.run_events import sanitize
        data={'token_count':15,'input_tokens':10,'password':42,'nested':{'token_count':'opaque-token'}}
        self.assertEqual(sanitize(data)['token_count'],'[REDACTED]')
        safe=sanitize(data,preserve_metrics=True)
        self.assertEqual(safe['token_count'],15)
        self.assertEqual(safe['input_tokens'],10)
        self.assertEqual(safe['password'],'[REDACTED]')
        self.assertEqual(safe['nested']['token_count'],'[REDACTED]')
