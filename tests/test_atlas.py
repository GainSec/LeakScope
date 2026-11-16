import json
from unittest.mock import patch
from django.test import TestCase,RequestFactory,override_settings
from leakscope.phase3.state import ingest_observation,set_tags
from leakscope.phase3 import relationships as store

class AtlasTechnologyTests(TestCase):
    def result(self,ip='192.0.2.1',provider='shodan',technology='redis',**metadata):
        return ingest_observation(provider,{'address':ip,'port':12345,'service_type':technology,'metadata':metadata})
    def request(self,**params):
        from leakscope_app.atlas import technologies
        return technologies(RequestFactory().get('/api/atlas/technologies',params))
    def test_counts_distinct_endpoints_and_never_collects(self):
        ep=self.result();self.result(provider='zoomeye');self.result('192.0.2.2',technology='modbus')
        with patch('requests.get',side_effect=AssertionError('No collection on browse')):
            response=self.request()
        self.assertEqual(response.status_code,200,response.content)
        body=json.loads(response.content);groups={r['key']:r for r in body['groups']}
        self.assertEqual(body['totals']['endpoints'],2)
        self.assertEqual(groups['redis']['endpoints'],1)
        self.assertEqual(groups['modbus']['category'],'ot')
        self.assertIn('provider_reported',groups['redis']['quality'])
    def test_requested_profile_is_not_confirmed_technology(self):
        self.result(technology='elastic',service_type_basis='Requested search profile; identity not verified')
        data=json.loads(self.request().content)
        self.assertEqual(data['totals']['unverified'],1)
        self.assertEqual(data['groups'][0]['key'],'unverified')
        self.assertNotIn('elastic',[r['key'] for r in data['groups']])
    def test_provider_product_can_classify_generic_http_with_stored_evidence(self):
        self.result(technology='http',product='Jenkins')
        groups=json.loads(self.request().content)['groups']
        self.assertIn('jenkins',[r['key'] for r in groups])
    def test_noncatalog_identity_survives_and_drills_down_without_fabricating_requested_identity(self):
        from leakscope_app.atlas import classify
        self.result(technology='http',product='nginx',service='NGINX',app='Apache httpd 2.4',http={'components':{'Grafana Plugin':{}}})
        groups={row['key']:row for row in json.loads(self.request().content)['groups']}
        self.assertIn('nginx',groups);self.assertEqual(groups['nginx']['label'],'nginx')
        self.assertEqual(groups['nginx']['category'],'other');self.assertEqual(groups['nginx']['endpoints'],1)
        self.assertIn('apache httpd 2.4',groups);self.assertIn('grafana plugin',groups)
        self.assertEqual(json.loads(self.request(technology='nginx').content)['totals']['endpoints'],1)
        self.assertEqual(classify({'metadata':{'product':'nginx'}},'shodan'),[('nginx','nginx','other','provider_reported')])
        values=classify({'service_type':'mongo','metadata':{'product':'MongoDB','app':'<svg onload=bad>','service':'bad\nidentity','http':{'components':{'x'*101:{}}}}},'shodan')
        self.assertEqual(values,[('mongo','MongoDB','database','provider_reported')])
        self.assertEqual(classify({'service_type':'http','metadata':{'product':'nginx','service_type_basis':'Requested search profile; identity not verified'}},'shodan')[0][0],'unverified')
    def test_visibility_tags_provider_and_explicit_org_scope(self):
        first=self.result();second=self.result('192.0.2.2');second.observations.update(hidden=True)
        org=store.node('organization','Atlas fixture');service=store._service(first)
        proof=store.evidence(org,'user_scope',{'kind':'service','value':service.value});store.relation(org,service,'user_scope',proof)
        set_tags('endpoint',first.pk,['review'])
        data=json.loads(self.request(organization_id=str(org.pk),tag='review',provider='shodan').content)
        self.assertEqual(data['totals']['endpoints'],1)
        data=json.loads(self.request(provider='netlas').content);self.assertEqual(data['totals']['endpoints'],0)
    def test_invalid_filters_fail_without_turning_missing_into_zero(self):
        self.assertEqual(self.request(since='not-a-date').status_code,400)

    def test_technology_and_category_drilldown_use_same_identity_as_cards(self):
        self.result(technology='http',product='Jenkins')
        self.result('192.0.2.2',technology='modbus')
        self.result('192.0.2.3',technology='elastic',service_type_basis='Requested search profile; identity not verified')
        self.assertEqual(json.loads(self.request(technology='jenkins').content)['totals']['endpoints'],1)
        self.assertEqual(json.loads(self.request(technology='unverified').content)['totals']['endpoints'],1)
        data=json.loads(self.request(category='ot').content)
        self.assertEqual(data['totals']['endpoints'],1)
        self.assertEqual([row['key'] for row in data['groups']],['modbus'])

