"""Collector pagination and non-discard guarantees; no network requests."""
import time
from unittest.mock import patch,Mock
from django.test import TestCase
from leakscope.phase3 import expansion_sources as sources
from leakscope_app.models import CertificateRecord,CertificateName


class ExpansionSourcePagingTests(TestCase):
    def setUp(self):
        self.context={'deadline':time.monotonic()+30,'seed_domains':['example.com'],'company_names':['Example Corp']}
        self.used={};self.limits={'web':10,'netlas':10,'dns':10}
    def spend(self,bucket,units=1):
        if self.used.get(bucket,0)+units>self.limits.get(bucket,0):return False
        self.used[bucket]=self.used.get(bucket,0)+units;return True
    def collect(self,source,kind='domain',value='example.com'):
        return sources.collect(source,{'kind':kind,'value':value,'depth':0},config={'NETLAS_API_KEY':'offline','FIRECRAWL_API_URL':'https://firecrawl.example/v2'},spend=self.spend,context=self.context)
    @patch('leakscope.phase3.expansion_sources._request')
    def test_netlas_returns_next_offset_and_uses_it(self,http):
        http.return_value={'items':[{'data':{'domain':f'brand{i}.net','registrant':{'organization':'Example Corp'}}} for i in range(20)],'total':21}
        first=self.collect('netlas','company','Example Corp')
        self.assertEqual(first['continuation'],{'start':20})
        self.context['cursor']=first['continuation']
        http.return_value={'items':[{'data':{'domain':'last.net'}}],'total':21}
        second=self.collect('netlas','company','Example Corp')
        self.assertEqual(http.call_args.kwargs['params']['start'],20)
        self.assertNotIn('continuation',second)
        self.assertEqual(self.used['netlas'],2)
    @patch('leakscope.phase3.expansion_sources._post')
    def test_web_budget_pause_resumes_search_without_repeating_homepage(self,http):
        http.return_value={'success':True,'data':{'markdown':'hello'}};self.limits['web']=1
        first=self.collect('web')
        self.assertEqual(first['continuation'],{'stage':'search'})
        self.context['cursor']=first['continuation'];self.limits['web']=2
        http.return_value={'success':True,'data':{'web':[]}}
        second=self.collect('web')
        self.assertEqual(http.call_count,2)
        self.assertTrue(http.call_args.args[0].endswith('/search'))
        self.assertNotIn('continuation',second)
    @patch('leakscope.phase3.expansion_sources.dns.resolver.Resolver')
    def test_dns_budget_pause_preserves_record_position_and_address_leads(self,factory):
        factory.return_value.resolve.return_value=[];self.limits['dns']=1
        first=self.collect('dns')
        self.assertEqual(first['continuation'],{'record':1})
        self.context['cursor']=first['continuation'];self.limits['dns']=6
        record=Mock();record.to_text.return_value='192.0.2.5'
        factory.return_value.resolve.side_effect=[[record],[],[],[],[]]
        second=self.collect('dns')
        self.assertIn('A',[x.args[1] for x in factory.return_value.resolve.call_args_list])
        self.assertTrue(any(x['kind']=='ip' and x['value']=='192.0.2.5' for x in second['leads']))
        self.assertNotIn('continuation',second)
    def test_all_names_on_retained_certificate_are_returned(self):
        names=[f'host{i}.example.com' for i in range(610)]
        cert=CertificateRecord.objects.create(key='bulk-retained-cert',domains=names)
        CertificateName.objects.create(certificate=cert,name=names[0])
        result=self.collect('certificates')
        self.assertEqual(len(result['candidates']),610)
