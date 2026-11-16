import json
from django.test import TestCase, override_settings
from leakscope_app.models import IntegrationProfile
from leakscope.services.config import get_config


class IntegrationProfileTests(TestCase):
    def test_settings_can_configure_endpoint_and_key_without_returning_secret(self):
        r = self.client.post('/api/intelligence/settings', json.dumps({'llm': {'base_url': 'http://host.docker.internal:11434/v1', 'model': 'fixture', 'api_key': 'private-fixture-key'}, 'netlas': {'api_key': 'netlas-fixture-key'}}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn('private-fixture-key', r.content.decode())
        self.assertTrue(r.json()['llm']['configured'])
        self.assertTrue(r.json()['llm']['key_configured'])
        self.assertEqual(get_config()['config']['LLM_API_KEY'], 'private-fixture-key')
        stored = IntegrationProfile.objects.get(name='llm').encrypted_secret
        self.assertNotIn('private-fixture-key', stored)
        self.assertNotIn('private-fixture-key', json.dumps(IntegrationProfile.objects.get(name='llm').values))

    def test_blank_omitted_key_preserves_and_explicit_empty_clears(self):
        url = '/api/intelligence/settings'
        send = lambda data: self.client.post(url, json.dumps(data), content_type='application/json')
        send({'llm': {'base_url': 'http://localhost:8001/v1', 'model': 'first', 'api_key': 'fixture-secret'}})
        send({'llm': {'model': 'second'}})
        self.assertEqual(get_config()['config']['LLM_API_KEY'], 'fixture-secret')
        send({'llm': {'api_key': ''}})
        self.assertEqual(get_config()['config']['LLM_API_KEY'], '')

    def test_invalid_settings_are_rejected_before_partial_save(self):
        r = self.client.post('/api/intelligence/settings', json.dumps({'netlas': {'api_key': 'valid'}, 'llm': {'base_url': 'file:///tmp/private'}}), content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(IntegrationProfile.objects.count(), 0)

    def test_endpoint_edit_preserves_ciphertext_when_site_secret_is_temporarily_wrong(self):
        from leakscope.services.integrations import save_profiles
        save_profiles({'llm': {'model': 'first', 'api_key': 'recoverable-secret'}}, {})
        ciphertext = IntegrationProfile.objects.get(name='llm').encrypted_secret
        with override_settings(SECRET_KEY='temporarily-wrong-site-secret'):
            config = get_config()['config']
            self.assertEqual(config['LLM_API_KEY'], '')
            save_profiles({'llm': {'model': 'second'}}, config)
            self.assertEqual(IntegrationProfile.objects.get(name='llm').encrypted_secret, ciphertext)
        self.assertEqual(get_config()['config']['LLM_API_KEY'], 'recoverable-secret')
