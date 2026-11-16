import base64
import json
from unittest.mock import patch
import requests
from django.test import SimpleTestCase
from leakscope.phase3.providers import search_provider, account_info, ProviderRateLimit


class Response:
    def __init__(self,data=None,status=200,headers=None,raw=None):
        self.status_code=status;self.headers=headers or {};self.closed=False
        self.raw=raw if raw is not None else json.dumps(data).encode()
    def iter_content(self,chunk_size=8192):
        for i in range(0,len(self.raw),chunk_size):yield self.raw[i:i+chunk_size]
    def close(self):self.closed=True


class Phase3ProviderTests(SimpleTestCase):
    config={'SHODAN_API_KEY':'shodan-fixture-secret','ZOOMEYE_API_KEY':'zoomeye-fixture-secret','NETLAS_API_KEY':'netlas-fixture-secret'}
    def shodan(self):
        return {'total':1,'matches':[{'ip_str':'192.0.2.1','port':443,'transport':'tcp','timestamp':'2026-11-07T01:00:00Z','http':{'title':'Acme Console'},'_shodan':{'module':'https'}}]}
    def netlas(self):
        return {'items':[{'_id':'netlas-one','data':{'ip':'192.0.2.2','port':5432,'prot4':'tcp','protocol':'postgres','@timestamp':'2026-11-07T01:00:00Z','uri':'postgres://192.0.2.2:5432','postgres':{'version':'17.0'}}}]}
    def zoomeye(self):
        return {'code':60000,'total':1,'data':[{'ip':'192.0.2.3','port':443,'protocol':'tcp','service':'https','title':['Acme Console'],'update_time':'2026-11-07T01:00:00Z','url':'https://console.example:443/'}]}

    @patch('leakscope.phase3.providers.requests.request')
    def test_real_shodan_shape(self,request):
        request.return_value=Response(self.shodan())
        result=search_provider('shodan','port:443',self.config)
        observation=result['observations'][0]
        self.assertEqual(observation['address'],'192.0.2.1')
        self.assertEqual(observation['scheme'],'https')
        self.assertEqual(observation['observed_at'],'2026-11-07T01:00:00Z')
        self.assertEqual(result['pages'],1);self.assertFalse(result['partial'])
        self.assertEqual(request.call_args.kwargs['params']['key'],'shodan-fixture-secret')

    @patch('leakscope.phase3.providers.requests.request')
    def test_real_zoomeye_v2_shape(self,request):
        request.return_value=Response(self.zoomeye())
        result=search_provider('zoomeye','title="Acme"',self.config,page_size=25)
        self.assertEqual(result['observations'][0]['title'],'Acme Console')
        self.assertEqual(result['observations'][0]['scheme'],'https')
        payload=request.call_args.kwargs['json']
        self.assertEqual(base64.b64decode(payload['qbase64']).decode(),'title="Acme"')
        self.assertEqual(payload['pagesize'],25)
        self.assertIn('title',payload['fields'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_real_netlas_shape_fixed_page_offsets_and_bearer_auth(self,request):
        first={'items':[dict(self.netlas()['items'][0],_id=f'id{i}') for i in range(20)]}
        request.side_effect=[Response(first),Response({'items':[]})]
        result=search_provider('netlas','port:5432',self.config,page_count=2,page_size=100)
        self.assertEqual(result['observations'][0]['service_type'],'postgresql')
        self.assertEqual(result['observations'][0]['source_id'],'id0')
        self.assertEqual(request.call_args_list[1].kwargs['params']['start'],20)
        self.assertNotIn('size',request.call_args.kwargs['params'])
        self.assertNotIn('indices',request.call_args.kwargs['params'])
        self.assertEqual(request.call_args.kwargs['headers']['Authorization'],'Bearer netlas-fixture-secret')

    @patch('leakscope.phase3.providers.requests.request')
    def test_partial_page_failure_preserves_completed_results(self,request):
        first=self.shodan();first['total']=1000
        first['matches']=first['matches']*100
        request.side_effect=[Response(first),Response(raw=b'shodan-fixture-secret',status=503)]
        result=search_provider('shodan','port:443',self.config,page_count=2)
        self.assertTrue(result['partial']);self.assertEqual(result['pages'],1)
        self.assertTrue(result['observations']);self.assertTrue(result['errors'])
        self.assertNotIn('shodan-fixture-secret',json.dumps(result))

    @patch('leakscope.phase3.providers.requests.request')
    def test_first_rate_limit_is_structured_and_redacted(self,request):
        response=Response(raw=b'netlas-fixture-secret',status=429,headers={'Retry-After':'23'});request.return_value=response
        with self.assertRaises(ProviderRateLimit) as caught:search_provider('netlas','port:443',self.config)
        self.assertEqual(caught.exception.retry_after,23)
        self.assertNotIn('netlas-fixture-secret',str(caught.exception));self.assertTrue(response.closed)
        self.assertEqual(request.call_count,1)

    @patch('leakscope.phase3.providers.requests.request')
    def test_later_rate_limit_returns_partial_and_retry_after(self,request):
        request.side_effect=[Response({'items':[dict(self.netlas()['items'][0],_id=str(i)) for i in range(20)]}),Response(status=429,headers={'Retry-After':'60'})]
        result=search_provider('netlas','port:443',self.config,page_count=2)
        self.assertTrue(result['partial']);self.assertEqual(result['retry_after'],60)

    @patch('leakscope.phase3.providers.requests.request')
    def test_empty_result_is_success_but_all_invalid_records_fail(self,request):
        request.return_value=Response({'items':[]})
        self.assertEqual(search_provider('netlas','port:443',self.config)['observations'],[])
        request.return_value=Response({'items':[{'data':{'ip':'invalid','port':999999}}]})
        with self.assertRaises(ValueError):search_provider('netlas','port:443',self.config)

    @patch('leakscope.phase3.providers.requests.request')
    def test_malformed_rows_preserve_valid_rows_and_report_partial(self,request):
        body=self.netlas();body['items'].append({'data':{'ip':[],'port':'no'}});request.return_value=Response(body)
        result=search_provider('netlas','port:5432',self.config)
        self.assertEqual(len(result['observations']),1);self.assertTrue(result['partial'])
        self.assertTrue(result['errors'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_errors_and_reflected_metadata_do_not_leak_keys(self,request):
        request.side_effect=requests.Timeout('netlas-fixture-secret')
        with self.assertRaises(ValueError) as caught:search_provider('netlas','port:443',self.config)
        self.assertNotIn('netlas-fixture-secret',str(caught.exception))
        request.side_effect=None;data=self.netlas();data['items'][0]['data']['banner']='netlas-fixture-secret';request.return_value=Response(data)
        self.assertNotIn('netlas-fixture-secret',json.dumps(search_provider('netlas','port:443',self.config)))

    @patch('leakscope.phase3.providers.requests.request')
    def test_bounds_configuration_and_json_failure(self,request):
        for params in ({'page_count':21},{'page_size':101},{'page_count':0}):
            with self.assertRaises(ValueError):search_provider('netlas','port:443',self.config,**params)
        with self.assertRaises(ValueError):search_provider('netlas','port:443',{})
        with self.assertRaises(ValueError):search_provider('unknown','port:443',self.config)
        request.assert_not_called();request.return_value=Response(raw=b'not JSON')
        with self.assertRaises(ValueError):search_provider('netlas','port:443',self.config)
        request.return_value=Response(raw=b'x'*(10*1024*1024+1))
        with self.assertRaisesMessage(ValueError,'limit'):search_provider('netlas','port:443',self.config)

    @patch('leakscope.phase3.providers.requests.request')
    def test_account_info_uses_only_safe_counters(self,request):
        request.return_value=Response({'username':'private-name','api_key':'netlas-fixture-secret','requests_left':{'remained':99,'limit':100},'coins':{'left':500,'total_coins_spent':10},'scan_coins':{'left':20}})
        result=account_info('netlas',self.config)
        self.assertEqual(result['coins_left'],500);self.assertEqual(result['requests_left'],99)
        self.assertNotIn('private-name',json.dumps(result));self.assertNotIn('secret',json.dumps(result))

    @patch('leakscope.phase3.providers.requests.request')
    def test_missing_provider_timestamp_is_not_invented(self,request):
        data=self.shodan();del data['matches'][0]['timestamp'];request.return_value=Response(data)
        self.assertIsNone(search_provider('shodan','port:443',self.config)['observations'][0]['observed_at'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_metadata_keys_are_redacted_and_boolean_ips_are_rejected(self,request):
        body=self.netlas();body['items'][0]['data']['netlas-fixture-secret']='value'
        body['items'].append({'data':{'ip':True,'port':443}});request.return_value=Response(body)
        result=search_provider('netlas','port:443',self.config)
        self.assertEqual(len(result['observations']),1)
        self.assertNotIn('netlas-fixture-secret',json.dumps(result))

    @patch('leakscope.phase3.providers.requests.request')
    def test_observation_cap_remains_partial_even_if_all_rows_were_received(self,request):
        body=self.shodan();body['total']=3;body['matches']=[dict(body['matches'][0],ip_str=f'192.0.2.{i}') for i in range(1,4)]
        request.return_value=Response(body)
        with patch('leakscope.phase3.providers.MAX_OBSERVATIONS',2):
            result=search_provider('shodan','port:443',self.config)
        self.assertEqual(len(result['observations']),2)
        self.assertTrue(result['partial']);self.assertTrue(result['more_available'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_account_counters_for_shodan_and_zoomeye_hide_identity(self,request):
        request.return_value=Response({'query_credits':12,'scan_credits':3,'email':'private@example.test'})
        self.assertEqual(account_info('shodan',self.config),{'query_credits':12,'scan_credits':3})
        request.return_value=Response({'code':60000,'data':{'username':'private','subscription':{'points':'40','zoomeye_points':'50','plan':'private'}}})
        self.assertEqual(account_info('zoomeye',self.config),{'points':40,'zoomeye_points':50})

    @patch('leakscope.phase3.providers.requests.request')
    def test_source_id_fallback_is_stable_and_metadata_bounded(self,request):
        body=self.shodan();body['matches'][0]['data']='x'*100000;request.return_value=Response(body)
        first=search_provider('shodan','port:443',self.config)['observations'][0]
        second=search_provider('shodan','port:443',self.config)['observations'][0]
        self.assertEqual(first['source_id'],second['source_id'])
        self.assertLess(len(json.dumps(first['metadata']).encode()),65536)

    @patch('leakscope.phase3.providers.requests.request')
    def test_http_vhosts_keep_distinct_identity_and_actual_resolved_ip(self,request):
        body=self.netlas();base=body['items'][0]['data']
        body['items']=[{'data':dict(base,port=443,protocol='https',uri='https://first.example/admin/console',http={'title':'First'})},{'data':dict(base,port=443,protocol='https',uri='https://second.example/',http={'title':'Second'})}]
        request.return_value=Response(body)
        observations=search_provider('netlas','port:443',self.config)['observations']
        self.assertEqual([r['address'] for r in observations],['first.example','second.example'])
        self.assertTrue(all(r['metadata']['resolved_ip']=='192.0.2.2' for r in observations))
        self.assertEqual(observations[0]['metadata']['uri'],'https://first.example/admin/console')
        self.assertNotEqual(observations[0]['source_id'],observations[1]['source_id'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_untrusted_or_mismatched_urls_do_not_replace_scanned_address(self,request):
        for uri in ('https://first.example:8443/','https://first.example\r\n.evil.example/','https://user:password@first.example/','https://first.example@evil.example/','https://first.example/?redirect=//evil.example/'):
            body=self.netlas();body['items'][0]['data'].update(port=443,protocol='https',uri=uri)
            request.return_value=Response(body)
            address=search_provider('netlas','port:443',self.config)['observations'][0]['address']
            # A query parameter cannot change the actual URL hostname.
            expected='first.example' if uri.startswith('https://first.example/?') else '192.0.2.2'
            self.assertEqual(address,expected)

    @patch('leakscope.phase3.providers.requests.request')
    def test_ip_literal_source_url_preserves_ip_and_observed_http_scheme(self,request):
        body=self.netlas();body['items'][0]['data'].update(port=443,protocol='tcp',uri='https://192.0.2.2/admin/')
        request.return_value=Response(body)
        row=search_provider('netlas','port:443',self.config)['observations'][0]
        self.assertEqual(row['address'],'192.0.2.2')
        self.assertEqual(row['scheme'],'https')

    @patch('leakscope.phase3.providers.requests.request')
    def test_http_module_variants_and_explicit_http_evidence(self,request):
        for module,extra,expected in (
            ('http-simple-new',{},'http'),('https-simple-new',{},'https'),
            ('psa/https',{'header':'HTTP/1.1 200 OK\r\n'},'https'),
            ('oauth2/http',{'banner':'HTTP/1.1 401 Unauthorized\r\n'},'http'),
            ('tcp',{'http':{'headers':{'server':'Acme'}}},'http'),
            ('tcp',{'data':'HTTP/1.1 200 OK\r\nServer: Acme\r\n\r\n'},'http'),
        ):
            with self.subTest(module=module,extra=extra):
                body=self.shodan();raw=body['matches'][0]
                raw.pop('http',None);raw['_shodan']['module']=module;raw.update(extra)
                request.return_value=Response(body)
                row=search_provider('shodan','port:443',self.config)['observations'][0]
                self.assertEqual(row['scheme'],expected)
                self.assertEqual(row['service_type'],expected)
                self.assertEqual(row['metadata']['_shodan']['module'],module)

    @patch('leakscope.phase3.providers.requests.request')
    def test_native_tls_and_http_like_names_without_http_evidence_stay_native(self,request):
        for module in ('mqtt','httpsomething','httpish','ssl'):
            body=self.shodan();raw=body['matches'][0];raw.pop('http')
            raw['_shodan']['module']=module;raw['ssl']={'cert':{'subject':{'CN':'example.test'}}}
            request.return_value=Response(body)
            row=search_provider('shodan','port:443',self.config)['observations'][0]
            self.assertEqual(row['scheme'],'')
            self.assertEqual(row['service_type'],module)

    @patch('leakscope.phase3.providers.requests.request')
    def test_zoomeye_response_signature_selects_native_adapter_despite_port_service_label(self,request):
        from leakscope.phase3.collection_policy import plan_collection
        cases=(
            ('webpush',1001,'EFATAL:  unsupported frontend protocol 0.0: server supports 2.0 to 3.0\n\\x00','postgresql'),
            ('sdl-ets',5081,'Mysql Version: 5.6.50-log\nserver greeting mysql_native_password','mysql'),
            ('clearvisn',2052,'STAT pid 1660\nSTAT uptime 28884\nSTAT version 1.2.1\nSTAT curr_connections 4\nEND','memcached'),
        )
        for service,port,banner,expected in cases:
            with self.subTest(service=service):
                request.return_value=Response({'code':60000,'total':1,'data':[{'ip':'192.0.2.8','port':port,'protocol':'tcp','service':service,'banner':banner}]})
                row=search_provider('zoomeye','fixture response query',self.config,page_size=10)['observations'][0]
                self.assertEqual(row['service_type'],expected)
                self.assertEqual(plan_collection(row,'full')['actions'],['identity'])
                self.assertEqual(row['metadata']['service'],service)
                self.assertEqual(row['metadata']['service_type_basis'],'indexed response signature; live identity unverified')
                self.assertFalse(row['metadata'].get('identity_verified',False))

    @patch('leakscope.phase3.providers.requests.request')
    def test_banner_product_mentions_ports_and_http_bodies_cannot_select_native_adapter(self,request):
        for banner in ('MySQL documentation: mysql_native_password','PostgreSQL server supports 2.0 to 3.0','STAT version 1.2.1',
                       'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nMysql Version: 8.0\nmysql_native_password'):
            with self.subTest(banner=banner):
                request.return_value=Response({'code':60000,'total':1,'data':[{'ip':'192.0.2.8','port':3306,'protocol':'tcp','service':'unclassified','banner':banner}]})
                row=search_provider('zoomeye','fixture response query',self.config)['observations'][0]
                self.assertNotIn(row['service_type'],('mysql','postgresql','memcached'))

    @patch('leakscope.phase3.providers.requests.request')
    def test_actual_http_status_in_zoomeye_banner_or_header_enables_http_on_any_port(self,request):
        from leakscope.phase3.collection_policy import plan_collection
        for field in ('banner','header'):
            with self.subTest(field=field):
                request.return_value=Response({'code':60000,'total':1,'data':[{'ip':'192.0.2.8','port':49157,'protocol':'tcp','service':'unclassified',field:'HTTP/1.1 401 Unauthorized\r\nServer: fixture\r\n\r\n'}]})
                row=search_provider('zoomeye','fixture response query',self.config)['observations'][0]
                self.assertEqual(row['scheme'],'http')
                self.assertEqual(plan_collection(row,'full')['actions'],['http','httpx','screenshot'])

    @patch('leakscope.phase3.providers.requests.request')
    def test_saved_nonstandard_response_overrides_unrelated_service_taxonomy(self,request):
        cases=(('ibm-db2-admin','ceph v2\n\\x10\\x00\\x01\\x00','ceph'),
               ('sdl-ets','Zookeeper version: 3.7.0-build, built on 2021\nLatency min/avg/max: 0/1/2\nMode: follower\n','zookeeper'),
               ('ms-sql-s','','mssql'),('niagara-fox','','fox'),('IEC 61850-8-1 MMS','','mms'))
        for service,banner,expected in cases:
            with self.subTest(service=service):
                request.return_value=Response({'code':60000,'total':1,'data':[{'ip':'192.0.2.8','port':42137,'protocol':'tcp','service':service,'banner':banner}]})
                row=search_provider('zoomeye','fixture response query',self.config)['observations'][0]
                self.assertEqual(row['service_type'],expected)
                self.assertEqual(row['metadata']['service'],service)
                self.assertFalse(row['metadata'].get('identity_verified',False))
