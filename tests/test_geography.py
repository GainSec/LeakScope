"""Local geography and retained provider estimates must not contact remote services."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, RequestFactory, override_settings
from django.utils import timezone
from leakscope_app.models import ServiceEndpoint, ServiceObservation
from leakscope.phase3.state import ingest_observation
from leakscope.phase3 import relationships as graph


class ProviderGeographyTests(SimpleTestCase):
    def test_shodan_normalization_keeps_zero_coordinates_and_ip_binding(self):
        from leakscope.phase3.geography import extract_provider_geo
        result=extract_provider_geo({'ip_str':'8.8.8.8','timestamp':'2026-11-01T10:00:00Z','location':{'country_code':'US','country_name':'United States','city':'Example','latitude':0,'longitude':0}},'shodan')
        self.assertEqual(result['ip'],'8.8.8.8');self.assertEqual(result['country_code'],'US')
        self.assertEqual((result['latitude'],result['longitude']),(0.0,0.0))
        self.assertEqual(result['source_date'],'2026-11-01T10:00:00+00:00')
        self.assertIsNone(result['accuracy_radius_km'])

    def test_zoomeye_flat_v2_and_legacy_payloads_are_supported(self):
        from leakscope.phase3.geography import extract_provider_geo
        flat=extract_provider_geo({'ip':'1.1.1.1','country.name':'Australia','continent.name':'Oceania','province.name':'Queensland','city.name':'Brisbane','lat':'-27.47','lon':'153.02','update_time':'2026-11-01T00:00:00Z'},'zoomeye')
        self.assertEqual(flat['city'],'Brisbane');self.assertEqual(flat['continent_code'],'OC');self.assertEqual(flat['latitude'],-27.47);self.assertEqual(flat['region'],'Queensland')
        legacy=extract_provider_geo({'ip':'1.1.1.1','geoinfo':{'country':{'code':'AU','names':{'en':'Australia'}},'continent':{'code':'OC','names':{'en':'Oceania'}},'city':{'names':{'en':'Brisbane'}},'location':{'lat':-27.47,'lon':153.02}}},'zoomeye')
        self.assertEqual(legacy['country_code'],'AU');self.assertEqual(legacy['longitude'],153.02)

    def test_netlas_accuracy_is_meters_and_contact_coordinates_are_not_ip_geo(self):
        from leakscope.phase3.geography import extract_provider_geo
        result=extract_provider_geo({'_source':{'ip':'8.8.8.8','geo':{'country':'US','city':'Example','location':{'lat':40,'lon':-70},'accuracy':5000},'contacts':{'geo':{'lat':-10,'lon':10}}}},'netlas')
        self.assertEqual(result['accuracy_radius_km'],5);self.assertEqual(result['latitude'],40)
        self.assertIsNone(extract_provider_geo({'ip':'8.8.8.8','contacts':{'geo':{'lat':1,'lon':2}}},'netlas'))

    def test_malformed_coordinates_dates_and_ambiguous_ips_remain_unknown(self):
        from leakscope.phase3.geography import extract_provider_geo
        for lat,lon in [(True,1),(float('nan'),1),(91,1),(1,181),('bad',1),(None,1)]:
            result=extract_provider_geo({'ip':'8.8.8.8','location':{'country_code':'US','latitude':lat,'longitude':lon},'timestamp':'not-time'},'shodan')
            self.assertIsNone(result['latitude']);self.assertIsNone(result['longitude']);self.assertIsNone(result['source_date'])
        value=extract_provider_geo({'ip':['8.8.8.8','1.1.1.1'],'geo':{'country':'US','location':{'lat':1,'lon':2}}},'netlas')
        self.assertIsNone(value['ip'])

    def test_normalization_discards_secrets_and_retains_only_geo_fields(self):
        from leakscope.phase3.geography import extract_provider_geo
        result=extract_provider_geo({'ip_str':'8.8.8.8','api_key':'private','location':{'city':'Example','country_code':'US','latitude':1,'longitude':2,'token':'private'}},'shodan')
        self.assertNotIn('private',json.dumps(result));self.assertLessEqual(len(result),16)

    def test_ip_list_tail_cannot_be_ignored_and_nested_lists_are_invalid(self):
        from leakscope.phase3.geography import canonical_ip,extract_provider_geo
        self.assertIsNone(canonical_ip(['8.8.8.8']*32+['1.1.1.1']))
        self.assertIsNone(canonical_ip([['8.8.8.8']]))
        self.assertIsNone(canonical_ip(['8.8.8.8','192.168.1.1'],public_only=True))
        self.assertIsNone(extract_provider_geo({'ip':['8.8.8.8']*32+['1.1.1.1'],'_geo':{'ip':'8.8.8.8','country_code':'US'}},'netlas'))

    def test_unknown_continent_names_do_not_become_known_facets(self):
        from leakscope.phase3.geography import extract_provider_geo
        for code,name in [(None,'-'),('XX','Mars'),('EUROPEAN',None)]:
            row=extract_provider_geo({'ip':'8.8.8.8','_geo':{'ip':'8.8.8.8','continent_code':code,'continent_name':name}},'netlas')
            self.assertIsNone(row['continent_code']);self.assertIsNone(row['continent_name'])

    def test_normalized_envelope_cannot_override_a_contradictory_raw_ip(self):
        from leakscope.phase3.geography import extract_provider_geo
        value={'ip_str':'1.1.1.1','_geo':{'ip':'8.8.8.8','country_code':'US','latitude':40,'longitude':-70}}
        self.assertIsNone(extract_provider_geo(value,'shodan'))
        value={'ip':['1.1.1.1','8.8.8.8'],'_geo':{'ip':'8.8.8.8','country_code':'US','latitude':40,'longitude':-70}}
        self.assertIsNone(extract_provider_geo(value,'netlas'))

    def test_country_identifiers_are_iso_codes_with_explicit_kosovo_extension(self):
        from leakscope.phase3.geography import extract_provider_geo
        for code in ['EU','AP','AA','UK','XX','ZZ']:
            row=extract_provider_geo({'ip':'8.8.8.8','geo':{'country':code}},'netlas')
            self.assertIsNone(row['country_code'],code)
        row=extract_provider_geo({'ip':'8.8.8.8','geo':{'country':'XK'}},'netlas')
        self.assertEqual(row['country_code'],'XK')

    def test_country_codes_are_not_guessed_from_name_prefixes(self):
        from leakscope.phase3.geography import extract_provider_geo
        row=extract_provider_geo({'ip':'8.8.8.8','geo':{'country':'Germany','location':{'lat':1,'lon':2}}},'netlas')
        self.assertIsNone(row['country_code'])

    def test_observed_ip_uses_captured_address_without_dns(self):
        from leakscope.phase3.geography import observed_ip
        with patch('socket.getaddrinfo',side_effect=AssertionError('No DNS')):
            self.assertEqual(observed_ip('example.test',{'metadata':{'resolved_ip':'8.8.8.8'}}),'8.8.8.8')
            self.assertEqual(observed_ip('2606:4700:4700::1111',{}),'2606:4700:4700::1111')
            self.assertIsNone(observed_ip('example.test',{}))
            self.assertIsNone(observed_ip('192.168.1.10',{}))
            self.assertIsNone(observed_ip('example.test',{'metadata':{'resolved_ip':'fe80::1%eth0'}}))

    def test_comparisons_require_same_ip_and_do_not_invent_precision(self):
        from leakscope.phase3.geography import compare_estimates
        local={'ip':'8.8.8.8','country_code':'US','country_name':'United States','latitude':40,'longitude':-70,'accuracy_radius_km':100,'status':'located'}
        provider={'ip':'8.8.8.8','country_code':'US','latitude':40.1,'longitude':-70,'accuracy_radius_km':None,'status':'located'}
        result=compare_estimates(local,provider)
        self.assertTrue(result['country_agreement']);self.assertTrue(10<result['distance_km']<12);self.assertTrue(result['within_local_accuracy'])
        self.assertIsNone(result['within_combined_accuracy']);self.assertFalse(result['exact_location_confirmed'])
        self.assertEqual(compare_estimates(local,dict(provider,ip='1.1.1.1'))['status'],'not_comparable')
        self.assertEqual(compare_estimates(local,dict(provider,country_code='DE'))['status'],'country_differs')


class LocalGeographyTests(SimpleTestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'city.mmdb';self.path.write_bytes(b'fixture')
        self.settings=override_settings(LEAKSCOPE_GEOIP_FILE=str(self.path),LEAKSCOPE_GEOIP_SOURCE='Fixture local database');self.settings.enable();self.addCleanup(self.settings.disable)

    def reader(self):
        class Reader:
            closed=False
            def metadata(self):return SimpleNamespace(database_type='Fixture-City',build_epoch=1793577600)
            def get(self,ip):
                return {'country':{'iso_code':'US','names':{'en':'United States','zh-CN':'美国'}},'continent':{'code':'NA','names':{'en':'North America'}},'city':{'names':{'en':'Example'}},'subdivisions':[{'names':{'en':'Region'}}],'location':{'latitude':40,'longitude':-70,'accuracy_radius':25}}
            def close(self):self.closed=True
        return Reader()

    def test_lookup_uses_local_reader_and_sidecar_without_network(self):
        from leakscope.phase3 import geography
        Path(str(self.path)+'.json').write_text(json.dumps({'source':'Fixture','version':'2026-11','installed_at':'2026-11-10T00:00:00Z','sha256':'a'*64,'source_url':'https://user:private@example.test/'}))
        reader=self.reader()
        with patch.object(geography,'_open_reader',return_value=reader) as opener,patch('requests.get',side_effect=AssertionError('No HTTP')):
            first=geography.lookup_ip('8.8.8.8');second=geography.lookup_ip('8.8.4.4');status=geography.database_status()
        self.assertEqual(first['country_code'],'US');self.assertEqual(first['accuracy_radius_km'],25);self.assertEqual(second['source'],'Fixture');self.assertEqual(opener.call_count,1)
        self.assertEqual(status['version'],'2026-11');self.assertNotIn('private',json.dumps(status));self.assertNotIn(str(self.path),json.dumps(status))

    def test_missing_database_private_ip_and_no_ip_do_not_try_network(self):
        from leakscope.phase3 import geography
        with override_settings(LEAKSCOPE_GEOIP_FILE=str(self.path)+'-missing'),patch('requests.get',side_effect=AssertionError('No HTTP')):
            self.assertEqual(geography.lookup_ip('8.8.8.8')['status'],'not_installed')
            self.assertEqual(geography.lookup_ip('127.0.0.1')['status'],'not_public')
            self.assertEqual(geography.lookup_ip('example.test')['status'],'no_ip')

    def test_corrupt_reader_failure_is_safe_and_lookup_can_recover_after_atomic_replacement(self):
        from leakscope.phase3 import geography
        with patch.object(geography,'_open_reader',side_effect=ValueError('private/file')):
            self.assertEqual(geography.lookup_ip('8.8.8.8')['status'],'unavailable')
        new=self.path.with_suffix('.new');new.write_bytes(b'new valid fixture');new.replace(self.path)
        with patch.object(geography,'_open_reader',return_value=self.reader()):
            self.assertEqual(geography.lookup_ip('8.8.8.8')['status'],'located')

    def test_actual_official_mmdb_fixture_when_runtime_dependency_installed(self):
        fixture=Path('/app/.runtime/visual-atlas/GeoIP2-City-Test.mmdb')
        if importlib.util.find_spec('maxminddb') is None or not fixture.exists():self.skipTest('Actual MMDB fixture requires atlas dependency image')
        from leakscope.phase3.geography import lookup_ip
        with override_settings(LEAKSCOPE_GEOIP_FILE=str(fixture)):
            row=lookup_ip('89.160.20.128')
        self.assertEqual(row['country_code'],'SE');self.assertEqual(row['continent_code'],'EU');self.assertIsNotNone(row['latitude'])


class GeographyApiTests(TestCase):
    def setUp(self):self.factory=RequestFactory()
    def endpoint(self,address='8.8.8.8',provider='shodan',port=443,**metadata):
        return ingest_observation(provider,{'address':address,'port':port,'service_type':'https','title':'Fixture','metadata':metadata})
    def get(self,params=None,endpoint_id=None):
        from leakscope_app import geography
        request=self.factory.get('/api/atlas/geography',params or {})
        response=geography.detail(request,endpoint_id) if endpoint_id else geography.geography(request)
        return response,json.loads(response.content)
    def local(self,ip):
        return {'source':'Local fixture','status':'located','ip':ip,'country_code':'US','country_name':'United States','continent_code':'NA','continent_name':'North America','region':'Fixture','city':'Example','latitude':40.0,'longitude':-70.0,'accuracy_radius_km':100,'source_date':'2026-11-01T00:00:00+00:00'}

    def test_geojson_local_source_counts_and_unique_ip_modes(self):
        from leakscope.phase3 import geography
        first=self.endpoint();self.endpoint(port=8443)
        self.endpoint('1.1.1.1')
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:self.local(ip) for ip in ips}):
            response,data=self.get();self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(data['type'],'FeatureCollection');self.assertEqual(len(data['features']),2);self.assertEqual(data['counts']['matched_endpoints'],3)
            self.assertEqual(data['continents'][0]['count'],2);self.assertEqual(data['count_mode'],'ips')
            response,all_services=self.get({'count_mode':'endpoints'})
            self.assertEqual(len(all_services['features']),3)
        self.assertEqual(data['features'][0]['geometry']['coordinates'],[-70.0,40.0])
        self.assertIn('sample',data['basis'])

    def test_catalog_choices_and_family_filter_cover_saved_services(self):
        from leakscope.phase3.catalog import CATEGORY_LABELS, catalog
        from leakscope.phase3 import geography

        modbus = ingest_observation('shodan', {
            'address': '8.8.8.8', 'port': 1502, 'service_type': 'modbus', 'metadata': {},
        })
        self.endpoint('1.1.1.1')
        with patch.object(geography, 'lookup_ips', side_effect=lambda ips: {ip: self.local(ip) for ip in ips}):
            response, data = self.get({'category': 'ot', 'technology': 'modbus'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([row['endpoint_id'] for row in data['results']], [str(modbus.pk)])
            _, empty = self.get({'category': 'messaging'})
        choices = data['filter_choices']
        self.assertEqual(
            {row['key'] for row in choices['technologies']},
            {row['key'] for row in catalog()},
        )
        self.assertEqual(
            {row['key']: row['label'] for row in choices['families'] if row['key'] in CATEGORY_LABELS},
            CATEGORY_LABELS,
        )
        self.assertEqual(empty['filter_choices'], choices)
        self.assertEqual(empty['features'], [])

    def test_country_code_aggregates_do_not_split_on_provider_label_variants(self):
        from leakscope.phase3 import geography
        self.endpoint('8.8.8.8',ip_str='8.8.8.8',location={'country_code':'US','country_name':'United States','latitude':40,'longitude':-70})
        self.endpoint('1.1.1.1',ip_str='1.1.1.1',location={'country_code':'US','country_name':'USA','latitude':40,'longitude':-70})
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:self.local(ip) for ip in ips}):
            response,data=self.get({'source':'shodan'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(len(data['countries']),1);self.assertEqual(data['countries'][0]['code'],'US');self.assertEqual(data['countries'][0]['count'],2)

    def test_hidden_proof_and_unrelated_org_records_are_excluded(self):
        from leakscope.phase3 import geography
        selected=self.endpoint();other=self.endpoint('1.1.1.1');hidden=self.endpoint('9.9.9.9');hidden.observations.update(hidden=True)
        org=graph.node('organization','Fixture Inc');proof=graph.evidence(org,'user_scope',{});graph.relation(org,graph._service(selected),'user_scope',proof)
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:self.local(ip) for ip in ips}):
            response,data=self.get({'organization_id':str(org.pk)})
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(data['counts']['scoped_endpoints'],1);self.assertNotIn(str(other.pk),json.dumps(data));self.assertNotIn(str(hidden.pk),json.dumps(data))
        self.assertEqual(self.get(endpoint_id=str(hidden.pk))[0].status_code,404)

    def test_provider_comparison_is_bound_to_matching_ip_and_source_observation(self):
        from leakscope.phase3 import geography
        endpoint=self.endpoint('example.test',resolved_ip='8.8.8.8',ip_str='8.8.8.8',location={'country_code':'US','latitude':40.1,'longitude':-70})
        self.endpoint('example.test','netlas',resolved_ip='1.1.1.1',ip='1.1.1.1',geo={'country':'AU','location':{'lat':-20,'lon':130}})
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:self.local(ip) for ip in ips}):
            response,data=self.get(endpoint_id=str(endpoint.pk))
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(data['ip'],'1.1.1.1')
        self.assertEqual(data['providers']['shodan']['status'],'ip_mismatch');self.assertEqual(data['comparisons']['shodan']['status'],'not_comparable')
        self.assertEqual(data['comparisons']['netlas']['status'],'country_differs')
        self.assertEqual(data['providers']['zoomeye']['status'],'not_collected')

    def test_unknown_selected_ip_does_not_assert_provider_disagreement(self):
        from leakscope.phase3 import geography
        endpoint=self.endpoint('example.test',resolved_ip='8.8.8.8',ip_str='8.8.8.8',location={'country_code':'US','latitude':40,'longitude':-70})
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:self.local(ip) for ip in ips}):
            response,data=self.get({'source':'netlas'},endpoint_id=str(endpoint.pk))
        self.assertEqual(response.status_code,200,response.content);self.assertIsNone(data['ip'])
        self.assertEqual(data['providers']['shodan']['status'],'located');self.assertEqual(data['comparisons']['shodan']['status'],'not_comparable')

    def test_unlocated_rows_are_retained_and_provider_overlays_are_explicit(self):
        from leakscope.phase3 import geography
        self.endpoint('example.test');self.endpoint('8.8.8.8',ip_str='8.8.8.8',location={'country_code':'US','latitude':40,'longitude':-70})
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:dict(self.local(ip),latitude=None,longitude=None,status='not_found') for ip in ips}):
            _,local=self.get();_,provider=self.get({'source':'shodan'})
        self.assertEqual(local['counts']['unlocated'],2);self.assertEqual(len(local['results']),2);self.assertEqual(provider['counts']['located'],1)

    def test_sampling_limits_are_honest_and_filters_validate(self):
        from leakscope.phase3 import geography
        for i in range(4):self.endpoint('8.8.8.'+str(i+1))
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:self.local(ip) for ip in ips}):
            response,data=self.get({'sample_limit':2,'limit':1})
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(data['counts']['scoped_endpoints'],4);self.assertEqual(data['counts']['sampled_endpoints'],2);self.assertTrue(data['truncated']);self.assertEqual(len(data['features']),1)
        for params in [{'source':'guess'},{'count_mode':'rows'},{'sample_limit':5001},{'limit':2001},{'since':'yesterday'},{'country':'https://evil.test'}]:
            self.assertEqual(self.get(params)[0].status_code,400)

    def test_latest_observation_and_country_filter_do_not_use_hidden_payloads_or_network(self):
        from leakscope.phase3 import geography
        endpoint=self.endpoint(ip_str='8.8.8.8',location={'country_code':'US','latitude':40,'longitude':-70})
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:self.local(ip) for ip in ips}),patch('requests.get',side_effect=AssertionError('No HTTP')),patch('socket.getaddrinfo',side_effect=AssertionError('No DNS')):
            _,selected=self.get({'country':'US','continent':'NA'});_,excluded=self.get({'country':'DE'})
        self.assertEqual(selected['counts']['matched_endpoints'],1);self.assertEqual(excluded['counts']['matched_endpoints'],0)

    def test_invalid_or_foreign_provider_geo_cannot_create_map_point(self):
        from leakscope.phase3 import geography
        self.endpoint('8.8.8.8',ip_str='1.1.1.1',location={'country_code':'US','latitude':40,'longitude':-70})
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:self.local(ip) for ip in ips}):
            response,data=self.get({'source':'shodan'})
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(data['features'],[])

    def test_unknown_area_filters_retain_unlocated_records(self):
        self.endpoint('example.test')
        response,country=self.get({'country':'unknown'})
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(country['counts']['matched_endpoints'],1)
        response,continent=self.get({'continent':'unknown'})
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(continent['counts']['matched_endpoints'],1)

    def test_hostname_keeps_last_retained_ip_when_new_capture_has_no_ip(self):
        from leakscope.phase3 import geography
        endpoint=self.endpoint('example.test',resolved_ip='8.8.8.8',ip_str='8.8.8.8',location={'country_code':'US','latitude':40,'longitude':-70})
        ServiceObservation.objects.create(endpoint=endpoint,provider='fetch:screenshot',fingerprint='new capture without ip',data={'metadata':{}})
        with patch.object(geography,'lookup_ips',side_effect=lambda ips:{ip:self.local(ip) for ip in ips}):
            response,data=self.get(endpoint_id=str(endpoint.pk))
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(data['ip'],'8.8.8.8')

    def test_geo_projection_does_not_materialize_unrelated_large_json_subtrees(self):
        from leakscope.phase3.geography import _saved_rows
        endpoint=self.endpoint()
        ServiceObservation.objects.create(endpoint=endpoint,provider='netlas',fingerprint='big geo',data={'metadata':{'ip':'8.8.8.8','geo':{'country':'US','location':{'lat':40,'lon':-70},'unused':'x'*600000}}})
        latest,providers=_saved_rows([endpoint])
        for row in [*latest,*providers]:
            for value in row.values():
                self.assertFalse(isinstance(value,(dict,list)),'Projection must not materialize JSON containers')
                if isinstance(value,str):self.assertLessEqual(len(value),512)


class DatabaseFailureTests(SimpleTestCase):
    def test_decoder_runtime_error_is_unavailable_without_leaking_paths(self):
        from leakscope.phase3 import geography
        with TemporaryDirectory() as directory:
            path=Path(directory)/'city.mmdb';path.write_bytes(b'fixture')
            with override_settings(LEAKSCOPE_GEOIP_FILE=str(path)),patch.object(geography,'_open_reader',side_effect=RuntimeError('private corrupted dataset path')):
                result=geography.lookup_ip('8.8.8.8')
            self.assertEqual(result['status'],'unavailable');self.assertNotIn('private',json.dumps(result))
