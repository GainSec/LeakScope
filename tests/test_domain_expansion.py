import json
from unittest.mock import patch
from django.test import TestCase
from celery.exceptions import Ignore
from leakscope.phase3 import relationships as store


class DomainExpansionTests(TestCase):
    def post(self, path='', data=None):
        return self.client.post('/api/assets/expansions'+path, json.dumps(data or {}), content_type='application/json')

    def start(self, **data):
        with patch('leakscope.phase3.domain_expansion.run_expansion.apply_async'):
            with self.captureOnCommitCallbacks(execute=True):
                response=self.post(data=data or {'domains':['example.com'],'sources':['certificates']})
        self.assertEqual(response.status_code,200,response.content)
        return response.json()['expansion']['id']

    def test_empty_inputs_are_rejected(self):
        response=self.post(data={})
        self.assertEqual(response.status_code,400,response.content)

    def test_listing_is_read_only(self):
        response=self.client.get('/api/assets/expansions')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['results'],[])

    def test_normalized_seeds_demo_and_paid_opt_in(self):
        from leakscope.phase3.domain_expansion import normalize_spec
        spec=normalize_spec({'domains':['API.Example.com.','api.example.com'],'companies':[' Example Corp '],'demo':True,'budgets':{'web':20,'netlas':3}})
        self.assertEqual([s['value'] for s in spec['seeds']],['Example Corp','api.example.com','example.com'])
        self.assertLessEqual(spec['budgets']['web'],1)
        self.assertEqual(spec['budgets']['netlas'],0)
        for value in [{'domains':['com']},{'domains':['http://example.com']},{'domains':['127.0.0.1']},{'domains':['example.com'],'max_depth':True},{'domains':['example.com'],'sources':['unknown']},{'domains':['example.com'],'demo':'yes'}]:
            with self.subTest(value=value), self.assertRaises(ValueError): normalize_spec(value)

    def test_organization_prefills_and_archive_blocks(self):
        org=store.node('organization','Example Corp',{'aliases':['Other Brand']})
        store.relation(org,store.node('domain','example.com'),'user_scope',store.evidence(org,'user_scope',{'domain':'example.com'}))
        key=self.start(organization_id=str(org.pk),sources=['certificates'])
        detail=self.client.get('/api/assets/expansions/'+key).json()['expansion']
        self.assertIn('example.com',[s['value'] for s in detail['seeds']])
        self.assertIn('Other Brand',[s['value'] for s in detail['seeds']])
        org.organization_status='archived';org.save()
        self.assertEqual(self.post(data={'organization_id':str(org.pk)}).status_code,400)

    def test_local_certificates_persist_unrelated_names_and_wildcards(self):
        from leakscope.phase3.domain_expansion import run_expansion
        from leakscope_app.models import CertificateRecord,CertificateName
        cert=CertificateRecord.objects.create(key='fixture-cert',domains=['api.example.com','otherbrand.net','*.otherbrand.net'])
        for name in cert.domains:CertificateName.objects.create(certificate=cert,name=name)
        key=self.start(domains=['example.com'],sources=['certificates'])
        run_expansion(key)
        data=self.client.get('/api/assets/expansions/'+key).json()
        self.assertEqual(data['expansion']['state'],'succeeded')
        self.assertTrue({'otherbrand.net','*.otherbrand.net'} <= {c['domain'] for c in data['candidates']})
        self.assertTrue(all(c['status']=='pending' for c in data['candidates']))

    def test_budget_is_shared_across_frontier_duplicate_delivery_and_cache(self):
        from leakscope.phase3.domain_expansion import run_expansion
        calls=[]
        def collect(source,seed,*,spend,**kwargs):
            if not spend('web'):return {'candidates':[],'notes':['budget stopped']}
            calls.append(seed['value'])
            return {'candidates':[{'domain':'otherbrand.net','relation':'related','evidence':[{'source':'fixture','signal':'certificate_san','strength':35}]}]}
        key=self.start(domains=['example.com'],sources=['web'],budgets={'web':1},max_depth=2)
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=collect):
            run_expansion(key)
            with self.assertRaises(Ignore):run_expansion(key)
        detail=self.client.get('/api/assets/expansions/'+key).json()
        self.assertEqual(len(calls),1)
        self.assertEqual(detail['expansion']['usage']['web'],1)
        self.assertTrue(detail['expansion']['partial'])
        self.assertLess(detail['candidates'][0]['confidence'],50)
        second=self.start(domains=['example.com'],sources=['web'],budgets={'web':1},max_depth=0)
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=AssertionError('cached')):run_expansion(second)
        self.assertEqual(self.client.get('/api/assets/expansions/'+second).json()['expansion']['usage']['web'],0)

    def test_cancelled_run_never_collects(self):
        from leakscope.phase3.domain_expansion import run_expansion
        key=self.start(domains=['example.com'],sources=['web'])
        self.assertEqual(self.post('/'+key+'/cancel').status_code,200)
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=AssertionError('cancelled')), self.assertRaises(Ignore):run_expansion(key)
        self.assertEqual(self.client.get('/api/assets/expansions/'+key).json()['expansion']['state'],'cancelled')

    def test_partial_error_keeps_results_and_hides_secret(self):
        from leakscope.phase3.domain_expansion import run_expansion
        key=self.start(domains=['example.com'],sources=['saved','web'])
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=[{'candidates':[{'domain':'other.net','evidence':[{'source':'fixture','signal':'same_certificate','strength':30}]}]},RuntimeError('sensitive upstream details')]):run_expansion(key)
        detail=self.client.get('/api/assets/expansions/'+key).json()
        self.assertTrue(detail['expansion']['partial']);self.assertEqual(len(detail['candidates']),1)
        self.assertNotIn('sensitive upstream details',str(detail))

    def test_accept_reject_tag_suppression_and_graph(self):
        from leakscope_app.models import DomainCandidate,DomainExpansion,AssetRelationship
        from leakscope.phase3.domain_expansion import run_expansion
        org=store.node('organization','Example Corp')
        key=self.start(organization_id=str(org.pk),domains=['example.com'],sources=['saved'])
        candidate=DomainCandidate.objects.create(expansion_id=key,domain='otherbrand.net',root='otherbrand.net',evidence=[{'source':'fixture','signal':'legal_statement','strength':70}])
        response=self.post('/'+key+'/review',{'candidate_ids':[str(candidate.pk)],'action':'accept'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertTrue(AssetRelationship.objects.filter(source=org,target__value='otherbrand.net',relation='user_scope').exists())
        self.assertEqual(self.post('/'+key+'/review',{'candidate_ids':[str(candidate.pk)],'action':'tag','tags':['brand']}).status_code,200)
        self.assertEqual(self.post('/'+key+'/review',{'candidate_ids':[str(candidate.pk)],'action':'reject'}).status_code,200)
        rejected=DomainCandidate.objects.create(expansion_id=key,domain='rejected-only.net',root='rejected-only.net')
        self.post('/'+key+'/review',{'candidate_ids':[str(rejected.pk)],'action':'reject'})
        nextkey=self.start(organization_id=str(org.pk),domains=['example.com'],sources=['saved'])
        with patch('leakscope.phase3.expansion_sources.collect',return_value={'candidates':[{'domain':'otherbrand.net','evidence':[]},{'domain':'rejected-only.net','evidence':[]}]}):run_expansion(nextkey)
        self.assertFalse(DomainCandidate.objects.filter(expansion_id=nextkey,domain='otherbrand.net').exists())
        self.assertEqual(DomainCandidate.objects.get(expansion_id=nextkey,domain='rejected-only.net').status,'rejected')
        org.organization_status='archived';org.save()
        self.assertEqual(self.post('/'+key+'/review',{'candidate_ids':[str(candidate.pk)],'action':'accept'}).status_code,400)

    def test_wrong_run_candidate_is_rejected(self):
        from leakscope_app.models import DomainCandidate
        key=self.start(domains=['example.com'],sources=['saved'])
        other=self.start(domains=['example.net'],sources=['saved'])
        c=DomainCandidate.objects.create(expansion_id=other,domain='other.net',root='other.net')
        self.assertEqual(self.post('/'+key+'/review',{'candidate_ids':[str(c.pk)],'action':'accept'}).status_code,400)

    def test_expand_selected_does_not_restart_whole_organization(self):
        from leakscope_app.models import DomainCandidate
        org=store.node('organization','Example Corp')
        key=self.start(organization_id=str(org.pk),domains=['example.com'],sources=['saved'])
        c=DomainCandidate.objects.create(expansion_id=key,domain='api.otherbrand.net',root='otherbrand.net')
        with patch('leakscope.phase3.domain_expansion.run_expansion.apply_async'):
            response=self.post('/'+key+'/expand',{'candidate_ids':[str(c.pk)]})
        self.assertEqual(response.status_code,200,response.content)
        seeds=response.json()['expansion']['seeds']
        self.assertEqual({s['value'] for s in seeds},{'api.otherbrand.net','otherbrand.net'})

    def test_recursion_pivots_to_new_registrable_root(self):
        from leakscope.phase3.domain_expansion import run_expansion
        visited=[]
        def collect(source,seed,**kwargs):
            visited.append(seed['value'])
            return {'candidates':[{'domain':'api.otherbrand.net','evidence':[{'source':'fixture','signal':'certificate_san','strength':35}]}]}
        key=self.start(domains=['example.com'],sources=['saved'],max_depth=1)
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=collect):run_expansion(key)
        self.assertIn('otherbrand.net',visited)

    def test_candidate_cap_stops_spending_on_later_sources(self):
        from leakscope.phase3.domain_expansion import run_expansion
        visited=[]
        def collect(source,seed,**kwargs):
            visited.append(source)
            return {'candidates':[{'domain':'other.net','evidence':[]},{'domain':'extra.net','evidence':[]}]}
        key=self.start(domains=['example.com'],sources=['saved','web'],max_candidates=1)
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=collect):run_expansion(key)
        self.assertEqual(visited,['saved'])

    def test_merge_preserves_expansion_and_rejection(self):
        from leakscope.phase3.organization_lifecycle import merge
        from leakscope_app.models import DomainCandidate,DomainExpansion,DomainDecision
        source=store.node('organization','Source');target=store.node('organization','Target')
        key=self.start(organization_id=str(source.pk),domains=['example.com'],sources=['saved'])
        c=DomainCandidate.objects.create(expansion_id=key,domain='other.net',root='other.net')
        self.post('/'+key+'/review',{'candidate_ids':[str(c.pk)],'action':'reject'})
        merge(source.pk,target.pk,source.value)
        self.assertEqual(DomainExpansion.objects.get(pk=key).organization_id,target.pk)
        self.assertEqual(DomainDecision.objects.get(domain='other.net').organization_id,target.pk)
        self.assertEqual(self.client.get('/api/assets/expansions',{'organization_id':str(source.pk)}).json()['results'][0]['organization_id'],str(target.pk))

    def test_cancel_during_source_blocks_next_request(self):
        from leakscope.phase3.domain_expansion import run_expansion,finish
        key=self.start(domains=['example.com'],sources=['web'])
        def collect(source,seed,*,spend,**kwargs):
            self.assertTrue(spend('web'));finish(key,'cancelled','User cancelled')
            self.assertFalse(spend('web'));return {'candidates':[]}
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=collect):run_expansion(key)
        self.assertEqual(self.client.get('/api/assets/expansions/'+key).json()['expansion']['usage']['web'],1)

    def test_duplicate_delivery_cannot_terminalize_original_run(self):
        from leakscope.phase3.domain_expansion import run_expansion
        from leakscope_app.models import DomainExpansion
        key=self.start(domains=['example.com'],sources=['saved'])
        exp=DomainExpansion.objects.get(pk=key);exp.state='running';exp.save()
        result=run_expansion.apply(args=[key],task_id=exp.run.task_id)
        exp.run.refresh_from_db()
        self.assertEqual(result.state,'IGNORED')
        self.assertNotIn(exp.run.state,{'succeeded','skipped','failed'})

    def test_tag_old_candidate_preserves_newer_rejection(self):
        from leakscope_app.models import DomainCandidate,DomainDecision
        org=store.node('organization','Example Corp')
        first=self.start(organization_id=str(org.pk),sources=['saved'])
        old=DomainCandidate.objects.create(expansion_id=first,domain='other.net',root='other.net')
        second=self.start(organization_id=str(org.pk),sources=['saved'])
        new=DomainCandidate.objects.create(expansion_id=second,domain='other.net',root='other.net')
        self.post('/'+second+'/review',{'candidate_ids':[str(new.pk)],'action':'reject'})
        self.post('/'+first+'/review',{'candidate_ids':[str(old.pk)],'action':'tag','tags':['reviewed']})
        decision=DomainDecision.objects.get(domain='other.net')
        self.assertEqual(decision.status,'rejected');self.assertIn('reviewed',decision.tags)

    def test_name_variant_does_not_downgrade_supported_subsidiary(self):
        from leakscope.phase3.domain_expansion import run_expansion
        key=self.start(domains=['example.com'],sources=['saved','variants'],max_depth=0)
        rows=[{'candidates':[{'domain':'other.net','relation':'subsidiary','evidence':[{'source':'fixture','signal':'legal_statement','strength':65}]}]}, {'candidates':[{'domain':'other.net','relation':'lookalike','evidence':[{'source':'variants','signal':'name_variant','strength':10}]}]}]
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=rows):run_expansion(key)
        candidate=self.client.get('/api/assets/expansions/'+key).json()['candidates'][0]
        self.assertEqual(candidate['relation'],'subsidiary')
        self.assertGreaterEqual(candidate['confidence'],50)

    def test_standalone_rejection_scope_survives_organization_merge(self):
        from leakscope_app.models import DomainCandidate,DomainExpansion,DomainDecision
        from leakscope.phase3.organization_lifecycle import merge
        source=store.node('organization','Source');target=store.node('organization','Target')
        key=self.start(domains=['example.com'],sources=['saved'])
        exp=DomainExpansion.objects.get(pk=key)
        c=DomainCandidate.objects.create(expansion=exp,domain='other.net',root='other.net')
        self.post('/'+key+'/review',{'candidate_ids':[str(c.pk)],'action':'reject','organization_id':str(source.pk)})
        merge(source.pk,target.pk,source.value)
        self.assertTrue(DomainDecision.objects.filter(scope_key=exp.scope_key,domain='other.net',status='rejected').exists())

    def test_known_seed_names_do_not_consume_new_candidate_limit(self):
        from leakscope.phase3.domain_expansion import run_expansion
        key=self.start(domains=['example.com'],sources=['saved','web'],demo=True)
        responses=[{'candidates':[{'domain':'example.com','evidence':[]}]},{'candidates':[{'domain':'other.net','evidence':[]}]}]
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=responses):run_expansion(key)
        data=self.client.get('/api/assets/expansions/'+key).json()
        self.assertEqual([c['domain'] for c in data['candidates']],['other.net'])

    def test_standalone_scope_does_not_depend_on_seed_order(self):
        from leakscope.phase3.domain_expansion import _scope,normalize_spec
        first=normalize_spec({'domains':['example.com','other.net']})
        second=normalize_spec({'domains':['other.net','example.com']})
        self.assertEqual(_scope(None,first),_scope(None,second))

    def test_ambiguous_company_context_has_separate_review_scope(self):
        from leakscope.phase3.domain_expansion import _scope,normalize_spec
        first=normalize_spec({'companies':['Acme'],'context':{'country':'US'}})
        second=normalize_spec({'companies':['Acme'],'context':{'country':'France'}})
        self.assertNotEqual(_scope(None,first),_scope(None,second))

    def test_child_expansion_does_not_reoffer_existing_organization_scope(self):
        from leakscope.phase3.domain_expansion import run_expansion,start
        org=store.node('organization','Example Corp')
        store.relation(org,store.node('domain','known.net'),'user_scope',store.evidence(org,'user_scope',{'domain':'known.net'}))
        with patch('leakscope.phase3.domain_expansion.run_expansion.apply_async'):
            exp=start({'organization_id':str(org.pk),'domains':['other.net'],'sources':['saved']},prefill=False)
        with patch('leakscope.phase3.expansion_sources.collect',return_value={'candidates':[{'domain':'known.net','evidence':[]}]}):run_expansion(str(exp.pk))
        self.assertFalse(exp.candidates.exists())
