"""Entra-only public discovery: fixed request, strict issuer and versioned cache."""
import json
from datetime import timedelta
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from leakscope.phase3.public_metadata import (parse_identities, collect_public_metadata,
    ingest_public_metadata, IDENTITY_POLICY_VERSION)

TENANT = '11111111-2222-4333-8444-555555555555'


class EntraTests(SimpleTestCase):
    def test_exact_official_issuer_hosts_keep_cloud_labels(self):
        clouds = {'login.microsoftonline.com': 'Microsoft public cloud',
                  'login.microsoftonline.us': 'Microsoft US Government cloud',
                  'login.partner.microsoftonline.cn': 'Microsoft China cloud'}
        for host, cloud in clouds.items():
            values = parse_identities('entra', json.dumps({'issuer': 'https://' + host + '/' + TENANT + '/v2.0'}))
            self.assertEqual(values[0]['value'], TENANT)
            self.assertEqual(values[0]['attributes']['cloud'], cloud)
        for issuer in ['https://login.microsoftonline.us.evil.test/' + TENANT + '/v2.0',
                       'https://login.microsoftonline.cn/' + TENANT + '/v2.0',
                       'http://login.microsoftonline.us/' + TENANT + '/v2.0',
                       'https://user@login.microsoftonline.us/' + TENANT + '/v2.0',
                       'https://@login.microsoftonline.us/' + TENANT + '/v2.0',
                       'https://login.microsoftonline.us:444/' + TENANT + '/v2.0',
                       'https://login.microsoftonline.us:invalid/' + TENANT + '/v2.0',
                       'https://login.microsoftonline.us/{tenantid}/v2.0',
                       'https://login.microsoftonline.us/common/v2.0',
                       'https://login.microsoftonline.us/' + TENANT + '/v2.0?q=other']:
            self.assertEqual(parse_identities('entra', json.dumps({'issuer': issuer})), [])

    def test_retired_methods_are_rejected_without_collection(self):
        for method in ['android', 'apple', 'ads', 'webauthn', 'a2a']:
            with self.assertRaises(ValueError):
                parse_identities(method, '{}')

    def test_legacy_budget_still_makes_only_one_fixed_request(self):
        payload = json.dumps({'issuer': 'https://login.microsoftonline.us/' + TENANT + '/v2.0'})
        for budget in [1, 6]:
            with patch('leakscope.phase3.public_metadata._read', return_value=(200, payload)) as read:
                result = collect_public_metadata('EXAMPLE.COM', max_requests=budget)
            read.assert_called_once()
            self.assertEqual(read.call_args.args[0], 'https://login.microsoftonline.com/example.com/v2.0/.well-known/openid-configuration')
            self.assertEqual(result['requests'], 1)
            self.assertEqual(result['attempts'][0]['method'], 'entra')
            self.assertEqual(result['identity_policy_version'], IDENTITY_POLICY_VERSION)
            self.assertEqual(len(result['identities']), 1)

    def test_redirects_unknown_issuer_and_errors_do_not_infer_a_tenant(self):
        for response, status in [((302, None), 'redirect_not_followed'), ((404, None), 'not_published'),
                                 ((200, json.dumps({'issuer': 'https://evil.test/' + TENANT + '/v2.0'})), 'no_supported_declarations')]:
            with patch('leakscope.phase3.public_metadata._read', return_value=response):
                result = collect_public_metadata('example.com')
            self.assertEqual(result['identities'], [])
            self.assertEqual(result['attempts'][0]['status'], status)
        for budget in [True, 0, 7, '1']:
            with self.assertRaises(ValueError):
                collect_public_metadata('example.com', max_requests=budget)

    def test_transport_does_not_follow_redirects_and_bounds_response(self):
        class Response:
            status_code = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def iter_content(self, size): yield b'x' * (256 * 1024 + 1)
        with patch('requests.get', return_value=Response()) as get:
            result = collect_public_metadata('example.com')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertEqual(result['attempts'][0]['status'], 'unavailable_or_invalid')
        self.assertEqual(result['identities'], [])


class EntraJobTests(TestCase):
    def test_legacy_multi_method_cache_cannot_suppress_corrected_demo(self):
        from leakscope.phase3 import asset_jobs as jobs, relationships as store
        root = store.node('domain', 'example.com')
        legacy = jobs._digest({'kind': 'identity_metadata', 'target': 'example.com', 'max_requests': 6, 'aliases': []})
        store.evidence(root, 'enrichment:identity_metadata', {'cache_key': legacy, 'result': {'identities': []}},
                       expires_at=timezone.now() + timedelta(hours=1))
        normal = jobs._spec('identity_metadata', 'example.com', max_requests=6)
        demo = jobs._spec('identity_metadata', 'example.com', max_requests=6, demo=True)
        self.assertEqual(normal['max_requests'], 1)
        self.assertEqual(demo['max_requests'], 1)
        self.assertNotEqual(normal['cache_key'], legacy)
        self.assertIsNone(jobs._mode_cached(root, demo))
        proof = store.evidence(root, 'enrichment:identity_metadata', {'cache_key': normal['cache_key'], 'result': {'identities': []}},
                               expires_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(jobs._mode_cached(root, demo).pk, proof.pk)

    def test_ingestion_preserves_existing_retired_evidence_but_adds_only_entra(self):
        from leakscope.phase3 import relationships as store
        from leakscope_app.models import AssetEvidence
        root = store.node('domain', 'example.com')
        old = store.evidence(root, 'public_identity:android', {'namespace': 'android.app', 'value': 'com.example.app'})
        ingest_public_metadata({'target': root.value, 'identities': [
            {'namespace': 'android.app', 'value': 'com.other.app', 'method': 'android'},
            {'namespace': 'entra.tenant', 'value': TENANT, 'method': 'entra', 'source_url': 'https://login.microsoftonline.com/'}]})
        self.assertTrue(AssetEvidence.objects.filter(pk=old.pk).exists())
        self.assertEqual(AssetEvidence.objects.filter(source='public_identity:android').count(), 1)
        self.assertEqual(AssetEvidence.objects.filter(source='public_identity:entra').count(), 1)