class AtlasIntegrationTests(TestCase):
    @override_settings(DEBUG=True)
    def test_three_new_pages_keep_the_existing_catalog(self):
        for path,page in [('/technology-dashboard/','technologies'),('/screenshots/','screenshots'),('/geography/','geography')]:
            response=self.client.get(path);self.assertEqual(response.status_code,200)
            self.assertContains(response,'data-page="'+page+'"');self.assertContains(response,'js/leakscope/atlas.js')
        self.assertContains(self.client.get('/technologies/'),'data-page="catalog"')
    def test_atlas_saved_views_accept_new_filters_without_credentials(self):
        for page in ['technologies','screenshots','geography']:
            response=self.client.post('/api/workspace/views',json.dumps({'name':'Atlas','page':page,'state':{'query':'category=ot&country=US&source=local&count_mode=ips&collection=fixture&latest=1&duplicates=collapse&group=technology&tag_scope=endpoint'}}),content_type='application/json')
            self.assertEqual(response.status_code,201,response.content)
    def test_geo_is_preserved_before_generic_provider_clipping(self):
        from leakscope.phase3.providers import _normalize
        raw={'ip_str':'8.8.8.8','port':443,**{str(i):'x'*8000 for i in range(70)},'location':{'country_code':'US','latitude':37.3,'longitude':-122.0}}
        value=_normalize('shodan',raw,{})
        self.assertIn('_geo',value['metadata']);self.assertEqual(value['metadata']['_geo']['country_code'],'US')
    def test_direct_filter_expands_native_and_fetch_sources(self):
        ingest_observation('fetch:identity',{'address':'192.0.2.9','port':1234,'service_type':'modbus','metadata':{}})
        response=self.client.get('/api/atlas/technologies?provider=direct')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['totals']['endpoints'],1)

class AtlasEvidenceHistoryTests(TestCase):
    def test_derived_geo_copy_does_not_fabricate_unclipped_evidence_changes(self):
        from leakscope.phase3.analysis import stable_evidence
        raw={'metadata':{'location':{'country_code':'US','latitude':1,'longitude':2},'timestamp':'2026-01-01T00:00:00Z'}}
        normalized={'metadata':{**raw['metadata'],'_geo':{'ip':'8.8.8.8','country_code':'US','latitude':1,'longitude':2,'source_date':'2026-01-01T00:00:00Z'}}}
        self.assertEqual(stable_evidence(raw),stable_evidence(normalized))
    def test_normalized_only_geo_preserves_changes_but_not_source_scan_dates(self):
        from leakscope.phase3.analysis import stable_evidence
        first={'metadata':{'_geo':{'ip':'8.8.8.8','latitude':1,'longitude':2,'source_date':'2026-01-01T00:00:00Z'}}}
        refreshed={'metadata':{'_geo':{**first['metadata']['_geo'],'source_date':'2026-01-02T00:00:00Z'}}}
        moved={'metadata':{'_geo':{**refreshed['metadata']['_geo'],'latitude':3}}}
        self.assertEqual(stable_evidence(first),stable_evidence(refreshed));self.assertNotEqual(stable_evidence(first),stable_evidence(moved))
