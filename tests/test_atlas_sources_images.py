from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import Mock,patch
from django.test import SimpleTestCase,RequestFactory,override_settings

class AtlasImageryLimitsTests(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.settings=override_settings(DATA_DIR=Path(self.tmp.name),LEAKSCOPE_PANORAMAX_API_URL='http://panoramax:5000/api');self.settings.enable();self.addCleanup(self.settings.disable)
    def test_image_proxy_accepts_real_pixels_and_rejects_active_documents(self):
        from PIL import Image
        from leakscope_app import atlas_sources as source
        identifier='22222222-2222-4222-8222-222222222222';url='http://panoramax:5000/api/pictures/'+identifier+'/sd.jpg'
        output=BytesIO();Image.new('RGB',(32,16),'blue').save(output,format='PNG')
        for body,status in [(output.getvalue(),200),(b'<svg onload="alert(1)"></svg>',502)]:
            response=Mock(status_code=200);response.iter_content.return_value=[body]
            with patch.dict(source._ASSETS,{('http://panoramax:5000/api',identifier):{'image':url}},clear=True),patch('requests.get',return_value=response) as call:
                result=source.imagery_image(RequestFactory().get('/api/atlas/imagery/images/'+identifier),identifier)
            self.assertEqual(result.status_code,status)
            self.assertFalse(call.call_args.kwargs['allow_redirects'])
            if status==200:self.assertEqual(result['Content-Type'],'image/png');self.assertEqual(result.content,body)
            response.close.assert_called_once()
    def test_unavailable_or_redirecting_response_is_closed(self):
        from leakscope_app.atlas_sources import _bytes
        response=Mock(status_code=302)
        with self.assertRaises(ValueError):_bytes(response,100)
        response.close.assert_called_once()
    def test_slow_stream_has_a_total_read_deadline(self):
        from leakscope_app.atlas_sources import _bytes
        response=Mock(status_code=200);response.iter_content.return_value=[b'a']
        with patch('leakscope_app.atlas_sources.time.monotonic',side_effect=[0,20]):
            with self.assertRaises(ValueError):_bytes(response,100)
        response.close.assert_called_once()
    def test_invalid_saved_configuration_never_exposes_rejected_values(self):
        import json
        from leakscope_app import atlas_sources as source
        for saved in [{'mode':'external','api_url':'https://user:secret@example.test/api'}, {'mode':'local','enabled':'false','api_url':'https://example.test/api'}]:
            source._config_path().write_text(json.dumps(saved))
            self.assertEqual(source._config(),{'enabled':True,'mode':'local','api_url':'http://panoramax:5000/api'})
    def test_asset_origin_uses_normalized_host_and_effective_port(self):
        from leakscope_app.atlas_sources import _asset_url
        identifier='22222222-2222-4222-8222-222222222222'
        url='https://EXAMPLE.test:443/api/pictures/'+identifier+'/sd.jpg'
        self.assertEqual(_asset_url(url,'https://example.test/api',identifier),url)
        for bad in [url.replace(':443',':444'),url.replace('EXAMPLE.test','other.test'),url.replace('/sd.jpg','/%2e%2e/sd.jpg')]:
            self.assertIsNone(_asset_url(bad,'https://example.test/api',identifier))
    def test_decompression_bomb_is_bounded_error(self):
        from PIL import Image
        from leakscope_app import atlas_sources as source
        identifier='22222222-2222-4222-8222-222222222222'
        response=Mock(status_code=200);response.iter_content.return_value=[b'pixels']
        with patch.dict(source._ASSETS,{('http://panoramax:5000/api',identifier):{'image':'http://panoramax:5000/api/pictures/'+identifier+'/sd.jpg'}},clear=True),patch('requests.get',return_value=response),patch('PIL.Image.open',side_effect=Image.DecompressionBombError('oversized')):
            result=source.imagery_image(RequestFactory().get('/'),identifier)
        self.assertEqual(result.status_code,502)
    def test_malformed_catalog_is_supported_failure(self):
        from leakscope_app.atlas_sources import imagery_nearby
        response=Mock(status_code=200);response.iter_content.return_value=[b'[]']
        with patch('requests.get',return_value=response):
            result=imagery_nearby(RequestFactory().get('/',{'lat':'48','lon':'2'}))
        import json
        self.assertEqual(result.status_code,200)
        self.assertFalse(json.loads(result.content)['available'])
    def test_raw_stream_timeout_is_translated_and_response_closed(self):
        from urllib3.response import HTTPResponse
        from urllib3.exceptions import ReadTimeoutError
        import requests
        from leakscope_app.atlas_sources import _bytes
        response=Mock(status_code=200);response.raw=HTTPResponse()
        with patch.object(response.raw,'read1',side_effect=ReadTimeoutError(None,None,'read deadline')):
            with self.assertRaises(requests.RequestException):_bytes(response,100)
        response.close.assert_called_once()
