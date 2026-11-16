"""Offline contracts for saved workspace state and retained evidence browsing."""
import json
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, Client
from django.utils import timezone
from django.db import connection
from django.test.utils import CaptureQueriesContext
from leakscope.phase3.state import ingest_observation
from leakscope.phase3 import relationships as store
from leakscope_app.models import (ServiceObservation, ObservationChange, CertificateRecord, EvidenceArtifact,
                                 EvidenceCorrelation, Run, DomainExpansion, DomainCandidate)


class WorkspaceTests(TestCase):
    def test_public_identifier_view_roundtrips_filters_without_execution(self):
        from urllib.parse import parse_qsl, urlencode
        endpoint = self.endpoint()
        organization = store.node('organization', 'Example')
        self.scope(organization, store._service(endpoint))
        EvidenceCorrelation.objects.create(endpoint=endpoint, observation=endpoint.observations.first(),
            kind='tracking.google_tag_manager', value='GTM-EXAMPLE1')
        query = urlencode({'public': '1', 'organization_id': str(organization.pk),
            'kind': 'tracking.google_tag_manager', 'q': 'GTM-EXAMPLE1', 'page': '1', 'page_size': '25'})
        with patch('requests.get', side_effect=AssertionError('Saved views must not collect')):
            saved = self.post('views', {'name': 'Public projects', 'page': 'correlations', 'state': {'query': query}})
            self.assertEqual(saved.status_code, 201, saved.content)
            view = self.client.get('/api/workspace/views', {'page': 'correlations'}).json()['results'][0]
            reopened = dict(parse_qsl(view['state']['query']))
            self.assertEqual(reopened.pop('public'), '1')
            self.assertEqual(reopened['organization_id'], str(organization.pk))
            response = self.client.get('/api/workspace/public-signals', reopened)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['results'][0]['value'], 'GTM-EXAMPLE1')
        self.assertEqual(Run.objects.count(), 0)

    def test_public_view_flag_is_a_bounded_boolean_filter(self):
        for value in ['0', '1']:
            response = self.post('views', {'name': 'Public view', 'page': 'correlations', 'state': {'query': 'public=' + value}})
            self.assertEqual(response.status_code, 201, response.content)
        for value in ['', 'yes', 'https%3A%2F%2Fexample.com']:
            response = self.post('views', {'name': 'Invalid', 'page': 'correlations', 'state': {'query': 'public=' + value}})
            self.assertEqual(response.status_code, 400, response.content)

    def post(self,path,data):
        return self.client.post('/api/workspace/'+path,json.dumps(data),content_type='application/json')

    def endpoint(self,address='192.0.2.1',**metadata):
        return ingest_observation('shodan',{'address':address,'port':443,'metadata':metadata})

    def scope(self,org,node):
        proof=store.evidence(org,'user_scope',{'kind':node.kind,'value':node.value})
        store.relation(org,node,'user_scope',proof)

    def test_named_view_crud_persists_query_layout_and_sort(self):
        state={'query':'?q=fixture&sort=last_seen&direction=desc','tables':{'observations':{'density':'compact','hidden_columns':['title'],'sort':{'key':'technology','direction':'asc'}}},'disclosures':{'evidence':False}}
        response=self.post('views',{'name':'Focused storage','page':'assets','state':state})
        self.assertEqual(response.status_code,201,response.content)
        view=response.json()['view']
        self.assertEqual(view['state']['query'],'q=fixture&sort=last_seen&direction=desc')
        self.assertEqual(view['state']['tables'],state['tables'])
        self.assertEqual(self.client.get('/api/workspace/views?page=assets').json()['count'],1)
        self.assertEqual(self.client.get('/api/workspace/views?page=cloud').json()['results'],[])
        changed=self.post('views',{'id':view['id'],'name':'Renamed'})
        self.assertEqual(changed.status_code,200,changed.content)
        self.assertEqual(changed.json()['view']['state'],view['state'])
        self.assertEqual(changed.json()['view']['name'],'Renamed')
        self.assertEqual(self.post('views/'+view['id']+'/delete',{}).json()['deleted'],view['id'])
        self.assertEqual(self.client.get('/api/workspace/views').json()['count'],0)

    def test_view_validation_rejects_navigation_secrets_scripts_bad_types_size_and_protected_columns(self):
        invalid=[{'page':'settings'},{'page':'https://evil.test'},
            {'state':{'query':'https://evil.test'}},{'state':{'query':'api_key=private'}},
            {'state':{'query':{'q':'fixture'}}},{'state':{'disclosures':{'x':'yes'}}},
            {'state':{'tables':{'x':{'hidden_columns':['actions']}}}},
            {'state':{'tables':{'x':{'density':'giant'}}}},
            {'state':{'tables':{'x':{'sort':{'key':'title','direction':'random'}}}}},
            {'state':{'script':'alert(1)'}},{'state':{'query':'q='+'x'*5000}},
            {'state':{'disclosures':{str(i):True for i in range(101)}}}]
        for value in invalid:
            with self.subTest(value=str(value)[:120]):
                response=self.post('views',dict({'name':'Fixture','page':'assets','state':{}},**value))
                self.assertEqual(response.status_code,400,response.content[:200])
        csrf=Client(enforce_csrf_checks=True)
        self.assertEqual(csrf.post('/api/workspace/views','{}',content_type='application/json').status_code,403)

    def test_changes_merge_cursor_fixed_window_and_no_duplicates(self):
        ep=self.endpoint()
        before=ep.observations.get()
        after=ServiceObservation.objects.create(endpoint=ep,provider='shodan',fingerprint='new',data={})
        change=ObservationChange.objects.create(endpoint=ep,provider='shodan',previous=before,observation=after,summary=[{'field':'title','before':'old','after':'new'}])
        domain=store.node('domain','example.test')
        cert=CertificateRecord.objects.create(key='a'*64,domains=['example.test'])
        response=self.client.get('/api/workspace/changes',{'limit':2})
        self.assertEqual(response.status_code,200,response.content)
        data=response.json();rows=data['results']
        self.assertEqual(len(rows),2)
        self.assertTrue(data['has_more'])
        self.endpoint('192.0.2.99')
        next_page=self.client.get('/api/workspace/changes',{'cursor':data['next_cursor'],'limit':2}).json()
        rows+=next_page['results']
        self.assertEqual(len({r['key'] for r in rows}),4)
        self.assertEqual({r['kind'] for r in rows},{'change','endpoint','domain','certificate'})
        self.assertEqual(next_page['window'],data['window'])
        self.assertFalse(next_page['has_more'])
        self.assertIn('DNS',data['basis'])
        self.assertEqual(self.client.get('/api/workspace/changes',{'cursor':data['next_cursor'],'kind':'domain'}).status_code,400)

    def test_changes_tied_timestamps_are_stable_across_all_source_kinds(self):
        ep=self.endpoint()
        proof=ep.observations.get()
        stamp=timezone.now()-timedelta(minutes=1)
        type(ep).objects.filter(pk=ep.pk).update(first_seen=stamp)
        for n in range(3):
            ObservationChange.objects.create(endpoint=ep,provider='shodan',observation=proof,collected_at=stamp)
            store.node('domain',f'tie{n}.example.test',{})
            CertificateRecord.objects.create(key=str(n)*64,first_seen=stamp)
        from leakscope_app.models import AssetNode
        AssetNode.objects.filter(kind='domain').update(first_seen=stamp)
        rows=[];cursor=None
        for _ in range(15):
            query={'limit':1}
            if cursor:query['cursor']=cursor
            response=self.client.get('/api/workspace/changes',query)
            self.assertEqual(response.status_code,200,response.content)
            data=response.json();rows+=data['results'];cursor=data['next_cursor']
            if not data['has_more']:break
        self.assertEqual(len(rows),10)
        self.assertEqual(len(set(r['key'] for r in rows)),10)
        self.assertEqual([r['key'] for r in rows],sorted((r['key'] for r in rows),reverse=True))

    def test_changes_visibility_and_org_filter_use_explicit_membership(self):
        org=store.node('organization','Fixture Inc')
        ep=self.endpoint();other=self.endpoint('192.0.2.2')
        self.scope(org,store._service(ep))
        domain=store.node('domain','scope.example.test');self.scope(org,domain)
        before=ep.observations.get();before.hidden=True;before.save()
        after=ServiceObservation.objects.create(endpoint=ep,provider='shodan',fingerprint='new',data={})
        ObservationChange.objects.create(endpoint=ep,provider='shodan',previous=before,observation=after)
        data=self.client.get('/api/workspace/changes',{'organization_id':str(org.pk)}).json()
        self.assertEqual({r['kind'] for r in data['results']},{'domain','endpoint'})
        self.assertNotIn(str(other.pk),str(data))
        after.hidden=True;after.save()
        data=self.client.get('/api/workspace/changes',{'organization_id':str(org.pk)}).json()
        self.assertEqual([r['kind'] for r in data['results']],['domain'])

    def test_changes_redaction_and_invalid_window_and_cursor(self):
        ep=self.endpoint();proof=ep.observations.get()
        ObservationChange.objects.create(endpoint=ep,provider='shodan',observation=proof,
            summary=[{'field':'banner','after':'https://user:pass@example.test/?token=hidden Bearer opaque'}])
        data=self.client.get('/api/workspace/changes').content.decode()
        for value in ['user:pass','token=hidden','Bearer opaque']:self.assertNotIn(value,data)
        for query in [{'since':'bad'},{'since':(timezone.now()+timedelta(days=1)).isoformat()},{'limit':1000},{'cursor':'tampered'},{'kind':'dns_history'}]:
            self.assertEqual(self.client.get('/api/workspace/changes',query).status_code,400)

    def test_activity_reads_saved_state_without_broker_calls_or_mutation(self):
        active=Run.objects.create(task_id='fixture',kind='lookup',provider='shodan',state='running',result=json.dumps({'stored':5,'token':'private'}),error='Bearer secret')
        Run.objects.create(task_id='failed',kind='lookup',state='failed')
        old=Run.objects.create(task_id='old',kind='lookup',state='failed')
        Run.objects.filter(pk=old.pk).update(updated_at=timezone.now()-timedelta(days=3))
        with patch('leakscope.services.runs.AsyncResult',side_effect=AssertionError('No broker access')),patch('leakscope.services.runs.write_state',side_effect=AssertionError('No mutation')):
            response=self.client.get('/api/workspace/activity')
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()
        self.assertEqual(data['counts'],{'queued':0,'running':1,'paused':0,'failed':1})
        self.assertEqual(data['total_active'],1)
        self.assertEqual(data['results'][0]['result']['stored'],5)
        self.assertNotIn('private',str(data));self.assertNotIn('Bearer secret',str(data))
        active.refresh_from_db();self.assertEqual(active.state,'running')

    def test_activity_bulk_expansion_counts_and_query_budget(self):
        for n in range(8):
            run=Run.objects.create(task_id=f'exp{n}',kind='domain_expansion',state='paused')
            exp=DomainExpansion.objects.create(run=run,spec={'target':'fixture.example'},scope_key=f'scope{n}',state='paused',usage={'requests':3},checkpoint={'current_source':'dns','pending_queries':4})
            DomainCandidate.objects.create(expansion=exp,domain=f'candidate{n}.example',root='example')
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get('/api/workspace/activity')
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()
        self.assertEqual(data['counts']['paused'],8)
        self.assertTrue(all(r['activity']['saved_candidates']==1 for r in data['results']))
        self.assertTrue(all(r['activity']['pending_queries']==4 for r in data['results']))
        self.assertLessEqual(len(queries),12)

    def test_correlation_groups_visible_exact_signals_and_cursor(self):
        a=self.endpoint('192.0.2.1',certificate={'sha256':'a'*64},jarm='b'*62)
        b=self.endpoint('192.0.2.2',certificate={'sha256':'a'*64},jarm='b'*62)
        initial=EvidenceCorrelation.objects.count()
        with patch('leakscope.phase3.analysis.index_observation',side_effect=AssertionError('No indexing on GET')):
            response=self.client.get('/api/workspace/correlations',{'limit':1})
        self.assertEqual(response.status_code,200,response.content)
        data=response.json();self.assertEqual(len(data['results']),1);self.assertTrue(data['has_more'])
        group=data['results'][0]
        self.assertEqual(group['endpoint_count'],2)
        self.assertEqual(len(group['members']),2)
        self.assertIn('ownership',group['meaning'])
        next_page=self.client.get('/api/workspace/correlations',{'cursor':data['next_cursor'],'limit':1}).json()
        self.assertNotEqual(next_page['results'][0]['key'],group['key'])
        self.assertFalse(next_page['has_more'])
        self.assertEqual(EvidenceCorrelation.objects.count(),initial)
        b.observations.update(hidden=True)
        self.assertEqual(self.client.get('/api/workspace/correlations').json()['results'],[])

    def test_correlation_artifacts_honor_hidden_proof_even_on_visible_endpoint(self):
        for address in ['192.0.2.1','192.0.2.2']:
            ep=self.endpoint(address)
            hidden=ServiceObservation.objects.create(endpoint=ep,provider='fetch:screenshot',fingerprint='hidden',hidden=True)
            artifact=EvidenceArtifact.objects.create(endpoint=ep,kind='screenshot',relative_path='fixture.png',mime='image/png',size=3,sha256='a'*64,metadata={'observation_pk':hidden.pk.hex})
        self.assertEqual(self.client.get('/api/workspace/correlations').json()['results'],[])

    def test_correlation_org_scope_does_not_expand_shared_infrastructure(self):
        org=store.node('organization','Fixture Inc')
        a=self.endpoint('192.0.2.1',certificate={'sha256':'a'*64})
        b=self.endpoint('192.0.2.2',certificate={'sha256':'a'*64})
        c=self.endpoint('192.0.2.3',certificate={'sha256':'a'*64})
        self.scope(org,store._service(a));self.scope(org,store._service(b))
        response=self.client.get('/api/workspace/correlations',{'organization_id':str(org.pk)}).json()
        self.assertEqual(response['results'][0]['endpoint_count'],2)
        self.assertNotIn(str(c.pk),str(response))

    def test_legacy_empty_and_uppercase_artifact_source_respect_hidden_visibility(self):
        for source in ['',None,'upper']:
            for number in range(2):
                ep=self.endpoint(f'192.0.2.{10+number}')
                hidden=ServiceObservation.objects.create(endpoint=ep,provider='fetch:screenshot',fingerprint=str(source),hidden=True)
                value=str(hidden.pk).upper() if source=='upper' else source
                EvidenceArtifact.objects.create(endpoint=ep,kind='screenshot',relative_path='fixture.png',mime='image/png',size=3,sha256='d'*64,metadata={'observation_pk':value})
            data=self.client.get('/api/workspace/correlations').json()
            self.assertEqual(data['results'],[],str(source))

    def test_correlation_members_are_bounded_and_legacy_fk_proof_has_no_n_plus_one(self):
        from leakscope_app.models import ServiceEndpoint
        for n in range(21):
            ep=ServiceEndpoint.objects.create(key=f'fk{n}',address=f'fk{n}.example.test',port=443)
            proof=ServiceObservation.objects.create(endpoint=ep,provider='shodan',fingerprint='fk')
            EvidenceCorrelation.objects.create(endpoint=ep,kind='certificate.sha256',value='a'*64,observation=proof)
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get('/api/workspace/correlations')
        self.assertEqual(response.status_code,200,response.content)
        group=response.json()['results'][0]
        self.assertEqual(group['endpoint_count'],21)
        self.assertEqual(len(group['members']),20)
        self.assertTrue(group['members_truncated'])
        self.assertLessEqual(len(queries),10)

    def test_direct_saved_body_hash_is_indexed_on_write_and_is_exact_not_perceptual(self):
        self.endpoint('192.0.2.1',body_sha256='c'*64)
        self.endpoint('192.0.2.2',body_sha256='c'*64)
        result=self.client.get('/api/workspace/correlations',{'kind':'http.body.sha256'}).json()
        self.assertEqual(result['results'][0]['endpoint_count'],2)
        self.assertIn('Exact',result['results'][0]['meaning'])

    def test_changes_payload_summary_is_bounded(self):
        ep=self.endpoint();proof=ep.observations.get()
        ObservationChange.objects.create(endpoint=ep,provider='shodan',observation=proof,
            summary=[{'field':'banner','after':'x'*10000,'before':'Bearer private'}])
        result=self.client.get('/api/workspace/changes',{'kind':'change'}).json()
        self.assertLessEqual(len(result['results'][0]['summary'][0]['after']),700)
        self.assertNotIn('Bearer private',str(result))

    def test_canonical_view_query_must_remain_bounded_after_encoding(self):
        response=self.post('views',{'name':'Fixture','page':'assets','state':{'query':'q='+'☃'*1000}})
        self.assertEqual(response.status_code,400,response.content[:100])

    def test_explicit_local_reindex_passes_org_scope_and_returns_run(self):
        from types import ModuleType
        from unittest.mock import Mock
        helper=ModuleType('leakscope.phase3.local_correlations')
        run=Run.objects.create(task_id='index-fixture',kind='correlation_index',state='queued')
        helper.queue_reindex=Mock(return_value=run)
        org=store.node('organization','Fixture Inc');ep=self.endpoint();self.scope(org,store._service(ep))
        other=self.endpoint('192.0.2.2')
        with patch.dict('sys.modules',{'leakscope.phase3.local_correlations':helper}):
            response=self.post('correlations/reindex',{'limit':50,'organization_id':str(org.pk)})
        self.assertEqual(response.status_code,202,response.content)
        self.assertEqual(response.json()['run']['id'],str(run.pk))
        self.assertEqual(helper.queue_reindex.call_args.args[0],{'limit':50})
        endpoints=helper.queue_reindex.call_args.kwargs['endpoints']
        self.assertEqual(set(endpoints.values_list('pk',flat=True)),{ep.pk})
        self.assertNotIn(other.pk,set(endpoints.values_list('pk',flat=True)))
        self.assertEqual(self.client.get('/api/workspace/correlations/reindex').status_code,405)

    def test_similarity_calls_only_saved_helper_with_explicit_bounded_sampling(self):
        from types import ModuleType
        from unittest.mock import Mock
        helper=ModuleType('leakscope.phase3.local_correlations')
        helper.visible_artifacts=lambda endpoints=None:EvidenceArtifact.objects.none()
        helper.screenshot_similarity=Mock(return_value={'results':[],'has_more':False,'truncated':False,
            'sampled_signals':0,'signal_limit':500,'threshold':8,'algorithm':'dHash64','basis':'Saved local hashes only'})
        with patch.dict('sys.modules',{'leakscope.phase3.local_correlations':helper}):
            response=self.client.get('/api/workspace/correlations',{'similar':'1','threshold':8,'signal_limit':500})
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()
        self.assertEqual(data['algorithm'],'dHash64')
        self.assertIsNone(data['next_cursor'])
        self.assertEqual(helper.screenshot_similarity.call_args.kwargs,{'threshold':8,'limit':500})

    def test_activity_retains_safe_local_index_counters(self):
        Run.objects.create(task_id='local-progress',kind='correlation_index',state='running',result=json.dumps({
            'planned':50,'processed':4,'indexed':2,'signals':3,'skipped':1,'errors':1,'network_contact':False,'token':'private'}))
        result=self.client.get('/api/workspace/activity').json()['results'][0]['result']
        self.assertEqual(result['planned'],50)
        self.assertEqual(result['indexed'],2)
        self.assertEqual(result['errors'],1)
        self.assertNotIn('token',result)

    @patch('leakscope.celery.app.control.revoke')
    def test_local_index_stop_is_cooperative_and_preserves_saved_counters(self,revoke):
        row=Run.objects.create(task_id='local-stop',kind='correlation_index',state='running',result='{"indexed":2}')
        response=self.client.post(f'/api/runs/{row.pk}/cancel','{}',content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        row.refresh_from_db()
        self.assertEqual(row.state,'cancelled')
        self.assertEqual(json.loads(row.result)['indexed'],2)
        revoke.assert_called_once_with('local-stop',terminate=False)

    def test_correlations_require_proof_on_the_group_member_endpoint(self):
        a=self.endpoint('192.0.2.1');b=self.endpoint('192.0.2.2');foreign=self.endpoint('192.0.2.3')
        proof=foreign.observations.get()
        for ep in [a,b]:
            row=EvidenceCorrelation.objects.create(endpoint=ep,kind='certificate.sha256',value='a'*64,observation=proof)
            row.observations.add(proof)
        self.assertEqual(self.client.get('/api/workspace/correlations').json()['results'],[])

    def test_layout_keys_accept_121_to_200_chars_but_keep_other_limits(self):
        for length in (121,200):
            key='k'*length
            response=self.post('views',{'name':'Long layout keys','page':'assets','state':{
                'tables':{key:{'density':'compact','hidden_columns':[]}},'disclosures':{key:True}}})
            self.assertEqual(response.status_code,201,response.content)
            self.assertIn(key,response.json()['view']['state']['tables'])
            self.assertIn(key,response.json()['view']['state']['disclosures'])
        for state in ({'tables':{'k'*201:{}}},{'disclosures':{'k'*201:True}},
                      {'disclosures':{f'{n:03d}'+'k'*197:True for n in range(100)}}):
            response=self.post('views',{'name':'Invalid layout','page':'assets','state':state})
            self.assertEqual(response.status_code,400,response.content[:200])
        response=self.post('views',{'name':'n'*121,'page':'assets','state':{}})
        self.assertEqual(response.status_code,400,response.content[:200])

    def test_domain_expansion_view_preserves_company_and_focused_entry_params(self):
        query='company=Fixture+Company&focused=1'
        response=self.post('views',{'name':'Focused company expansion','page':'domain-expansion','state':{'query':query}})
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(response.json()['view']['state']['query'],query)
