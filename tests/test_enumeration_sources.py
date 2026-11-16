"""Native enumeration fixtures: no external DNS or provider traffic."""
import time
from unittest.mock import patch, Mock
from django.test import SimpleTestCase
import dns.resolver

class EnumerationSourceTests(SimpleTestCase):
    def setUp(self):
        self.used={};self.limit=100
        self.context={'deadline':time.monotonic()+60,'seed_domains':['example.com'],'dns_rate':1000,'wordlist':['www','api'],'max_candidates':1}
    def spend(self,bucket,units=1):
        if self.used.get(bucket,0)+units>self.limit:return False
        self.used[bucket]=self.used.get(bucket,0)+units;return True
    def collect(self,source,value='example.com',kind='domain'):
        from leakscope.phase3.enumeration_sources import collect
        return collect(source,{'kind':kind,'value':value,'depth':0},config={},spend=self.spend,context=self.context)
    def test_ptr_records_keep_weak_provenance(self):
        from leakscope.phase3 import enumeration_sources as sources
        with patch.object(sources,'_resolve',return_value=['host.otherbrand.net.']) as resolve:
            result=self.collect('reverse_dns','192.0.2.1','ip')
        self.assertEqual(result['candidates'][0]['domain'],'host.otherbrand.net')
        self.assertEqual(result['candidates'][0]['evidence'][0]['signal'],'reverse_dns')
        self.assertLessEqual(result['candidates'][0]['evidence'][0]['strength'],25)
        self.assertEqual(resolve.call_args.args[1],'PTR')
    def test_reverse_ip_retains_all_returned_names_without_candidate_cap(self):
        from leakscope.phase3 import enumeration_sources as sources
        with patch.object(sources,'_request',return_value='a.other.net\nb.other.net\n'):
            result=self.collect('passive_dns','192.0.2.1','ip')
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['evidence'][0]['signal']=='shared_ip' for c in result['candidates']))
    def test_prefix_overview_exposes_asn_and_network_leads_without_ownership(self):
        from leakscope.phase3 import enumeration_sources as sources
        with patch.object(sources,'_request',return_value={'data':{'prefix':'192.0.2.0/24','asns':[{'asn':64500,'holder':'Example Network'}]}}):
            result=self.collect('infrastructure','192.0.2.1','ip')
        self.assertEqual({(x['kind'],x['value']) for x in result['leads']},{('network','192.0.2.0/24'),('asn','AS64500')})
        self.assertTrue(all(x['evidence'][0]['strength']<=20 for x in result['leads']))
    def test_asn_returns_all_announced_prefix_leads(self):
        from leakscope.phase3 import enumeration_sources as sources
        with patch.object(sources,'_request',return_value={'data':{'prefixes':[{'prefix':'192.0.2.0/24'},{'prefix':'2001:db8::/32'}]}}):
            result=self.collect('infrastructure','AS64500','asn')
        self.assertEqual(len(result['leads']),2)
    def test_brute_filters_wildcard_answers_and_keeps_distinct_hosts(self):
        from leakscope.phase3 import enumeration_sources as sources
        def resolve(name,kind,**kwargs):
            if name.startswith('api.'):return ['192.0.2.2'] if kind=='A' else []
            return ['192.0.2.1'] if kind=='A' else []
        with patch.object(sources,'_resolve',side_effect=resolve):result=self.collect('brute')
        self.assertEqual([c['domain'] for c in result['candidates']],['api.example.com'])
        self.assertEqual(result['candidates'][0]['dns_status'],'resolved')
        self.assertTrue(any('wildcard' in n.lower() for n in result['notes']))
    def test_brute_budget_pause_keeps_cursor_and_reuses_completed_dns_answers(self):
        from leakscope.phase3 import enumeration_sources as sources
        self.limit=1
        with patch.object(sources,'_resolve',return_value=[]) as resolve:
            result=self.collect('brute')
            self.assertEqual(result['blocked'],'budget');self.assertIn('continuation',result)
            self.context['cursor']=result['continuation'];self.limit=100
            resumed=self.collect('brute')
        self.assertEqual(resumed.get('blocked'),None)
        # Three wildcard controls and two guesses, A/AAAA each, ten total reservations.
        self.assertEqual(self.used['dns'],10)
    def test_out_of_scope_brute_is_not_run(self):
        from leakscope.phase3 import enumeration_sources as sources
        with patch.object(sources,'_resolve',side_effect=AssertionError('unrelated domain')):
            result=self.collect('brute','vendor.net')
        self.assertEqual(result['candidates'],[]);self.assertEqual(self.used,{})
    def test_alterations_are_resolved_and_preserve_parent_scope(self):
        from leakscope.phase3 import enumeration_sources as sources
        def resolve(name,kind,**kwargs):
            return ['192.0.2.3'] if name=='api-dev.example.com' and kind=='A' else []
        with patch.object(sources,'_resolve',side_effect=resolve):result=self.collect('alterations','api.example.com')
        self.assertIn('api-dev.example.com',{c['domain'] for c in result['candidates']})
        self.assertTrue(all(c['domain'].endswith('.example.com') for c in result['candidates']))
    def test_demo_does_one_dns_request_and_never_returns_continuation(self):
        from leakscope.phase3 import enumeration_sources as sources
        self.context['demo']=True
        with patch.object(sources,'_resolve',return_value=['192.0.2.1']):result=self.collect('brute')
        self.assertEqual(self.used.get('dns'),1)
        self.assertLessEqual(len(result['candidates']),1);self.assertNotIn('continuation',result)
    def test_zero_budget_prevents_dns_and_http(self):
        from leakscope.phase3 import enumeration_sources as sources
        self.limit=0
        with patch.object(sources,'_resolve',side_effect=AssertionError('dns')),patch.object(sources.requests,'get',side_effect=AssertionError('http')):
            self.assertEqual(self.collect('reverse_dns','192.0.2.1','ip')['blocked'],'budget')
            self.assertEqual(self.collect('passive_dns','192.0.2.1','ip')['blocked'],'budget')

    def test_network_reverse_ip_advances_in_24_blocks(self):
        from leakscope.phase3 import enumeration_sources as sources
        with patch.object(sources,'_request',return_value='host.example.com\n') as request:
            first=self.collect('passive_dns','192.0.2.0/23','network')
            self.assertIn('q=192.0.2.0%2F24',request.call_args.args[0]);self.context['cursor']=first['continuation']
            second=self.collect('passive_dns','192.0.2.0/23','network')
        self.assertIn('q=192.0.3.0%2F24',request.call_args.args[0]);self.assertNotIn('continuation',second)

    def test_ipv6_reverse_ip_index_is_explicitly_unsupported_without_spend(self):
        from leakscope.phase3 import enumeration_sources as sources
        with patch.object(sources,'_request',side_effect=AssertionError('unsupported')):
            result=self.collect('passive_dns','2001:db8::1','ip')
        self.assertEqual(result['candidates'],[]);self.assertTrue(any('IPv6' in note for note in result['notes']))

    def test_native_http_refuses_redirects_and_closes_responses(self):
        from leakscope.phase3 import enumeration_sources as sources
        response=Mock(status_code=302)
        with patch.object(sources.requests,'get',return_value=response) as get:
            result=self.collect('passive_dns','192.0.2.1','ip')
        self.assertFalse(get.call_args.kwargs['allow_redirects']);response.close.assert_called_once()
        self.assertEqual(self.used,{'domain_api':1});self.assertEqual(result['candidates'],[]);self.assertTrue(result['notes'])

    def test_dns_errors_do_not_drop_remaining_wordlist(self):
        from leakscope.phase3 import enumeration_sources as sources
        def resolve(name,kind,**kwargs):
            if name.startswith('www.'):raise dns.resolver.NoNameservers()
            return ['192.0.2.3'] if name.startswith('api.') and kind=='A' else []
        with patch.object(sources,'_resolve',side_effect=resolve):result=self.collect('brute')
        self.assertEqual([c['domain'] for c in result['candidates']],['api.example.com']);self.assertTrue(result['notes'])

    def test_domain_only_passive_reverse_ip_uses_the_supplied_name(self):
        from leakscope.phase3 import enumeration_sources as sources
        with patch.object(sources,'_request',return_value='related.otherbrand.net\n') as request,patch.object(sources,'_resolve',side_effect=AssertionError('no active DNS')):
            result=self.collect('passive_dns')
        self.assertIn('q=example.com',request.call_args.args[0])
        self.assertEqual([candidate['domain'] for candidate in result['candidates']],['related.otherbrand.net'])
        self.assertEqual(result['candidates'][0]['evidence'][0]['signal'],'shared_ip')

    def test_alteration_wildcard_controls_use_the_sibling_parent(self):
        from leakscope.phase3 import enumeration_sources as sources
        queried=[]
        def resolve(name,kind,**kwargs):
            queried.append(name)
            if kind!='A' or name.endswith('.api.example.com'):return []
            return ['192.0.2.2'] if name=='api-prod.example.com' else ['192.0.2.1']
        with patch.object(sources,'_resolve',side_effect=resolve):result=self.collect('alterations','api.example.com')
        self.assertEqual([candidate['domain'] for candidate in result['candidates']],['api-prod.example.com'])
        controls=[name for name in queried if name.startswith('ls-')]
        self.assertTrue(controls);self.assertTrue(all(len(name.split('.'))==3 for name in controls))

    def test_nested_wordlist_validates_each_label_instead_of_total_label_length(self):
        from leakscope.phase3 import enumeration_sources as sources
        nested='a'*50+'.'+'b'*50
        self.context['wordlist']=[nested,'c'*64,'bad..word']
        queried=[]
        def resolve(name,kind,**kwargs):
            queried.append(name)
            return ['192.0.2.3'] if name==nested+'.example.com' and kind=='A' else []
        with patch.object(sources,'_resolve',side_effect=resolve):result=self.collect('brute')
        self.assertEqual([candidate['domain'] for candidate in result['candidates']],[nested+'.example.com'])
        self.assertFalse(any('c'*64 in name or 'bad..word' in name for name in queried))

    def test_nested_wordlist_checks_wildcards_at_the_nested_parent(self):
        from leakscope.phase3 import enumeration_sources as sources
        self.context['wordlist']=['api.dev','www']
        def resolve(name,kind,**kwargs):
            if kind=='A' and name.endswith('.dev.example.com'):return ['192.0.2.1']
            if name=='www.example.com' and kind=='A':return ['192.0.2.2']
            return []
        with patch.object(sources,'_resolve',side_effect=resolve):result=self.collect('brute')
        self.assertEqual([candidate['domain'] for candidate in result['candidates']],['www.example.com'])

    def test_demo_retains_one_dns_answer_with_explicit_wildcard_uncertainty(self):
        from leakscope.phase3 import enumeration_sources as sources
        self.context['demo']=True
        with patch.object(sources,'_resolve',return_value=['192.0.2.7']) as resolve:result=self.collect('brute')
        self.assertEqual(resolve.call_count,1);self.assertEqual(self.used['dns'],1)
        self.assertEqual(len(result['candidates']),1);candidate=result['candidates'][0]
        self.assertEqual(candidate['domain'],'www.example.com');self.assertEqual(candidate['dns_status'],'resolved')
        proof=candidate['evidence'][0]
        self.assertEqual(proof['signal'],'dns_demo_unvalidated');self.assertEqual(proof['addresses'],['192.0.2.7']);self.assertLessEqual(proof['strength'],20)
        self.assertIn('wildcard',proof['excerpt'].lower());self.assertEqual(result['leads'],[]);self.assertNotIn('continuation',result)
