import json
from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3 import relationships as store


class PassiveEngineTests(TestCase):
    def start(self,**data):
        from leakscope.phase3.domain_expansion import start
        with patch('leakscope.phase3.domain_expansion.run_expansion.apply_async'):
            return start(data)

    def test_default_sources_use_passive_datasets_and_no_web(self):
        from leakscope.phase3.domain_expansion import normalize_spec
        spec=normalize_spec({'domains':['example.com']})
        self.assertEqual(set(spec['sources']),{'saved','certificates','crtsh','certspotter','hackertarget','urlscan','gleif','passive_dns'})
        self.assertEqual(spec['budgets']['web'],0)
        self.assertEqual(spec['budgets']['netlas'],0)
        self.assertEqual(spec['budgets']['public_ct'],20)

    def test_public_sources_have_grouped_demo_budgets(self):
        from leakscope.phase3.domain_expansion import normalize_spec
        spec=normalize_spec({'domains':['example.com'],'sources':['crtsh','certspotter','urlscan','gleif'],'demo':True})
        for bucket in ['public_ct','domain_api','org_api']:self.assertEqual(spec['budgets'][bucket],1)

    def test_domains_are_not_counted_with_website_dependencies(self):
        from leakscope.phase3.domain_expansion import detail,run_expansion
        exp=self.start(domains=['example.com'],sources=['saved'],max_depth=0)
        result={'candidates':[{'domain':'docs.example.com','relation':'service_provider','evidence':[{'source':'firecrawl','signal':'service_dependency','strength':10}]},
            {'domain':'cdn.other.net','relation':'service_provider','evidence':[{'source':'firecrawl','signal':'service_dependency','strength':10}]},
            {'domain':'news.net','relation':'related','evidence':[{'source':'firecrawl','signal':'web_link','strength':25}]},
            {'domain':'acquiredbrand.net','relation':'acquired_brand','evidence':[{'source':'firecrawl','signal':'acquisition_statement','strength':65}]}]}
        with patch('leakscope.phase3.expansion_sources.collect',return_value=result):run_expansion(str(exp.pk))
        exp.refresh_from_db();data=detail(exp)
        self.assertEqual(data['counts']['domains'],1);self.assertEqual(data['counts']['services'],1)
        self.assertEqual(data['counts']['references'],1);self.assertEqual(data['counts']['related'],1)
        self.assertEqual(next(c['relation'] for c in data['candidates'] if c['domain']=='docs.example.com'),'subdomain')

    def test_services_and_plain_links_do_not_become_recursive_seeds(self):
        from leakscope.phase3.domain_expansion import run_expansion
        exp=self.start(domains=['example.com'],sources=['saved'],max_depth=2)
        visited=[]
        def collect(source,seed,**kwargs):
            visited.append(seed['value'])
            return {'candidates':[{'domain':'external.net','relation':'related','evidence':[{'source':'firecrawl','signal':'web_link','strength':25}]},{'domain':'cdn.provider.net','relation':'service_provider','evidence':[]}]}
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=collect):run_expansion(str(exp.pk))
        self.assertEqual(visited,['example.com'])

    def test_origin_and_certificate_dates_survive_evidence_normalization(self):
        from leakscope.phase3.domain_expansion import _safe_evidence
        result=_safe_evidence([{'source':'crtsh','signal':'certificate_name','evidence_id':'id1','certificate_id':'123','not_before':'2020-01-01T00:00:00Z','not_after':'2021-01-01T00:00:00Z','record_type':'A','addresses':['1.1.1.1','invalid']}])
        self.assertEqual(result[0]['certificate_id'],'123');self.assertEqual(result[0]['evidence_id'],'id1')
        self.assertEqual(result[0]['addresses'],['1.1.1.1']);self.assertEqual(result[0]['not_after'],'2021-01-01T00:00:00Z')

    def test_unsupported_company_leads_are_recorded_without_automatic_expansion(self):
        from leakscope.phase3.domain_expansion import run_expansion,serialize
        exp=self.start(companies=['Example Corp'],sources=['saved'],max_depth=2)
        visited=[]
        def collect(source,seed,**kwargs):
            visited.append(seed['value']);return {'candidates':[],'leads':[{'kind':'company','value':'Publisher Media','ambiguous':True,'evidence':[]}]}
        with patch('leakscope.phase3.expansion_sources.collect',side_effect=collect):run_expansion(str(exp.pk))
        exp.refresh_from_db();self.assertEqual(visited,['Example Corp']);self.assertEqual(serialize(exp)['leads'][0]['value'],'Publisher Media')

    def test_new_evidence_does_not_accept_misleading_firstparty_labels(self):
        from leakscope.phase3.domain_expansion import run_expansion,detail
        exp=self.start(domains=['example.com'],sources=['saved'],max_depth=0)
        with patch('leakscope.phase3.expansion_sources.collect',return_value={'candidates':[{'domain':'www.tiktok.com','relation':'subdomain','evidence':[{'source':'saved','signal':'domain_structure','strength':50}]}]}):run_expansion(str(exp.pk))
        exp.refresh_from_db();self.assertNotEqual(detail(exp)['candidates'][0]['category'],'domains')

    def test_optional_domain_api_credentials_are_encrypted_and_redacted(self):
        from leakscope.services.config import get_config,redact
        from leakscope_app.models import IntegrationProfile
        response=self.client.post('/api/intelligence/settings',json.dumps({'certspotter':{'api_key':'fixture-cert-key'},'urlscan':{'api_key':'fixture-url-key'}}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertNotIn('fixture-cert-key',str(response.content));self.assertTrue(response.json()['domain_sources']['certspotter']['key_configured'])
        self.assertNotIn('fixture-cert-key',IntegrationProfile.objects.get(name='certspotter').encrypted_secret)
        self.assertNotIn('fixture-cert-key',redact('fixture-cert-key'))

    def test_company_only_defaults_include_bounded_identity_search(self):
        from leakscope.phase3.domain_expansion import normalize_spec
        self.assertIn('web',normalize_spec({'companies':['Example Corp']})['sources'])
        self.assertNotIn('web',normalize_spec({'companies':['Example Corp'],'sources':['gleif']})['sources'])

    def test_service_acceptance_does_not_seed_provider_domains(self):
        from leakscope.phase3.domain_expansion import run_expansion,review
        from leakscope_app.models import DomainCandidate,AssetRelationship
        org=store.node('organization','Example Corp')
        exp=self.start(organization_id=str(org.pk),domains=['example.com'],sources=['saved'],max_depth=0)
        c=DomainCandidate.objects.create(expansion=exp,domain='cdn.vendor.net',root='vendor.net',relation='service_provider',evidence=[{'source':'firecrawl','signal':'service_dependency','strength':10}])
        review(exp.pk,{'action':'accept','candidate_ids':[str(c.pk)]})
        self.assertFalse(AssetRelationship.objects.filter(source=org,target__value=c.domain,relation='user_scope').exists())
        self.assertTrue(AssetRelationship.objects.filter(source=org,target__value=c.domain,relation='uses_service').exists())
        follow=self.start(organization_id=str(org.pk),sources=['saved'])
        self.assertNotIn('cdn.vendor.net',[s['value'] for s in follow.spec['seeds']])

    def test_focused_company_preserves_org_without_parent_reseeding(self):
        org=store.node('organization','Parent Holdings')
        exp=self.start(organization_id=str(org.pk),companies=['Different Brand'],focused=True,sources=['gleif'])
        self.assertEqual(exp.organization_id,org.pk)
        self.assertEqual([s['value'] for s in exp.spec['seeds']],['Different Brand'])

    def test_public_dataset_queried_once_for_each_root(self):
        from leakscope.phase3.domain_expansion import run_expansion
        exp=self.start(domains=['api.example.com'],sources=['crtsh'],max_depth=2)
        calls=[]
        def collect(source,seed,*,spend,**kwargs):
            self.assertTrue(spend('public_ct'));calls.append(seed['value'])
            return {'candidates':[{'domain':'other.example.com','relation':'subdomain','evidence':[{'source':'crtsh','signal':'historical_certificate_name','strength':30}]}]}
        with patch('leakscope.phase3.passive_sources.collect',side_effect=collect):run_expansion(str(exp.pk))
        self.assertEqual(len(calls),1)

    def test_successful_bounded_page_is_cached_with_coverage_notes(self):
        from leakscope.phase3.domain_expansion import run_expansion
        exp=self.start(domains=['example.com'],sources=['certspotter'],max_depth=0)
        def collect(source,seed,*,spend,**kwargs):
            self.assertTrue(spend('public_ct'));return {'candidates':[],'cacheable':True,'notes':['First page only']}
        with patch('leakscope.phase3.passive_sources.collect',side_effect=collect):run_expansion(str(exp.pk))
        again=self.start(domains=['example.com'],sources=['certspotter'],max_depth=0)
        with patch('leakscope.phase3.passive_sources.collect',side_effect=AssertionError('cached')):run_expansion(str(again.pk))
        again.refresh_from_db();self.assertEqual(again.usage['public_ct'],0);self.assertIn('First page only',again.notes)

    def test_accepted_scope_bootstraps_company_only_and_scoped_index_records_stay_visible(self):
        from leakscope.phase3.domain_expansion import category,run_expansion
        org=store.node('organization','Example Corp')
        store.relation(org,store.node('domain','example.com'),'user_scope',store.evidence(org,'user_scope',{'domain':'example.com'}))
        exp=self.start(companies=['Example Corp'],sources=['saved'],max_depth=1)
        run_expansion(str(exp.pk));self.assertTrue(exp.candidates.filter(domain='example.com').exists())
        self.assertEqual(category(exp.spec,'example.com','related',[{'signal':'user_scope'}]),'related')
        self.assertEqual(category(exp.spec,'api.example.com','related',[{'signal':'passive_host_record'}]),'related')

    def test_index_cache_reuses_same_root_from_different_seed_hosts(self):
        from leakscope.phase3.domain_expansion import run_expansion
        exp=self.start(domains=['api.example.com'],sources=['crtsh'],max_depth=0)
        def collect(source,seed,*,spend,**kwargs):
            self.assertTrue(spend('public_ct'));return {'candidates':[],'cacheable':True}
        with patch('leakscope.phase3.passive_sources.collect',side_effect=collect):run_expansion(str(exp.pk))
        again=self.start(domains=['www.example.com'],sources=['crtsh'],max_depth=0)
        with patch('leakscope.phase3.passive_sources.collect',side_effect=AssertionError('same root should use cache')):run_expansion(str(again.pk))
        again.refresh_from_db();self.assertEqual(again.usage['public_ct'],0);self.assertFalse(again.partial)

    def test_non_scope_members_count_and_detach_on_organization_delete(self):
        from leakscope.phase3.organization_lifecycle import counts,set_status
        from leakscope_app.models import AssetNode,AssetRelationship,AssetEvidence
        org=store.node('organization','Example Corp')
        target=store.node('domain','provider.net')
        proof=store.evidence(org,'domain_expansion_review',{'association':'uses_service'})
        store.relation(org,target,'uses_service',proof)
        self.assertEqual(counts(org)['members'],1)
        set_status(org.pk,'delete',org.value)
        self.assertFalse(AssetRelationship.objects.filter(source=org,relation='uses_service').exists())
        self.assertTrue(AssetNode.objects.filter(pk=target.pk).exists());self.assertTrue(AssetEvidence.objects.filter(pk=proof.pk).exists())

    def test_shared_certificate_references_are_normalized_on_cache_replay(self):
        from leakscope.phase3.domain_expansion import run_expansion,detail
        from leakscope_app.models import DomainSourceCache
        payload={'cacheable':True,'candidates':[{'domain':name,'relation':'related','evidence':[{'source':'certspotter','signal':'certificate_san','certificate_id':'wide','via':'example.com','strength':35}]} for name in ['status.example.com',*[f'status.tenant{n}.net' for n in range(12)]]]}
        exp=self.start(domains=['example.com'],sources=['certspotter'],max_depth=0)
        with patch('leakscope.phase3.passive_sources.collect',return_value=payload):run_expansion(str(exp.pk))
        cache=DomainSourceCache.objects.get();cache.data=payload;cache.save()
        again=self.start(domains=['example.com'],sources=['certspotter'],max_depth=0)
        with patch('leakscope.phase3.passive_sources.collect',side_effect=AssertionError('cache must be reused')):run_expansion(str(again.pk))
        again.refresh_from_db();data=detail(again)
        self.assertEqual(data['counts']['domains'],1);self.assertEqual(data['counts']['references'],12);self.assertEqual(data['counts']['related'],0)
        self.assertEqual(again.usage['public_ct'],0)
        proof=next(c for c in data['candidates'] if c['category']=='references')['evidence'][0]
        self.assertEqual(proof['certificate_root_count'],13)
