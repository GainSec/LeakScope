"""Passive lookup adapters never scan target services or spend paid provider keys."""
import json
import time
from unittest.mock import Mock, patch
from django.test import SimpleTestCase


class PassiveSourceTests(SimpleTestCase):
    def setUp(self):
        self.used={};self.maximum={'public_ct':10,'domain_api':10,'org_api':10}
        self.context={'seed_domains':['example.com'],'company_names':['Example Corp'],'deadline':time.monotonic()+30}

    def spend(self,key,units=1):
        if self.used.get(key,0)+units>self.maximum[key]:return False
        self.used[key]=self.used.get(key,0)+units;return True

    def collect(self,source,value='example.com',kind='domain',config=None):
        from leakscope.phase3.passive_sources import collect
        return collect(source,{'kind':kind,'value':value,'depth':0},config=config or {},spend=self.spend,context=self.context)

    @patch('leakscope.phase3.passive_sources._request')
    def test_crtsh_retains_historical_scoped_names_and_wildcards(self,request):
        request.return_value=[{'id':42,'name_value':'*.example.com\napi.example.com\napi.example.com\nother.net','not_before':'2019-01-01T00:00:00','not_after':'2020-01-01T00:00:00','entry_timestamp':'2019-01-01T00:01:00'}]
        result=self.collect('crtsh')
        self.assertEqual({c['domain'] for c in result['candidates']},{'*.example.com','api.example.com','other.net'})
        url=request.call_args.args[0]
        self.assertIn('output=json',url);self.assertNotIn('exclude',url)
        proof=result['candidates'][0]['evidence'][0]
        self.assertEqual(proof['not_after'],'2020-01-01T00:00:00');self.assertEqual(proof['certificate_id'],'42')

    @patch('leakscope.phase3.passive_sources._request')
    def test_certspotter_keeps_full_san_cross_domain_as_weak(self,request):
        request.return_value=[{'id':'12','dns_names':['api.example.com','otherbrand.net','*.otherbrand.net'],'not_before':'2026-01-01','not_after':'2027-01-01'}]
        result=self.collect('certspotter')
        item=next(c for c in result['candidates'] if c['domain']=='otherbrand.net')
        self.assertEqual(item['relation'],'related');self.assertEqual(item['evidence'][0]['signal'],'certificate_san')
        self.assertLessEqual(item['evidence'][0]['strength'],35)
        self.assertTrue(result['notes']);self.assertTrue(result['cacheable']);self.assertEqual(request.call_count,1)

    @patch('leakscope.phase3.passive_sources._request')
    def test_hostsearch_scope_and_validated_address_only(self,request):
        request.return_value='api.example.com,192.0.2.1\nnotexample.com,192.0.2.2\nother.net,192.0.2.3\ninvalid.example.com,not-ip\n'
        result=self.collect('hackertarget')
        self.assertEqual([c['domain'] for c in result['candidates']],['api.example.com'])
        self.assertEqual(result['candidates'][0]['evidence'][0]['addresses'],['192.0.2.1'])
        self.assertEqual(request.call_count,1)

    @patch('leakscope.phase3.passive_sources._request')
    def test_urlscan_uses_existing_index_never_resource_domains(self,request):
        request.return_value={'results':[{'_id':'a'*32,'page':{'domain':'api.example.com','ip':'192.0.2.1'},'task':{'domain':'example.com','time':'2026-01-01'},'resources':[{'domain':'cdn.vendor.net'}]}],'total':1}
        result=self.collect('urlscan')
        self.assertEqual({c['domain'] for c in result['candidates']},{'api.example.com','example.com'})
        self.assertNotIn('cdn.vendor.net',str(result));self.assertIn('/search/',request.call_args.args[0])

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_extracts_exact_aliases_and_direct_children_in_two_calls(self,request):
        parent={'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corporation'},'otherNames':[{'name':'Prior Brand'}]}}}
        child={'type':'lei-records','id':'B'*18+'12','attributes':{'entity':{'legalName':{'name':'Unrelated Name LLC'}}}}
        request.side_effect=[{'data':[parent],'links':{}},{'data':[child],'links':{}}]
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(result['candidates'],[]);self.assertEqual(request.call_count,2)
        self.assertIn('/direct-children',request.call_args.args[0])
        leads={lead['value']:lead for lead in result['leads']}
        self.assertEqual(leads['Prior Brand']['evidence'][0]['signal'],'legal_entity_alias')
        self.assertEqual(leads['Unrelated Name LLC']['evidence'][0]['signal'],'legal_entity_child')
        self.assertEqual(leads['Unrelated Name LLC']['evidence'][0]['source_record_id'],child['id'])

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_ambiguous_results_never_follow_or_assert_subsidiary(self,request):
        request.return_value={'data':[{'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Partner Holdings'}}}}]}
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,1)
        self.assertTrue(result['leads'][0]['ambiguous'])
        self.assertEqual(result['leads'][0]['evidence'][0]['signal'],'legal_entity_search')

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_demo_never_fetches_relationships(self,request):
        self.context['demo']=True
        request.return_value={'data':[{'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'}}}}]}
        self.collect('gleif','Example Corp','company');self.assertEqual(request.call_count,1)
        self.assertIn('page%5Bsize%5D=1',request.call_args.args[0])

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_transport_charges_each_real_get_and_never_redirects(self,get):
        response=Mock(status_code=200,headers={});response.iter_content.return_value=[b'[]'];get.return_value=response
        result=self.collect('crtsh')
        self.assertEqual(self.used,{'public_ct':1});self.assertEqual(get.call_count,1)
        self.assertFalse(get.call_args.kwargs['allow_redirects']);self.assertTrue(get.call_args.kwargs['stream'])
        response.close.assert_called_once();self.assertTrue(result['cacheable'])

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_zero_budget_prevents_all_network_calls(self,get):
        self.maximum['public_ct']=0
        result=self.collect('crtsh');get.assert_not_called();self.assertTrue(result['notes']);self.assertFalse(result['cacheable'])

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_rate_limit_is_partial_not_empty_complete_and_not_retried(self,get):
        get.return_value=Mock(status_code=429,headers={'Retry-After':'60'})
        result=self.collect('certspotter',config={'CERTSPOTTER_API_KEY':'private-secret'})
        self.assertEqual(result['candidates'],[]);self.assertTrue(result['notes']);self.assertFalse(result['cacheable'])
        self.assertEqual(get.call_count,1);self.assertIn('429',str(result));self.assertNotIn('private-secret',str(result))

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_oversized_response_stops_stream_and_reports_partial(self,get):
        response=Mock(status_code=200,headers={});response.iter_content.return_value=[b'a'*(2*1024*1024),b'b'];get.return_value=response
        result=self.collect('crtsh');self.assertFalse(result['cacheable']);self.assertTrue(result['notes']);response.close.assert_called_once()

    @patch('leakscope.phase3.passive_sources._request')
    def test_invalid_seed_does_not_reach_provider(self,request):
        for seed in ['https://example.com','not-a-domain','127.0.0.1','com']:
            with self.subTest(seed=seed), self.assertRaises(ValueError):self.collect('crtsh',seed)
        request.assert_not_called()

    @patch('leakscope.phase3.passive_sources._request')
    def test_multiple_exact_legal_entities_remain_ambiguous(self,request):
        request.return_value={'data':[{'type':'lei-records','id':letter*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'}}}} for letter in ('A','B')]}
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,1)
        self.assertTrue(all(lead['ambiguous'] for lead in result['leads']))
        self.assertTrue(all(lead['evidence'][0]['signal']=='legal_entity_search' for lead in result['leads']))

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_invalid_credential_does_not_consume_network_budget(self,get):
        result=self.collect('certspotter',config={'CERTSPOTTER_API_KEY':'invalid\nvalue'})
        get.assert_not_called();self.assertEqual(self.used,{})
        self.assertFalse(result['cacheable']);self.assertNotIn('invalid\nvalue',str(result))

    @patch('leakscope.phase3.passive_sources.requests.get')
    @patch('leakscope.phase3.passive_sources.time.monotonic')
    def test_streaming_has_twenty_second_total_deadline_even_when_job_allows_longer(self,clock,get):
        times=iter([100]);clock.side_effect=lambda:next(times,121)
        response=Mock(status_code=200,headers={});response.iter_content.return_value=[b'[]'];get.return_value=response
        self.context['deadline']=200
        result=self.collect('crtsh')
        self.assertFalse(result['cacheable']);self.assertIn('deadline',str(result).lower());response.close.assert_called_once()

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_certspotter_optional_key_only_in_header(self,get):
        response=Mock(status_code=200,headers={});response.iter_content.return_value=[b'[]'];get.return_value=response
        result=self.collect('certspotter',config={'CERTSPOTTER_API_KEY':'private-secret'})
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'],'Bearer private-secret')
        self.assertNotIn('private-secret',get.call_args.args[0]);self.assertNotIn('private-secret',str(result))

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_redirect_is_not_followed_or_retried(self,get):
        get.return_value=Mock(status_code=302,headers={'Location':'https://attacker.example/secret'})
        result=self.collect('urlscan');self.assertFalse(result['cacheable']);self.assertEqual(get.call_count,1)
        self.assertNotIn('attacker',str(result));self.assertIn('redirect',str(result).lower())

    @patch('leakscope.phase3.passive_sources._request')
    def test_pagination_links_are_reported_but_never_followed(self,request):
        request.return_value={'results':[{'page':{'domain':'api.example.com'}}],'total':20,'has_more':True,'links':{'next':'https://attacker.example'}}
        result=self.collect('urlscan');self.assertEqual(request.call_count,1);self.assertTrue(result['notes'])
        self.assertNotIn('attacker',str(result))

    @patch('leakscope.phase3.passive_sources._request')
    def test_expired_certificates_are_not_silently_dropped(self,request):
        request.return_value=[{'id':1,'name_value':'old.example.com','not_after':'2001-01-01T00:00:00'}]
        result=self.collect('crtsh');self.assertEqual(result['candidates'][0]['domain'],'old.example.com')
        self.assertEqual(result['candidates'][0]['evidence'][0]['record_type'],'certificate')

    @patch('leakscope.phase3.passive_sources._request')
    def test_unrelated_page_redirect_domain_is_not_owned_candidate(self,request):
        request.return_value={'results':[{'page':{'domain':'login.vendor.net'},'task':{'domain':'api.example.com'}}]}
        result=self.collect('urlscan');self.assertEqual([c['domain'] for c in result['candidates']],['api.example.com'])

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_never_fabricates_website_from_entity_names(self,request):
        self.context['demo']=True
        request.return_value={'data':[{'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'},'website':'https://untrusted.net'}}}]}
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(result['candidates'],[]);self.assertNotIn('untrusted.net',str(result))

    @patch('leakscope.phase3.passive_sources._request')
    def test_certspotter_foreign_issuance_cannot_seed_cross_domain_pivots(self,request):
        request.return_value=[{'id':'foreign','dns_names':['unrelated.net','*.elsewhere.org']}, {'id':'valid','dns_names':['api.example.com','sibling-brand.net']}]
        result=self.collect('certspotter')
        names={candidate['domain'] for candidate in result['candidates']}
        self.assertEqual(names,{'api.example.com','sibling-brand.net'})
        self.assertFalse(result['cacheable'])
        self.assertTrue(any('scope' in note.lower() or 'unrelated' in note.lower() for note in result['notes']))

    @patch('leakscope.phase3.passive_sources._request')
    def test_inactive_entity_is_preserved_without_current_relationship_expansion(self,request):
        request.return_value={'data':[{'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'},'status':'INACTIVE'},'registration':{'status':'RETIRED','lastUpdateDate':'2020-01-01'}}}]}
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,1)
        self.assertTrue(result['leads']);self.assertTrue(all(lead['ambiguous'] for lead in result['leads']))
        proof=result['leads'][0]['evidence'][0]
        self.assertEqual(proof['entity_status'],'INACTIVE');self.assertEqual(proof['registration_status'],'RETIRED')
        self.assertEqual(proof['last_seen'],'2020-01-01')

    @patch('leakscope.phase3.passive_sources._request')
    def test_lapsed_lei_does_not_claim_business_ceased_or_fetch_current_children(self,request):
        request.return_value={'data':[{'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'},'status':'ACTIVE'},'registration':{'status':'LAPSED'}}}]}
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['leads'][0]['evidence'][0]['registration_status'],'LAPSED')
        self.assertTrue(result['leads'][0]['ambiguous'])
        self.assertNotIn('ceased',str(result).lower())

    @patch('leakscope.phase3.passive_sources._request')
    def test_hostsearch_ipv6_address_preserved_without_active_resolution(self,request):
        request.return_value='api.example.com,2001:db8::1\n'
        result=self.collect('hackertarget')
        self.assertEqual(result['candidates'][0]['evidence'][0]['addresses'],['2001:db8::1'])
        self.assertEqual(self.used,{})

    @patch('leakscope.phase3.passive_sources._request')
    def test_inactive_child_is_historical_review_lead_not_automatic_subsidiary_seed(self,request):
        parent={'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'},'status':'ACTIVE'},'registration':{'status':'ISSUED'}}}
        child={'type':'lei-records','id':'B'*18+'12','attributes':{'entity':{'legalName':{'name':'Old Brand'},'status':'INACTIVE'},'registration':{'status':'RETIRED'}}}
        request.side_effect=[{'data':[parent]},{'data':[child]}]
        result=self.collect('gleif','Example Corp','company')
        lead=next(lead for lead in result['leads'] if lead['value']=='Old Brand')
        self.assertTrue(lead['ambiguous']);self.assertEqual(lead['relation'],'historical')
        self.assertNotEqual(lead['evidence'][0]['signal'],'legal_entity_child')

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_next_page_makes_first_page_exact_match_ambiguous(self,request):
        request.return_value={'data':[{'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'},'status':'ACTIVE'}}}], 'links':{'next':'https://api.gleif.org/api/v1/lei-records?page[number]=2'}}
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,1)
        self.assertTrue(all(lead['ambiguous'] for lead in result['leads']))
        self.assertTrue(all(lead['evidence'][0]['signal']=='legal_entity_search' for lead in result['leads']))

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_full_page_without_total_cannot_prove_exact_match_unique(self,request):
        rows=[{'type':'lei-records','id':str(index).zfill(18)+'12','attributes':{'entity':{'legalName':{'name':'Example Corp' if index==0 else 'Unrelated '+str(index)}}}} for index in range(100)]
        request.return_value={'data':rows}
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,1)
        self.assertTrue(all(lead['ambiguous'] for lead in result['leads']))
        self.assertTrue(any('page' in note.lower() for note in result['notes']))

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_full_page_explicit_complete_total_can_follow_unique_match(self,request):
        rows=[{'type':'lei-records','id':letter*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp' if index==0 else 'Unrelated '+letter}}}} for index,letter in enumerate('ABCDE')]
        request.side_effect=[{'data':rows,'meta':{'pagination':{'total':5}},'links':{'next':None}},{'data':[]}]
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,2)
        lead=next(lead for lead in result['leads'] if lead['value']=='Example Corp')
        self.assertFalse(lead['ambiguous'])

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_next_link_wins_over_inconsistent_complete_total(self,request):
        row={'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'}}}}
        request.return_value={'data':[row],'meta':{'pagination':{'total':1}},'links':{'next':'https://api.gleif.org/api/v1/lei-records?page[number]=2'}}
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,1)
        self.assertTrue(result['leads'][0]['ambiguous'])

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_total_larger_than_short_page_still_means_incomplete_search(self,request):
        row={'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'}}}}
        request.return_value={'data':[row],'meta':{'pagination':{'total':20}},'links':{}}
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,1);self.assertTrue(result['leads'][0]['ambiguous'])

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_free_text_country_cannot_silently_allow_unique_entity_expansion(self,request):
        row={'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'}}}}
        request.return_value={'data':[row]}
        for country in ('United States','USA'):
            with self.subTest(country=country):
                request.reset_mock();self.context['country']=country
                result=self.collect('gleif','Example Corp','company')
                self.assertEqual(request.call_count,1)
                self.assertTrue(all(lead['ambiguous'] for lead in result['leads']))
                self.assertTrue(all(lead['evidence'][0]['signal']=='legal_entity_search' for lead in result['leads']))
                self.assertTrue(any('ISO' in note and 'not applied' in note for note in result['notes']))

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_iso2_country_is_sent_as_filter(self,request):
        self.context['country']='us'
        request.return_value={'data':[]}
        self.collect('gleif','Example Corp','company')
        self.assertIn('filter%5Bentity.legalAddress.country%5D=US',request.call_args.args[0])

    @patch('leakscope.phase3.passive_sources._request')
    def test_high_fanout_certificate_keeps_names_as_shared_hosting_references(self,request):
        names=['api.example.com',*[f'brand{index}.net' for index in range(12)]]
        request.return_value=[{'id':'wide','dns_names':names}]
        result=self.collect('certspotter')
        self.assertEqual(len(result['candidates']),13)
        scoped=next(c for c in result['candidates'] if c['domain']=='api.example.com')
        reference=next(c for c in result['candidates'] if c['domain']=='brand0.net')
        self.assertEqual(scoped['evidence'][0]['signal'],'certificate_san')
        self.assertEqual(reference['relation'],'related')
        self.assertEqual(reference['evidence'][0]['signal'],'shared_hosting_certificate')
        self.assertEqual(reference['evidence'][0]['strength'],10)
        self.assertEqual(reference['evidence'][0]['certificate_root_count'],13)

    @patch('leakscope.phase3.passive_sources._request')
    def test_certificate_root_count_uses_full_issuance_before_candidate_limit(self,request):
        self.context['max_candidates']=2
        names=['api.example.com',*[f'brand{index}.net' for index in range(12)]]
        request.return_value=[{'id':'wide','dns_names':names}]
        result=self.collect('certspotter')
        self.assertEqual(len(result['candidates']),13)
        self.assertEqual(result['candidates'][1]['evidence'][0]['certificate_root_count'],13)
        self.assertEqual(result['candidates'][1]['evidence'][0]['signal'],'shared_hosting_certificate')

    @patch('leakscope.phase3.passive_sources._request')
    def test_narrow_two_root_certificate_remains_weak_cross_domain_lead(self,request):
        request.return_value=[{'id':'narrow','dns_names':['api.example.com','otherbrand.net','*.otherbrand.net']}]
        result=self.collect('certspotter')
        lead=next(c for c in result['candidates'] if c['domain']=='otherbrand.net')
        self.assertEqual(lead['evidence'][0]['signal'],'certificate_san')
        self.assertEqual(lead['evidence'][0]['strength'],35)
        self.assertEqual(lead['evidence'][0]['certificate_root_count'],2)

    def test_cached_certificate_result_can_be_reclassified_without_api_or_input_mutation(self):
        from leakscope.phase3.passive_sources import normalize_result
        names=['api.example.com',*[f'brand{index}.net' for index in range(12)]]
        cached={'candidates':[{'domain':name,'relation':'subdomain' if name=='api.example.com' else 'related','evidence':[{'source':'certspotter','certificate_id':'wide','via':'example.com','signal':'certificate_san','strength':35}]} for name in names], 'notes':[],'cacheable':True}
        with patch('leakscope.phase3.passive_sources.requests.get',side_effect=AssertionError('cache replay must not query providers')):
            result=normalize_result(cached)
        self.assertEqual(cached['candidates'][1]['evidence'][0]['signal'],'certificate_san')
        self.assertEqual(result['candidates'][0]['evidence'][0]['signal'],'certificate_san')
        self.assertEqual(result['candidates'][1]['evidence'][0]['signal'],'shared_hosting_certificate')
        self.assertEqual(result['candidates'][1]['evidence'][0]['certificate_root_count'],13)
        self.assertEqual(normalize_result(result),result)

    def test_cache_replay_preserves_known_full_root_count_when_only_two_names_retained(self):
        from leakscope.phase3.passive_sources import normalize_result
        cached={'candidates':[{'domain':name,'relation':'related','evidence':[{'source':'certspotter','certificate_id':'wide','via':'example.com','signal':'certificate_san','strength':35,'certificate_root_count':35}]} for name in ['api.example.com','otherbrand.net']], 'notes':[]}
        result=normalize_result(cached)
        proof=result['candidates'][1]['evidence'][0]
        self.assertEqual(proof['certificate_root_count'],35)
        self.assertEqual(proof['signal'],'shared_hosting_certificate')

    @patch('leakscope.phase3.passive_sources._request')
    def test_crtsh_retains_more_than_500_names_even_when_ui_limit_is_one(self,request):
        self.context['max_candidates']=1
        request.return_value=[{'id':i,'name_value':f'host{i}.example.com'} for i in range(650)]
        result=self.collect('crtsh')
        self.assertEqual(len(result['candidates']),650)

    @patch('leakscope.phase3.passive_sources._request')
    def test_certspotter_continuation_uses_last_issuance_and_ends_at_empty_page(self,request):
        request.return_value=[{'id':'4321','dns_names':['api.example.com']}]
        result=self.collect('certspotter')
        self.assertEqual(result['continuation'],{'after':'4321'})
        self.context['cursor']=result['continuation'];request.return_value=[]
        done=self.collect('certspotter')
        self.assertIn('after=4321',request.call_args.args[0]);self.assertNotIn('continuation',done)

    @patch('leakscope.phase3.passive_sources._request')
    def test_urlscan_continuation_uses_search_after_tuple(self,request):
        request.return_value={'results':[{'sort':[1700000000000,'id123'],'page':{'domain':'api.example.com'}} for _ in range(100)],'has_more':False,'total':200}
        result=self.collect('urlscan')
        self.assertEqual(result['continuation'],{'search_after':[1700000000000,'id123']})
        self.context['cursor']=result['continuation'];request.return_value={'results':[],'has_more':False}
        done=self.collect('urlscan')
        self.assertIn('search_after=1700000000000%2Cid123',request.call_args.args[0]);self.assertNotIn('continuation',done)

    @patch('leakscope.phase3.passive_sources.requests.get')
    def test_budget_refusal_returns_resumable_cursor(self,get):
        self.maximum['public_ct']=0;self.context['cursor']={'after':'4321'}
        result=self.collect('certspotter')
        self.assertEqual(result['blocked'],'budget');self.assertEqual(result['continuation'],{'after':'4321'});get.assert_not_called()

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_search_pages_resume_then_unique_entity_children_are_queried(self,request):
        row={'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'}}}}
        request.return_value={'data':[row],'links':{'next':'https://api.gleif.org/api/v1/lei-records?page[number]=2'}}
        first=self.collect('gleif','Example Corp','company')
        self.assertEqual(first['continuation']['phase'],'search')
        self.context['cursor']=first['continuation'];request.side_effect=[{'data':[],'links':{}},{'data':[]}]
        second=self.collect('gleif','Example Corp','company')
        self.assertEqual(request.call_count,3)
        self.assertIn('/direct-children',request.call_args.args[0]);self.assertNotIn('continuation',second)

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_children_are_paginated_without_repeating_name_search(self,request):
        row={'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'}}}}
        child={'type':'lei-records','id':'B'*18+'12','attributes':{'entity':{'legalName':{'name':'Other Brand'}}}}
        request.side_effect=[{'data':[row]},{'data':[child],'links':{'next':'https://api.gleif.org/api/v1/lei-records/'+row['id']+'/direct-children?page[number]=2'}}]
        first=self.collect('gleif','Example Corp','company')
        self.assertEqual(first['continuation']['phase'],'children')
        self.context['cursor']=first['continuation'];request.side_effect=None;request.return_value={'data':[]}
        second=self.collect('gleif','Example Corp','company')
        self.assertIn('/direct-children',request.call_args.args[0]);self.assertIn('page%5Bnumber%5D=2',request.call_args.args[0]);self.assertNotIn('continuation',second)

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_failed_children_are_not_automatically_retried(self,request):
        from leakscope.phase3.passive_sources import LookupError
        row={'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'}}}}
        request.side_effect=[{'data':[row]},LookupError('Provider rate limit HTTP 429; no automatic retry was made')]
        result=self.collect('gleif','Example Corp','company')
        self.assertTrue(result['failed']);self.assertNotIn('continuation',result)
        self.assertTrue(result['leads']);self.assertEqual(request.call_count,2)

    @patch('leakscope.phase3.passive_sources._request')
    def test_gleif_budget_blocked_children_resume_without_repeating_search(self,request):
        from leakscope.phase3.passive_sources import CollectionBlocked
        row={'type':'lei-records','id':'A'*18+'12','attributes':{'entity':{'legalName':{'name':'Example Corp'}}}}
        request.side_effect=[{'data':[row]},CollectionBlocked('budget')]
        result=self.collect('gleif','Example Corp','company')
        self.assertEqual(result['blocked'],'budget');self.assertEqual(result['continuation'],{'phase':'children','lei':row['id'],'page':1})
        self.context['cursor']=result['continuation'];request.side_effect=None;request.return_value={'data':[]}
        self.collect('gleif','Example Corp','company')
        self.assertIn('/direct-children',request.call_args.args[0]);self.assertEqual(request.call_count,3)

    @patch('leakscope.phase3.passive_sources._request')
    def test_crtsh_cross_root_names_require_a_scoped_name_on_the_same_certificate(self,request):
        request.return_value=[{'id':11,'name_value':'notexample.com\nunrelated.net'}, {'id':12,'name_value':'*.example.com\nbrand.other.net','not_before':'2020-01-01','not_after':'2021-01-01'}]
        result=self.collect('crtsh')
        self.assertEqual({candidate['domain'] for candidate in result['candidates']},{'*.example.com','brand.other.net'})
        lead=next(candidate for candidate in result['candidates'] if candidate['domain']=='brand.other.net')
        self.assertEqual(lead['relation'],'related');proof=lead['evidence'][0]
        self.assertEqual(proof['signal'],'historical_certificate_name');self.assertLessEqual(proof['strength'],35)
        self.assertEqual(proof['certificate_id'],'12');self.assertEqual(proof['certificate_root_count'],2)
        self.assertEqual(proof['not_after'],'2021-01-01');self.assertIn('ownership',proof['excerpt'])

    @patch('leakscope.phase3.passive_sources._request')
    def test_crtsh_high_fanout_certificates_preserve_scoped_names_and_downgrade_references(self,request):
        names=['api.example.com',*[f'brand{index}.net' for index in range(12)]]
        request.return_value=[{'id':123,'name_value':'\n'.join(names)}]
        result=self.collect('crtsh')
        self.assertEqual(len(result['candidates']),13)
        scoped=next(candidate for candidate in result['candidates'] if candidate['domain']=='api.example.com')
        foreign=next(candidate for candidate in result['candidates'] if candidate['domain']=='brand0.net')
        self.assertEqual(scoped['evidence'][0]['signal'],'historical_certificate_name')
        self.assertEqual(foreign['evidence'][0]['signal'],'shared_hosting_certificate')
        self.assertEqual(foreign['evidence'][0]['certificate_root_count'],13);self.assertLessEqual(foreign['evidence'][0]['strength'],10)

    def test_cached_crtsh_certificate_references_are_normalized_without_network(self):
        from leakscope.phase3.passive_sources import normalize_result
        names=['api.example.com',*[f'brand{index}.net' for index in range(12)]]
        cached={'candidates':[{'domain':name,'relation':'related','evidence':[{'source':'crtsh','certificate_id':'123','via':'example.com','signal':'historical_certificate_name','strength':35}]} for name in names]}
        result=normalize_result(cached)
        self.assertEqual(cached['candidates'][1]['evidence'][0]['signal'],'historical_certificate_name')
        self.assertEqual(result['candidates'][0]['evidence'][0]['signal'],'historical_certificate_name')
        self.assertEqual(result['candidates'][1]['evidence'][0]['signal'],'shared_hosting_certificate')
        self.assertEqual(normalize_result(result),result)

    def test_certificate_ids_are_grouped_separately_for_each_index_source(self):
        from leakscope.phase3.passive_sources import normalize_result
        def item(name,source):
            return {'domain':name,'relation':'related','evidence':[{'source':source,'certificate_id':'123','via':'example.com','signal':'historical_certificate_name' if source=='crtsh' else 'certificate_san','strength':35}]}
        cached={'candidates':[item(name,'certspotter') for name in ['api.example.com',*[f'brand{index}.net' for index in range(12)]]]+[item('api.example.com','crtsh'),item('different.org','crtsh')]}
        result=normalize_result(cached)
        proof=result['candidates'][-1]['evidence'][0]
        self.assertEqual(proof['signal'],'historical_certificate_name');self.assertEqual(proof['certificate_root_count'],2)
