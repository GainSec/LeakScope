import json
from unittest.mock import patch
from django.test import TestCase


class CertificateStorageTests(TestCase):
    def test_empty_input_hashes_from_lite_stream_are_not_certificate_fingerprints(self):
        from leakscope.phase3.certificate_store import store_certificate
        from hashlib import sha256,sha1
        raw={'sha256':sha256(b'').hexdigest(),'fingerprint':sha1(b'').hexdigest(),'domains':['example.com']}
        record=store_certificate({'certificate':raw},'ac'*32)
        self.assertEqual(record.fingerprint,'')
        self.assertEqual(record.data['certificate']['sha256'],raw['sha256'])
        raw['sha256']='ab'*32
        self.assertEqual(store_certificate({'certificate':raw},'ad'*32).fingerprint,'ab'*32)
    def event(self, names=None):
        return {'message_type': 'certificate_update', 'data': {'seen': 1794124800,
            'leaf_cert': {'all_domains': names or ['*.gainsec.com', 'gainsec.com', 'api.gainsec.com'],
                          'sha256': 'ab' * 32, 'serial_number': '123', 'issuer': {'CN': 'Fixture CA'}}}}

    def test_observe_only_preserves_wildcards_and_stores_no_assumed_services(self):
        from leakscope_app.models import WatchRule, CertificateRecord, CertificateName, CertificateWatchMatch, ServiceEndpoint
        from leakscope.phase3.certstream import process_event
        watch = WatchRule.objects.create(name='GainSec', domains=['gainsec.com'], auto_fetch=False)
        result = process_event(self.event(), watches=[watch])
        self.assertEqual(result['matched'], 3)
        self.assertEqual(CertificateRecord.objects.count(), 1)
        self.assertEqual(set(CertificateName.objects.values_list('name', flat=True)), {'*.gainsec.com', 'gainsec.com', 'api.gainsec.com'})
        self.assertEqual(CertificateWatchMatch.objects.get().watch_id, watch.pk)
        self.assertEqual(ServiceEndpoint.objects.count(), 0)

    def test_wildcard_only_certificate_matches_domain_family(self):
        from leakscope_app.models import WatchRule, CertificateRecord
        from leakscope.phase3.certstream import process_event
        watch = WatchRule.objects.create(name='Subdomains', domains=['*.gainsec.com'], auto_fetch=False)
        result = process_event(self.event(['*.gainsec.com']), watches=[watch])
        self.assertEqual(result['matched'], 1)
        self.assertEqual(CertificateRecord.objects.count(), 1)
        self.assertEqual(process_event(self.event(['gainsec.com']), watches=[watch])['matched'], 0)

    def test_repeat_events_dedupe_certificates_and_watch_matches_and_keep_last_seen(self):
        from leakscope_app.models import WatchRule, CertificateRecord, CertificateWatchMatch
        from leakscope.phase3.certstream import process_event
        watch = WatchRule.objects.create(name='GainSec', domains=['gainsec.com'], auto_fetch=False)
        process_event(self.event(), watches=[watch]); process_event(self.event(), watches=[watch])
        self.assertEqual(CertificateRecord.objects.count(), 1)
        self.assertEqual(CertificateWatchMatch.objects.count(), 1)
        watch.delete()
        self.assertEqual(CertificateRecord.objects.count(), 1)
        self.assertIsNone(CertificateWatchMatch.objects.get().watch_id)

    @patch('socket.getaddrinfo', side_effect=AssertionError('No network for local certificate search'))
    def test_local_certificate_search_is_domain_bounded_and_network_free(self, network):
        from leakscope_app.models import WatchRule
        from leakscope.phase3.certstream import process_event
        watch = WatchRule.objects.create(name='Local', keywords=['gainsec'], auto_fetch=False)
        process_event(self.event(), watches=[watch])
        process_event(self.event(['notgainsec.com']), watches=[watch])
        response = self.client.get('/api/assets/certificates?q=gainsec.com&mode=domain')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['source'], 'local_certstream')
        self.assertFalse(response.json()['external_sources_enabled'])

    def test_observe_only_watch_needs_no_ports_or_fetch_profile(self):
        response = self.client.post('/api/operations/watches', json.dumps({'name':'Observe', 'domains':['gainsec.com'],
            'auto_fetch':False, 'ports':[], 'actions':[]}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['watch']['ports'], [])
