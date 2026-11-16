import json,tempfile
from pathlib import Path
from unittest.mock import patch,Mock
from django.test import SimpleTestCase,RequestFactory,override_settings

class AtlasSourcesTests(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.settings=override_settings(DATA_DIR=Path(self.tmp.name),LEAKSCOPE_PANORAMAX_API_URL='http://panoramax:5000/api');self.settings.enable();self.addCleanup(self.settings.disable)
    def test_local_map_style_has_no_external_resource_urls(self):
        from leakscope_app.atlas_sources import map_style,map_status
        response=map_style(RequestFactory().get('/api/atlas/maps/style'))
        self.assertEqual(response.status_code,200)
        data=json.loads(response.content)
        self.assertTrue(data['sources']['countries']['data'].startswith('/static/'))
        self.assertTrue(data['glyphs'].startswith('/static/'))
        self.assertNotIn('http:',json.dumps({k:v for k,v in data.items() if k!='metadata'}))
        status=json.loads(map_status(RequestFactory().get('/api/atlas/maps/status')).content)
        self.assertTrue(status['installed']);self.assertIn('overview',status['coverage'])
    def test_config_persists_only_validated_endpoint_without_network(self):
        from leakscope_app.atlas_sources import configuration
        factory=RequestFactory()
        with patch('requests.get',side_effect=AssertionError('No network on config browse')):
            response=configuration(factory.post('/api/atlas/config',json.dumps({'api_url':'http://panoramax:5000/api','mode':'local','enabled':True}),content_type='application/json'))
            self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(json.loads(configuration(factory.get('/api/atlas/config')).content)['imagery']['api_url'],'http://panoramax:5000/api')
        for url in ['file:///etc/passwd','http://user:password@host/api','http://host/api?token=secret','javascript:alert(1)']:
            response=configuration(factory.post('/api/atlas/config',json.dumps({'api_url':url,'mode':'external'}),content_type='application/json'));self.assertEqual(response.status_code,400,response.content)
    def test_nearby_is_explicit_bounded_same_origin_and_proxied(self):
        from leakscope_app.atlas_sources import imagery_nearby
        feature={'type':'Feature','id':'11111111-1111-4111-8111-111111111111','geometry':{'type':'Point','coordinates':[2.01,48.01]},'properties':{'datetime':'2026-01-01T00:00:00Z','view:type':'equirectangular'},'assets':{'thumbnail':{'href':'http://panoramax:5000/api/pictures/11111111-1111-4111-8111-111111111111/thumb.jpg'},'hd':{'href':'http://panoramax:5000/api/pictures/11111111-1111-4111-8111-111111111111/hd.jpg'}}}
        body=json.dumps({'features':[feature]}).encode();response=Mock(status_code=200);response.iter_content.return_value=[body]
        with patch('requests.get',return_value=response) as call:
            output=imagery_nearby(RequestFactory().get('/api/atlas/imagery/nearby',{'lat':'48','lon':'2'}))
        self.assertEqual(output.status_code,200,output.content);data=json.loads(output.content)
        self.assertEqual(len(data['results']),1);self.assertTrue(data['results'][0]['image_url'].startswith('/api/atlas/imagery/images/'))
        self.assertFalse(call.call_args.kwargs['allow_redirects']);self.assertLessEqual(call.call_args.kwargs['params']['limit'],10)
    def test_invalid_or_unknown_vicinity_does_not_contact_imagery(self):
        from leakscope_app.atlas_sources import imagery_nearby
        with patch('requests.get',side_effect=AssertionError('No network on invalid coordinates')):
            for value in ['nan','91','inf','bad']:
                self.assertEqual(imagery_nearby(RequestFactory().get('/api/atlas/imagery/nearby',{'lat':value,'lon':'2'})).status_code,400)
