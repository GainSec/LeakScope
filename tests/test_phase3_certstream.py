import json
from unittest.mock import patch

from django.test import TestCase
from leakscope_app.models import (ServiceEndpoint, ServiceObservation, StreamSettings, WatchRule,
                                  CertificateRecord, CertificateWatchMatch)


class CertstreamTests(TestCase):
    def event(self, domains=None):
        return {'message_type':'certificate_update','data':{'seen':1770000000,'cert_index':22,
            'leaf_cert':{'all_domains':domains or ['api.example.com'],'sha256':'ab'*32,
                         'issuer':{'CN':'Fixture CA'},'serial_number':'42','not_before':1700000000,'not_after':1800000000},
            'source':{'url':'https://log.example.invalid'}}}

    def test_domain_matching_boundaries_wildcards_and_idna(self):
        from leakscope.phase3.certstream import domain_matches
        self.assertTrue(domain_matches('api.example.com', 'example.com'))
        self.assertFalse(domain_matches('badexample.com', 'example.com'))
        self.assertFalse(domain_matches('example.com.evil.invalid', 'example.com'))
        self.assertTrue(domain_matches('bücher.example', 'xn--bcher-kva.example'))
        self.assertTrue(domain_matches('deep.api.example.com', '*.example.com'))
        self.assertFalse(domain_matches('example.com', '*.example.com'))

    @patch('leakscope.phase3.asset_jobs.queue_watch_capture')
    def test_matches_persist_certificate_evidence_tags_and_queue_explicit_followup_once(self, capture):
        from leakscope.phase3.certstream import process_event
        from leakscope.phase3.state import set_tags, tag_names
        watch=WatchRule.objects.create(name='Fixture',domains=['example.com'],ports=[443],actions=['http'],auto_fetch=True)
        set_tags('watch',watch.pk,['storage'])
        result=process_event(self.event())
        self.assertEqual(result['matched'],1)
        certificate=CertificateRecord.objects.get()
        self.assertEqual(certificate.domains,['api.example.com'])
        self.assertEqual(tag_names('certificate',certificate.pk),['storage'])
        self.assertEqual(CertificateWatchMatch.objects.get().matched_names,['api.example.com'])
        self.assertFalse(ServiceEndpoint.objects.exists())
        self.assertFalse(ServiceObservation.objects.exists())
        self.assertEqual(capture.call_count,1)
        self.assertEqual(capture.call_args.args[0].pk,certificate.pk)
        self.assertEqual(capture.call_args.args[1].pk,watch.pk)
        self.assertEqual(capture.call_args.args[2],'api.example.com')
        process_event(self.event())
        self.assertEqual(capture.call_count,1)
        self.assertEqual(CertificateRecord.objects.count(),1)
        self.assertEqual(CertificateWatchMatch.objects.count(),1)

    @patch('leakscope.phase3.asset_jobs.queue_watch_capture')
    def test_unmatched_and_disabled_watches_store_no_global_firehose(self, capture):
        from leakscope.phase3.certstream import process_event
        WatchRule.objects.create(name='disabled',domains=['example.com'],enabled=False)
        self.assertEqual(process_event(self.event())['matched'],0)
        self.assertFalse(ServiceEndpoint.objects.exists())
        self.assertFalse(CertificateRecord.objects.exists())
        self.assertFalse(CertificateWatchMatch.objects.exists())
        capture.assert_not_called()

    @patch('leakscope.phase3.asset_jobs.queue_watch_capture')
    def test_observe_only_rule_never_fetches_and_invalid_domains_are_dropped(self,capture):
        from leakscope.phase3.certstream import process_event
        WatchRule.objects.create(name='Observe',keywords=['example'],auto_fetch=False)
        result=process_event(self.event(['api.example.com','https://bad.example.com','../example.com','*.wild.example.com']))
        self.assertEqual(result['matched'],2)
        self.assertEqual(result['dropped'],2)
        self.assertEqual(CertificateRecord.objects.get().domains,['*.wild.example.com','api.example.com'])
        self.assertFalse(ServiceEndpoint.objects.exists())
        self.assertFalse(ServiceObservation.objects.exists())
        capture.assert_not_called()

    def test_stream_mode_is_explicit_and_never_falls_back(self):
        from leakscope.phase3.certstream import stream_connection
        settings=StreamSettings.objects.create(mode='builtin')
        self.assertEqual(stream_connection(settings)[0],'ws://certstream:8080/')
        settings.mode='external';settings.external_url='wss://feed.example.invalid/stream'
        self.assertEqual(stream_connection(settings)[0],settings.external_url)
        settings.external_url=''
        with self.assertRaises(ValueError):stream_connection(settings)

    def test_oversized_or_invalid_event_is_not_processed(self):
        from leakscope.phase3.certstream import decode_event
        with self.assertRaises(ValueError):decode_event('x'*(1024*1024+1))
        with self.assertRaises(ValueError):decode_event('[]')
        self.assertEqual(decode_event(json.dumps({'message_type':'heartbeat'}))['message_type'],'heartbeat')

    def test_frame_limit_rejects_before_receiving_large_payload(self):
        from unittest.mock import Mock
        from leakscope.phase3.certstream import _BoundedFrames, MAX_MESSAGE
        receive = Mock()
        with self.assertRaises(ValueError):
            _BoundedFrames(receive, False).recv_strict(MAX_MESSAGE + 1)
        receive.assert_not_called()

    def test_consumer_flushes_health_and_stops_after_fixture_event(self):
        import threading
        from unittest.mock import Mock
        from websocket._abnf import ABNF
        from leakscope.phase3.certstream import consume_stream
        from leakscope_app.models import StreamHealth
        connection=Mock()
        connection.recv_data.return_value=(ABNF.OPCODE_TEXT,json.dumps(self.event()))
        self.assertEqual(consume_stream(stop_event=threading.Event(),max_events=1,connect=lambda url:connection),1)
        health=StreamHealth.objects.get(pk=1)
        self.assertEqual(health.received,1)
        self.assertEqual(health.state,'stopped')
        self.assertIsNotNone(health.heartbeat_at)
        self.assertIsNotNone(health.last_event_at)
        self.assertFalse(ServiceEndpoint.objects.exists())
