"""Offline collector contracts; no network or provider credits."""
import json
import time
from unittest.mock import patch, Mock
from django.test import TestCase
from django.utils import timezone

from leakscope.phase3 import relationships as store
from leakscope_app.models import CertificateRecord, CertificateName, ServiceObservation


class ExpansionSourceTests(TestCase):
    def setUp(self):
        self.used = {}
        self.maximum = {'web':10, 'dns':10, 'rdap':10, 'netlas':10}
        self.context = {'company_names':['Example Corp'], 'seed_domains':['example.com'], 'deadline':time.monotonic()+30}
        self.config = {'FIRECRAWL_API_URL':'https://firecrawl.example/v2', 'FIRECRAWL_API_KEY':'fixture-secret', 'NETLAS_API_KEY':'fixture-netlas'}

    def spend(self, bucket, units=1):
        if self.used.get(bucket,0)+units > self.maximum.get(bucket,0): return False
        self.used[bucket] = self.used.get(bucket,0)+units
        return True

    def collect(self, source, value='example.com', kind='domain'):
        from leakscope.phase3.expansion_sources import collect
        return collect(source, {'kind':kind,'value':value,'depth':0},config=self.config,spend=self.spend,context=self.context)

    def test_retained_certificates_include_unrelated_sans_and_preserve_wildcard(self):
        record=CertificateRecord.objects.create(key='1'*64, domains=['*.example.com','differentbrand.net','api.example.com'], observed_at=timezone.now())
        for name in record.domains: CertificateName.objects.create(certificate=record,name=name)
        other=CertificateRecord.objects.create(key='2'*64,domains=['notexample.com','excluded.net'])
        for name in other.domains: CertificateName.objects.create(certificate=other,name=name)
        result=self.collect('certificates','portal.example.com')
        self.assertEqual({c['domain'] for c in result['candidates']},{'*.example.com','differentbrand.net','api.example.com'})
        candidate=next(c for c in result['candidates'] if c['domain']=='differentbrand.net')
        self.assertEqual(candidate['evidence'][0]['signal'],'certificate_san')
        self.assertLessEqual(candidate['evidence'][0]['strength'],45)
        self.assertEqual(self.used,{})

    def test_certstream_company_name_does_not_guess_certificate_names(self):
        self.assertEqual(self.collect('certificates','Example Corp','company')['candidates'],[])
        self.assertEqual(self.used,{})

    def test_saved_graph_finds_domains_via_shared_address_without_claiming_ownership(self):
        root=store.node('domain','example.com'); sibling=store.node('domain','different.net'); ip=store.node('ip','192.0.2.1')
        proof=store.evidence(root,'dns',{'name':'example.com','type':'A','value':ip.value})
        store.relation(root,ip,'resolves_to',proof)
        other=store.evidence(sibling,'dns',{'name':sibling.value,'type':'A','value':ip.value})
        store.relation(sibling,ip,'resolves_to',other)
        result=self.collect('saved')
        candidate=next(c for c in result['candidates'] if c['domain']==sibling.value)
        self.assertEqual(candidate['evidence'][0]['signal'],'shared_infrastructure')
        self.assertLessEqual(candidate['evidence'][0]['strength'],20)
        self.assertNotEqual(candidate['relation'],'owns')

    def test_hidden_provider_observations_do_not_leak_into_saved_candidates(self):
        from leakscope.phase3.state import ingest_observation
        endpoint=ingest_observation('shodan',{'address':'192.0.2.2','port':8443,'metadata':{'hostnames':['example.com','hidden.net']}})
        endpoint.observations.update(hidden=True)
        self.assertNotIn('hidden.net',str(self.collect('saved')))

    @patch('leakscope.phase3.expansion_sources._post')
    def test_company_search_discovers_differently_named_brand_and_company_lead(self, post):
        post.return_value={'success':True,'data':{'web':[{'url':'https://differentbrand.net/about','title':'Different Brand', 'markdown':'Different Brand is a subsidiary of Example Corp. Visit https://differentbrand.net.', 'html':'<script type="application/ld+json">{"@type":"Organization","legalName":"Other Legal Holdings"}</script>'}]}}
        result=self.collect('web','Example Corp','company')
        item=next(c for c in result['candidates'] if c['domain']=='differentbrand.net')
        self.assertEqual(item['relation'],'subsidiary')
        self.assertTrue(item['evidence'][0]['url'].startswith('https://differentbrand.net'))
        self.assertIn({'kind':'company','value':'Other Legal Holdings'},result['leads'])
        self.assertEqual(self.used,{'web':1})
        self.assertEqual(post.call_count,1)

    @patch('leakscope.phase3.expansion_sources._post')
    def test_domain_scrape_extracts_external_links_and_identifier_leads(self,post):
        post.side_effect=[{'success':True,'data':{'markdown':'Our other brand [Different](https://different.net).', 'html':'<a href="https://different.net">Our brand</a><script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABCD123"></script>', 'metadata':{'sourceURL':'https://example.com'}}},{'success':True,'data':{'web':[]}}]
        result=self.collect('web')
        self.assertIn('different.net',[c['domain'] for c in result['candidates']])
        self.assertTrue(any(lead['kind']=='identifier' and lead['value']=='GTM-ABCD123' and lead.get('evidence') for lead in result['leads']))
        self.assertEqual(self.used,{'web':2})
        self.assertEqual(post.call_count,2)

    @patch('leakscope.phase3.expansion_sources._post')
    def test_shared_cdn_and_analytics_host_are_service_providers(self,post):
        post.side_effect=[{'success':True,'data':{'html':'<script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABCD123"></script><img src="https://d123.cloudfront.net/p.png">'}},{'data':{'web':[]}}]
        result=self.collect('web')
        item=next(c for c in result['candidates'] if c['domain']=='d123.cloudfront.net')
        self.assertEqual(item['relation'],'service_provider')
        self.assertLessEqual(item['evidence'][0]['strength'],15)

    @patch('leakscope.phase3.expansion_sources._post')
    def test_web_no_budget_means_no_call(self,post):
        self.maximum['web']=0
        result=self.collect('web')
        post.assert_not_called()
        self.assertTrue(result['notes'])

    @patch('leakscope.phase3.expansion_sources._post')
    def test_web_one_budget_does_not_start_followup_search(self,post):
        self.maximum['web']=1
        post.return_value={'data':{'markdown':'hello'}}
        self.collect('web')
        self.assertEqual(post.call_count,1)
        self.assertEqual(self.used,{'web':1})

    @patch('leakscope.phase3.expansion_sources._post')
    def test_identifier_search_is_weak_and_must_contain_identifier(self,post):
        post.return_value={'data':{'web':[{'url':'https://other.net','markdown':'GTM-ABCD123 is present'},{'url':'https://unrelated.net','markdown':'not related'}]}}
        result=self.collect('web','GTM-ABCD123','identifier')
        self.assertEqual([c['domain'] for c in result['candidates']],['other.net'])
        self.assertEqual(result['candidates'][0]['evidence'][0]['signal'],'shared_identifier')
        self.assertLessEqual(result['candidates'][0]['evidence'][0]['strength'],35)

    @patch('leakscope.phase3.expansion_sources._post')
    def test_web_redacts_secrets_and_rejects_private_or_credential_links(self,post):
        post.side_effect=[{'data':{'markdown':'fixture-secret https://127.0.0.1/x https://user:pass@bad.net/a https://good.net/x', 'html':'<a href="javascript:alert(1)">bad</a>'}},{'data':{'web':[]}}]
        result=self.collect('web')
        self.assertNotIn('fixture-secret',str(result))
        self.assertNotIn('127.0.0.1',[c['domain'] for c in result['candidates']])
        self.assertNotIn('bad.net',[c['domain'] for c in result['candidates']])
        self.assertIn('good.net',[c['domain'] for c in result['candidates']])

    @patch('leakscope.phase3.expansion_sources._post')
    def test_malformed_firecrawl_payload_has_safe_note(self,post):
        post.return_value={'success':False,'error':'fixture-secret'}
        result=self.collect('web','Example Corp','company')
        self.assertEqual(result['candidates'],[])
        self.assertTrue(result['notes'])
        self.assertNotIn('fixture-secret',str(result))

    @patch('leakscope.phase3.expansion_sources.dns.resolver.Resolver')
    def test_dns_queries_are_metered_and_cname_is_service_relationship(self,resolver):
        answer=Mock();answer.rrset=Mock();answer.rrset.ttl=300
        item=Mock();item.target='vendor.net.';item.to_text.return_value='vendor.net.'
        answer.__iter__=Mock(return_value=iter([item]))
        resolver.return_value.resolve.return_value=answer
        self.maximum['dns']=1
        result=self.collect('dns')
        self.assertEqual(resolver.return_value.resolve.call_count,1)
        self.assertEqual(self.used,{'dns':1})
        self.assertEqual(result['candidates'][0]['relation'],'service_provider')
        self.assertEqual(result['candidates'][0]['domain'],'vendor.net')

    @patch('leakscope.phase3.expansion_sources.free_registry._http_once')
    def test_rdap_meters_bootstrap_authority_and_each_redirect(self,http):
        from leakscope.phase3 import free_registry
        with free_registry._cache_lock: free_registry._cache.clear()
        http.side_effect=[(200,{},json.dumps({'services':[[['com'],['https://rdap.registry.example/']]]}).encode()),(302,{'location':'/domain/example.com?x=1'},b''),(200,{},json.dumps({'ldhName':'EXAMPLE.COM','entities':[{'roles':['registrant'],'vcardArray':['vcard',[['fn',{},'text','Example Corp']]]}]}).encode())]
        result=self.collect('registration')
        self.assertEqual(self.used,{'rdap':3})
        self.assertEqual(http.call_count,3)
        self.assertEqual(result['candidates'][0]['domain'],'example.com')
        self.assertIn({'kind':'company','value':'Example Corp'},result['leads'])

    @patch('leakscope.phase3.expansion_sources.free_registry._http_once')
    def test_rdap_refuses_redirect_when_budget_exhausted(self,http):
        from leakscope.phase3 import free_registry
        with free_registry._cache_lock: free_registry._cache.clear()
        self.maximum['rdap']=1
        http.return_value=(302,{'location':'https://data.iana.org/rdap/other.json'},b'')
        result=self.collect('registration')
        self.assertEqual(http.call_count,1)
        self.assertTrue(result['notes'])

    @patch('leakscope.phase3.expansion_sources._request')
    def test_netlas_only_company_name_and_single_metered_first_page(self,request):
        request.return_value={'items':[{'data':{'domain':'different.net','registrant':{'organization':'Example Corp'}}}]}
        result=self.collect('netlas','Example Corp','company')
        self.assertEqual(self.used,{'netlas':1})
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['candidates'][0]['domain'],'different.net')
        self.assertEqual(request.call_args.args[1],'/whois_domains/')
        self.assertEqual(request.call_args.kwargs['params']['start'],0)

    @patch('leakscope.phase3.expansion_sources._request')
    def test_netlas_zero_budget_does_not_spend(self,request):
        self.maximum['netlas']=0
        result=self.collect('netlas','Example Corp','company')
        request.assert_not_called()
        self.assertEqual(result['candidates'],[])

    @patch('leakscope.phase3.expansion_sources.dns.resolver.Resolver')
    def test_variants_generate_unverified_candidates_without_queries(self,resolver):
        result=self.collect('variants')
        self.assertIn('example.net',[c['domain'] for c in result['candidates']])
        self.assertTrue(all(c['relation']=='lookalike' for c in result['candidates']))
        self.assertTrue(all(c['evidence'][0]['strength']<=10 for c in result['candidates']))
        self.assertLessEqual(len(result['candidates']),24)
        self.assertEqual(self.used,{})
        resolver.assert_not_called()

    @patch('leakscope.phase3.expansion_sources._post')
    def test_deadline_stops_network_before_request(self,post):
        self.context['deadline']=time.monotonic()-1
        result=self.collect('web')
        post.assert_not_called()
        self.assertTrue(result['notes'])

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):self.collect('arbitrary')

    def test_saved_does_not_expose_hidden_subdomain_nodes(self):
        from leakscope.phase3.state import ingest_observation
        endpoint=ingest_observation('shodan',{'address':'192.0.2.9','port':8443,'metadata':{'hostnames':['hidden.example.com']}})
        endpoint.observations.update(hidden=True)
        self.assertNotIn('hidden.example.com',str(self.collect('saved')))

    @patch('leakscope.phase3.expansion_sources._post')
    def test_news_page_is_not_classified_as_acquired_company(self,post):
        post.return_value={'data':{'web':[{'url':'https://news.net/news/acquisition','title':'Acquisition news','markdown':'Example Corp acquired Other Brand at https://different.net.'}]}}
        result=self.collect('web','Example Corp','company')
        source=next(item for item in result['candidates'] if item['domain']=='news.net')
        acquired=next(item for item in result['candidates'] if item['domain']=='different.net')
        self.assertEqual(source['relation'],'related')
        self.assertLessEqual(source['evidence'][0]['strength'],25)
        self.assertEqual(acquired['relation'],'acquired_brand')

    @patch('leakscope.phase3.expansion_sources._post')
    def test_demo_asks_firecrawl_for_one_result(self,post):
        self.context['demo']=True
        post.return_value={'data':{'web':[]}}
        self.collect('web','Example Corp','company')
        self.assertEqual(post.call_args.args[1]['limit'],1)

    @patch('leakscope.phase3.expansion_sources.free_registry._http_once')
    def test_registry_redirect_cannot_escape_authoritative_hosts(self,http):
        from leakscope.phase3 import free_registry
        with free_registry._cache_lock:free_registry._cache.clear()
        http.return_value=(302,{'location':'https://127.0.0.1/secrets'},b'')
        result=self.collect('registration')
        self.assertEqual(http.call_count,1)
        self.assertEqual(self.used,{'rdap':1})
        self.assertTrue(result['notes'])

    @patch('leakscope.phase3.expansion_sources.free_registry._http_once')
    def test_rdap_registrar_identity_is_never_company_owner(self,http):
        from leakscope.phase3 import free_registry
        with free_registry._cache_lock:free_registry._cache.clear()
        http.side_effect=[(200,{},json.dumps({'services':[[['com'],['https://rdap.registry.example/']]]}).encode()),(200,{},json.dumps({'ldhName':'example.com','entities':[{'roles':['registrar'],'vcardArray':['vcard',[['fn',{},'text','Example Corp']]]}]}).encode())]
        result=self.collect('registration')
        self.assertEqual(result['leads'],[])
        self.assertEqual(result['candidates'][0]['evidence'][0]['signal'],'registration_record')

    @patch('leakscope.phase3.expansion_sources._post')
    def test_page_links_to_legal_statement_discover_domain_without_url_prefix(self,post):
        post.side_effect=[{'data':{'markdown':'Example Corp operates Different Brand at different.net.','metadata':{'sourceURL':'https://example.com/legal'}}},{'data':{'web':[]}}]
        result=self.collect('web')
        self.assertIn('different.net',[c['domain'] for c in result['candidates']])

    @patch('leakscope.phase3.expansion_sources._request')
    def test_netlas_demo_retains_one_without_requesting_next_page(self,request):
        self.context['demo']=True
        request.return_value={'items':[{'data':{'domain':name,'registrant':{'organization':'Example Corp'}}} for name in ['one.net','two.net']]}
        result=self.collect('netlas','Example Corp','company')
        self.assertEqual(len(result['candidates']),1)
        self.assertEqual(request.call_count,1)

    @patch('leakscope.phase3.expansion_sources.dns.resolver.Resolver')
    def test_dns_positive_status_matches_persistent_candidate_contract(self,resolver):
        import dns.resolver
        item=Mock();item.to_text.return_value='8.8.8.8'
        answer=Mock();answer.__iter__=Mock(return_value=iter([item]))
        resolver.return_value.resolve.side_effect=[dns.resolver.NoAnswer(),answer]
        self.maximum['dns']=2
        result=self.collect('dns')
        self.assertEqual(result['candidates'][0]['dns_status'],'resolved')

    @patch('leakscope.phase3.expansion_sources._post')
    def test_raw_html_is_requested_and_preferred_for_identifiers(self,post):
        post.side_effect=[{'data':{'html':'<p>Cleaned page</p>','rawHtml':'<script src="https://www.googletagmanager.com/gtm.js?id=GTM-RAWD123"></script>'}},{'data':{'web':[]}}]
        result=self.collect('web')
        self.assertIn('rawHtml',post.call_args_list[0].args[1]['formats'])
        self.assertNotIn('scrapeOptions',post.call_args_list[1].args[1])
        self.assertTrue(any(lead['kind']=='identifier' and lead['value']=='GTM-RAWD123' and lead.get('evidence') for lead in result['leads']))

    @patch('leakscope.phase3.expansion_sources._post')
    def test_web_query_never_exceeds_documented_five_hundred_chars(self,post):
        self.context.update(country='C'*160,industry='I'*160)
        post.return_value={'data':{'web':[]}}
        self.collect('web','\\'*200,'company')
        self.assertLessEqual(len(post.call_args.args[1]['query']),500)

    @patch('leakscope.phase3.expansion_sources._post')
    def test_brand_statement_does_not_upgrade_every_unrelated_page_link(self,post):
        post.side_effect=[{'data':{'markdown':'Example Corp lists our brands at https://different.net.\nRead the unrelated article [Example](https://unrelated.net).'}},{'data':{'web':[]}}]
        result=self.collect('web')
        unrelated=next(c for c in result['candidates'] if c['domain']=='unrelated.net')
        self.assertTrue(all(p['strength']<=30 for p in unrelated['evidence']))

    @patch('leakscope.phase3.expansion_sources._post')
    def test_failed_homepage_scrape_does_not_prevent_planned_search(self,post):
        post.side_effect=[{'success':False},{'data':{'web':[{'url':'https://different.net','markdown':'Example Corp operates Different Brand.'}]}}]
        result=self.collect('web')
        self.assertEqual(post.call_count,2)
        self.assertEqual(self.used,{'web':2})
        self.assertIn('different.net',[c['domain'] for c in result['candidates']])
        self.assertTrue(result['notes'])

    @patch('leakscope.phase3.expansion_sources._post')
    def test_error_page_content_cannot_become_company_evidence(self,post):
        post.return_value={'data':{'web':[{'url':'https://different.net','markdown':'Example Corp is owned by Other Corp.','metadata':{'statusCode':403,'error':'fixture-secret'}}]}}
        result=self.collect('web','Example Corp','company')
        self.assertEqual(result['candidates'],[])
        self.assertTrue(result['notes'])
        self.assertNotIn('fixture-secret',str(result))

    @patch('leakscope.phase3.expansion_sources._post')
    def test_relationship_statement_must_name_source_site_before_upgrading_it(self,post):
        post.return_value={'data':{'web':[{'url':'https://publisher.net/reports/acquisition.html','title':'Company report','markdown':'Example Corp acquired Other Brand at https://different.net.'}]}}
        result=self.collect('web','Example Corp','company')
        publisher=next(item for item in result['candidates'] if item['domain']=='publisher.net')
        self.assertEqual(publisher['relation'],'related')
        self.assertTrue(all(p['strength']<=25 for p in publisher['evidence']))

    @patch('leakscope.phase3.expansion_sources.free_registry._http_once')
    def test_hosted_namespace_rdap_never_promotes_parent_provider_to_tenant_owner(self,http):
        from leakscope.phase3 import free_registry
        with free_registry._cache_lock:free_registry._cache.clear()
        self.context['company_names']=['GitHub Inc']
        http.side_effect=[(200,{},json.dumps({'services':[[['io'],['https://rdap.registry.example/']]]}).encode()),(200,{},json.dumps({'ldhName':'github.io','entities':[{'roles':['registrant'],'vcardArray':['vcard',[['fn',{},'text','GitHub Inc']]]}]}).encode())]
        result=self.collect('registration','tenant.github.io')
        self.assertEqual(result['leads'],[])
        self.assertEqual(result['candidates'],[])
        note=' '.join(result['notes'])
        self.assertIn('parent',note.lower())
        self.assertIn('tenant.github.io',note)
        self.assertIn('https://rdap.registry.example/domain/github.io',note)
        self.assertIn('observed',note.lower())

    @patch('leakscope.phase3.expansion_sources._post')
    def test_publisher_named_in_reporting_sentence_does_not_become_acquired_brand(self,post):
        post.return_value={'data':{'web':[{'url':'https://marketreport.net/2026/11/08/update','markdown':'Marketreport reports Example Corp acquired Other Brand at https://different.net.'}]}}
        result=self.collect('web','Example Corp','company')
        publisher=next(c for c in result['candidates'] if c['domain']=='marketreport.net')
        brand=next(c for c in result['candidates'] if c['domain']=='different.net')
        self.assertEqual(publisher['relation'],'related')
        self.assertLessEqual(publisher['evidence'][0]['strength'],25)
        self.assertEqual(brand['relation'],'acquired_brand')
        self.assertEqual(brand['evidence'][0]['signal'],'acquisition_statement')

    def test_expansion_outputs_cannot_be_replayed_as_independent_saved_evidence(self):
        org=store.node('organization','Example Corp')
        root=store.node('domain','example.com'); candidate=store.node('domain','ads.vendor.net')
        for node in (root,candidate):
            proof=store.evidence(node,'domain_expansion',{'expansion_id':'current','evidence':[{'excerpt':'GTM-ABCD123'}]})
            store.relation(org,node,'domain_candidate',proof)
        result=self.collect('saved')
        self.assertEqual(result['candidates'],[])
        self.assertEqual(result['leads'],[])

    def test_same_organization_membership_is_not_shared_infrastructure(self):
        org=store.node('organization','Example Corp')
        for name in ('example.com','separate.net'):
            proof=store.evidence(org,'user_scope',{'domain':name})
            store.relation(org,store.node('domain',name),'user_scope',proof)
        result=self.collect('saved')
        self.assertNotIn('separate.net',[c['domain'] for c in result['candidates']])
        by_company=self.collect('saved','Example Corp','company')
        member=next(c for c in by_company['candidates'] if c['domain']=='separate.net')
        self.assertTrue(all(p['signal']=='user_scope' for p in member['evidence']))

    def test_saved_fact_retains_original_identity_and_replays_without_new_proof(self):
        node=store.node('domain','api.example.com')
        proof=store.evidence(node,'dns',{'name':node.value,'type':'A','value':'192.0.2.12'},source_url='https://source.example/record/12')
        first=self.collect('saved');second=self.collect('saved')
        self.assertEqual(first,second)
        item=next(c for c in first['candidates'] if c['domain']==node.value)
        self.assertEqual(len(item['evidence']),1)
        original=item['evidence'][0]
        self.assertEqual(original['source'],'dns')
        self.assertEqual(original['via'],'evidence:'+str(proof.pk))
        self.assertEqual(original['url'],proof.source_url)
        self.assertEqual(original['observed_at'],proof.observed_at.isoformat())

    def test_saved_candidate_only_subdomain_nodes_are_not_facts(self):
        store.node('domain','orphan.example.com')
        self.assertNotIn('orphan.example.com',str(self.collect('saved')))

    @patch('leakscope.phase3.expansion_sources._post')
    def test_first_party_resources_and_canonical_links_are_not_third_party_services(self,post):
        post.side_effect=[{'data':{'html':'<link rel="canonical" href="https://www.example.com"><link rel="alternate" hreflang="fr" href="https://fr.example.com"><script src="https://go.example.com/js/forms.js"></script>'}},{'data':{'web':[]}}]
        result=self.collect('web')
        for name in ('www.example.com','fr.example.com','go.example.com'):
            with self.subTest(name=name):
                self.assertEqual(next(c for c in result['candidates'] if c['domain']==name)['relation'],'subdomain')

    @patch('leakscope.phase3.expansion_sources._post')
    def test_recursive_external_seed_cannot_relabel_vendor_family_as_first_party(self,post):
        post.side_effect=[{'data':{'markdown':'Vendor website https://www.vendor.net', 'html':'<script src="https://cdn.vendor.net/sdk.js"></script><a href="https://help.vendor.net/">Help</a>'}},{'data':{'web':[]}}]
        result=self.collect('web','vendor.net')
        self.assertTrue(all(c['relation']!='subdomain' for c in result['candidates']))
        self.assertEqual(next(c for c in result['candidates'] if c['domain']=='cdn.vendor.net')['relation'],'service_provider')

    def test_accepted_expansion_review_is_scope_not_a_new_identifier_observation(self):
        org=store.node('organization','Example Corp'); name=store.node('domain','accepted.net')
        proof=store.evidence(org,'domain_expansion_review',{'domain':name.value,'evidence':[{'excerpt':'GTM-ABCD123'}]})
        store.relation(org,name,'user_scope',proof)
        result=self.collect('saved','Example Corp','company')
        self.assertEqual([c['domain'] for c in result['candidates']],['accepted.net'])
        self.assertEqual(result['leads'],[])
        self.assertEqual(result['candidates'][0]['evidence'][0]['signal'],'user_scope')
        self.assertEqual(result['candidates'][0]['evidence'][0]['evidence_id'],str(proof.pk))

    def test_original_labeled_proof_from_expansion_run_is_still_derived(self):
        from leakscope_app.models import Run
        run=Run.objects.create(kind='domain_expansion')
        node=store.node('domain','derived.example.com')
        store.evidence(node,'dns',{'evidence':[{'excerpt':'GTM-ABCD123'}]},run=run)
        self.assertEqual(self.collect('saved')['candidates'],[])

    def test_recursive_certificate_pivot_does_not_change_original_namespace(self):
        record=CertificateRecord.objects.create(key='recursive-cert',domains=['vendor.net','help.vendor.net'])
        for name in record.domains:CertificateName.objects.create(certificate=record,name=name)
        result=self.collect('certificates','vendor.net')
        self.assertTrue(result['candidates'])
        self.assertTrue(all(c['relation']!='subdomain' for c in result['candidates']))
        self.assertEqual(result['candidates'][0]['evidence'][0]['certificate_id'],str(record.pk))
