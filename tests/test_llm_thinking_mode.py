import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase,TestCase
from leakscope.intelligence.clients import ask_endpoint
from leakscope.services.config import get_config
from leakscope.services.integrations import save_profiles
from leakscope_app.models import IntegrationProfile
from tests.test_intelligence_clients import Response

class ThinkingRequestTests(SimpleTestCase):
    config={'LLM_API_BASE':'http://model.fixture/v1','LLM_MODEL':'fixture-model'}

    @patch('leakscope.intelligence.clients.requests.post')
    def test_default_has_no_nonstandard_request_body_options(self,post):
        for extra in [{},{'LLM_THINKING_MODE':''},{'LLM_THINKING_MODE':'default'}]:
            post.return_value=Response({'choices':[{'message':{'content':'Fixture answer'}}]})
            ask_endpoint('Fixture question',{},dict(self.config,**extra))
            self.assertEqual(set(post.call_args.kwargs['json']),{'model','messages','max_tokens'})
            self.assertNotIn('chat_template_kwargs',post.call_args.kwargs['json'])

    @patch('leakscope.intelligence.clients.requests.post')
    def test_explicit_modes_send_only_documented_boolean_flag(self,post):
        for mode,value in [('disabled',False),('enabled',True)]:
            with self.subTest(mode=mode):
                post.return_value=Response({'choices':[{'message':{'content':'Fixture answer'}}]})
                ask_endpoint('Fixture question',{},dict(self.config,LLM_THINKING_MODE=mode))
                self.assertEqual(post.call_args.kwargs['json'].get('chat_template_kwargs'),{'enable_thinking':value})

    @patch('leakscope.intelligence.clients.requests.post')
    def test_invalid_modes_fail_before_contacting_model(self,post):
        post.return_value=Response({'choices':[{'message':{'content':'Fixture answer'}}]})
        for mode in [None,True,False,0,[],{},'auto','false','custom']:
            with self.subTest(mode=mode),self.assertRaisesMessage(ValueError,'LLM_THINKING_MODE'):
                ask_endpoint('Fixture question',{},dict(self.config,LLM_THINKING_MODE=mode))
        post.assert_not_called()

class ThinkingProfileTests(TestCase):
    def test_settings_roundtrip_mode_preserves_endpoint_model_and_encrypted_secret(self):
        save_profiles({'llm':{'base_url':'http://model.fixture/v1','model':'fixture','api_key':'private-key-fixture'}},{})
        before=IntegrationProfile.objects.get(pk='llm').encrypted_secret
        response=self.client.post('/api/intelligence/settings',json.dumps({'llm':{'thinking_mode':'disabled'}}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['llm']['thinking_mode'],'disabled')
        cfg=get_config()['config']
        self.assertEqual(cfg['LLM_THINKING_MODE'],'disabled')
        self.assertEqual(cfg['LLM_API_BASE'],'http://model.fixture/v1')
        self.assertEqual(cfg['LLM_MODEL'],'fixture')
        self.assertEqual(IntegrationProfile.objects.get(pk='llm').encrypted_secret,before)
        self.assertNotIn('private-key-fixture',response.content.decode())
        self.assertEqual(self.client.get('/api/intelligence/settings').json()['llm']['thinking_mode'],'disabled')

    def test_unknown_values_and_arbitrary_extra_body_are_rejected_atomically(self):
        for value in [True,None,{},'auto','false']:
            with self.subTest(value=value):
                response=self.client.post('/api/intelligence/settings',json.dumps({'llm':{'thinking_mode':value},'netlas':{'api_key':'fixture'}}),content_type='application/json')
                self.assertEqual(response.status_code,400)
        response=self.client.post('/api/intelligence/settings',json.dumps({'llm':{'chat_template_kwargs':{'arbitrary':True}}}),content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(IntegrationProfile.objects.count(),0)

    def test_unset_profile_response_defaults_to_server_default(self):
        self.assertEqual(self.client.get('/api/intelligence/settings').json()['llm'].get('thinking_mode'),'default')

    def test_supported_environment_file_and_profile_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.json';config.write_text(json.dumps({'config':{'LLM_THINKING_MODE':'disabled'}}))
            with patch.dict(os.environ,{'LEAKSCOPE_CONFIG_FILE':str(config),'LLM_THINKING_MODE':'enabled'}):
                self.assertEqual(get_config()['config'].get('LLM_THINKING_MODE'),'disabled')
                save_profiles({'llm':{'thinking_mode':'default'}},get_config()['config'])
                self.assertEqual(get_config()['config']['LLM_THINKING_MODE'],'default')
