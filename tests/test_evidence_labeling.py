"""Provider hints route collection, evidence levels stay honest, and saved
banners and exchanges become labelled images without contacting anything."""
import hashlib
import json
import tempfile
from datetime import datetime, timezone as dt_timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import path

from leakscope.phase3 import attribution, capture_status, evidence_jobs, evidence_render, protocol_hints
from leakscope.phase3.collection_policy import plan_collection
from leakscope_app import atlas, gallery, operations
from leakscope_app.models import EvidenceArtifact, Run, SavedLookup, ServiceEndpoint, ServiceObservation, Tag, TagAssignment

urlpatterns = [path('api/atlas/technologies', atlas.technologies), path('api/atlas/screenshots', gallery.screenshots),
               path('api/atlas/screenshots/<uuid:artifact_id>/thumbnail', gallery.thumbnail),
               path('api/atlas/evidence/render', gallery.render_evidence),
               path('api/operations/endpoints/<uuid:endpoint_id>', operations.endpoints),
               path('api/operations/artifacts/<uuid:artifact_id>', operations.artifact)]
SSH_BANNER = 'SSH-2.0-OpenSSH_7.4'
SEEN = datetime(2026, 11, 6, 6, 49, 56, tzinfo=dt_timezone.utc)
SAVED = datetime(2026, 11, 12, 0, 51, 28, tzinfo=dt_timezone.utc)


def shodan_data(banner=SSH_BANNER, service='auto', **extra):
    return {'service_type': service, 'title': 'OpenSSH', 'metadata': {'data': banner, 'product': 'OpenSSH', 'version': '7.4', '_provider': 'shodan', **extra}}


class ProtocolHintTests(SimpleTestCase):
    def test_response_text_names_a_protocol_and_a_port_or_product_alone_does_not(self):
        hint = protocol_hints.protocol_hint(shodan_data(), 'shodan')
        self.assertEqual((hint['service'], hint['strength'], hint['basis'], hint['verified']), ('ssh', 'strong', 'banner', False))
        self.assertEqual(hint['decoded'], {'protocol': 'SSH', 'protocol_version': '2.0', 'software': 'OpenSSH_7.4'})
        self.assertEqual(hint['reader']['action'], 'identity')
        product_only = protocol_hints.protocol_hint({'metadata': {'product': 'OpenSSH', 'port': 22}}, 'shodan')
        self.assertEqual((product_only['strength'], product_only['basis']), ('moderate', 'product'))
        self.assertIsNone(protocol_hints.protocol_hint({'metadata': {'port': 12798, 'org': 'Example'}}, 'shodan'))
        self.assertIsNone(protocol_hints.protocol_hint({'metadata': {'data': 'HTTP/1.1 200 OK\r\n\r\n'}}, 'shodan'))

    def test_signatures_cover_supported_and_unsupported_protocols(self):
        cases = {'220 ProFTPD 1.3 Server ready': 'ftp', '@RSYNCD: 31.0': 'rsync', 'RFB 003.008\n': 'vnc', 'RTSP/1.0 200 OK': 'rtsp',
                 '# Server\r\nredis_version:7.2.4\r\n': 'redis', '-NOAUTH Authentication required.\r\n': 'redis', '220 mx.example.net ESMTP Postfix': 'smtp', '* OK IMAP4rev1 ready': 'imap'}
        for text, service in cases.items():
            with self.subTest(text=text):
                self.assertEqual(protocol_hints.decode_banner(text)[0], service)
        self.assertTrue(protocol_hints.reader('rsync')['implemented'])
        missing = protocol_hints.reader('smtp')
        self.assertFalse(missing['implemented'])
        self.assertIn('no native SMTP reader', missing['limitation'])

    def test_a_hint_only_fills_a_placeholder_and_only_when_strong(self):
        saved = [('shodan', shodan_data())]
        self.assertEqual(protocol_hints.routing_service('auto', saved)[0], 'ssh')
        self.assertEqual(protocol_hints.routing_service('', saved)[0], 'ssh')
        self.assertEqual(protocol_hints.routing_service('modbus', saved), ('modbus', None))
        service, hint = protocol_hints.routing_service('auto', [('shodan', {'metadata': {'product': 'OpenSSH'}})])
        self.assertEqual((service, hint['strength']), ('', 'moderate'))
        # LeakScope's own failed attempt is never read back as a provider hint.
        self.assertEqual(protocol_hints.routing_service('auto', [('fetch:tcp', shodan_data())])[0], '')

    def test_provider_normalizer_replaces_the_auto_placeholder(self):
        from leakscope.phase3.providers import _normalize
        raw = {'ip_str': '192.0.2.44', 'port': 12798, 'transport': 'tcp', 'data': SSH_BANNER, 'product': 'OpenSSH', '_shodan': {'module': 'auto'}}
        row = _normalize('shodan', raw, {})
        self.assertEqual(row['service_type'], 'ssh')
        self.assertEqual(row['metadata']['service_type_basis'], protocol_hints.HINT_BASIS)
        self.assertEqual(row['metadata']['protocol_hint']['evidence'], SSH_BANNER)
        silent = _normalize('shodan', dict(raw, data='', product=''), {})
        self.assertEqual(silent['service_type'], '')
        self.assertNotIn('protocol_hint', silent['metadata'])
        named = _normalize('shodan', dict(raw, _shodan={'module': 'redis'}), {})
        self.assertEqual(named['service_type'], 'redis')


class CollectionRoutingTests(TestCase):
    def setUp(self):
        self.endpoint = ServiceEndpoint.objects.create(key='hint-1', address='192.0.2.44', port=12798, service_type='auto', title='OpenSSH')
        self.observation = ServiceObservation.objects.create(endpoint=self.endpoint, provider='shodan', fingerprint='a', data=shodan_data(),
                                                             observed_at=SEEN, last_observed_at=SEEN, first_collected_at=SAVED, last_collected_at=SAVED)

    def test_saved_banner_selects_the_existing_ssh_reader_on_an_unusual_port(self):
        from leakscope.phase3.jobs import endpoint_snapshot
        from leakscope.phase3.capture import supported_actions
        snapshot = endpoint_snapshot(self.endpoint)
        self.assertEqual(snapshot['service_type'], 'ssh')
        self.assertEqual(snapshot['service_type_basis'], protocol_hints.HINT_BASIS)
        self.assertEqual(snapshot['protocol_hint']['evidence'], SSH_BANNER)
        self.assertIn('identity', supported_actions(snapshot))
        plan = plan_collection(snapshot, 'full')
        self.assertEqual(plan['actions'], ['identity'])
        self.assertEqual(plan['routing']['basis'], 'provider_hint')
        self.assertIn('not a verification', plan['routing']['note'])
        self.endpoint.refresh_from_db()
        self.assertEqual(self.endpoint.service_type, 'auto', 'the stored record is not rewritten')

    def test_without_a_hint_the_placeholder_gets_generic_checks_only(self):
        from leakscope.phase3.jobs import endpoint_snapshot
        self.observation.data = shodan_data(banner='', product='')
        self.observation.save()
        snapshot = endpoint_snapshot(self.endpoint)
        self.assertEqual(snapshot['service_type'], '')
        self.assertNotIn('protocol_hint', snapshot)
        self.assertEqual(plan_collection(snapshot, 'full')['actions'], ['check', 'tcp'])

    def test_a_protocol_without_a_reader_is_reported_as_unsupported(self):
        from leakscope.phase3.jobs import endpoint_snapshot
        self.observation.data = shodan_data(banner='220 mx.example.net ESMTP Postfix', product='Postfix smtpd')
        self.observation.save()
        plan = plan_collection(endpoint_snapshot(self.endpoint), 'full')
        self.assertEqual(plan['actions'], ['check', 'tcp'])
        self.assertTrue(any(text.startswith('Unsupported: LeakScope has no native SMTP reader') for text in plan['skipped']), plan['skipped'])
        self.assertEqual(plan['routing']['service'], 'smtp')

    def test_hinted_attempt_that_fails_is_not_recorded_as_confirmed(self):
        from leakscope.phase3.capture import CaptureEvidenceError
        from leakscope.phase3.jobs import fetch_endpoint, endpoint_snapshot
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        with override_settings(LEAKSCOPE_CAPTURE_DIR=Path(folder.name)):
            partial = {'metadata': {'operation': 'native identity attempt', 'identity_verified': False, 'partial': True,
                                    'error': 'The capture timed out because its deadline expired.', 'error_code': 'timeout'}, 'partial': True, 'artifacts': []}
            with patch('leakscope.phase3.jobs.capture_endpoint', side_effect=CaptureEvidenceError('The capture timed out because its deadline expired.', partial)) as capture:
                result = fetch_endpoint.apply(args=[str(self.endpoint.pk), ['identity'], endpoint_snapshot(self.endpoint)]).get()
        self.assertEqual(capture.call_args.args[0]['service_type'], 'ssh')
        self.assertTrue(result['partial'])
        # Fixture endpoints use a short key, so the capture's observation lands on the canonical endpoint.
        attempt = ServiceObservation.objects.get(provider='fetch:identity')
        self.assertEqual(attempt.data['service_type'], 'auto', 'an unverified attempt does not rename the endpoint')
        self.assertEqual(attempt.data['metadata']['protocol_hint']['service'], 'ssh')
        self.assertEqual(attempt.endpoint.service_type, 'auto')
        self.assertEqual([item['key'] for item in atlas.classify_levels(attempt.data, attempt.provider)], ['unknown'])
        signals = {}
        for row in ServiceObservation.objects.all():
            for item in atlas.classify_levels(row.data, row.provider):
                atlas.merge_signal(signals, item)
        levels = {key: item['level'] for key, item in signals.items()}
        self.assertEqual(levels['ssh'], 'provider_reported', 'the provider banner still counts; the failed attempt adds nothing')
        self.assertNotIn('protocol_confirmed', levels.values())


class AttributionTests(SimpleTestCase):
    def test_each_source_reaches_only_the_level_its_evidence_supports(self):
        level = lambda provider, data: attribution.observation_level(provider, data)['level']
        self.assertEqual(level('shodan', {'service_type': 'ssh', 'metadata': {}}), 'provider_reported')
        self.assertEqual(level('zoomeye', {'service_type': 'modbus', 'metadata': {'service_type_basis': attribution.REQUESTED_BASIS}}), 'candidate')
        self.assertEqual(level('fetch:identity', {'service_type': 'ssh', 'metadata': {'identity_verified': True}}), 'protocol_confirmed')
        self.assertEqual(level('native:cardano-query', {'service_type': 'cardano-n2n', 'metadata': {'cardano_protocol_confirmed': True}}), 'protocol_confirmed')
        self.assertEqual(level('fetch:http', {'service_type': 'https', 'metadata': {'status': 200}}), 'protocol_confirmed')
        # A reachability check, a failed attempt and a port scan carry a label; they do not verify it.
        for provider, metadata in (('fetch:check', {'reachable': True, 'identity_verified': False}), ('fetch:identity', {'identity_verified': False, 'error': 'x'}),
                                   ('fetch:http', {'status': 200}), ('native:tcp-inventory', {'tcp_state': 'open'}), ('scan:tcp', {})):
            verdict = attribution.observation_level(provider, {'service_type': 'modbus', 'metadata': metadata})
            self.assertEqual((verdict['level'], verdict['carried']), ('candidate', True), provider)
        self.assertEqual(level('fetch:identity', {'service_type': 'x', 'metadata': {'implementation_verified': True}}), 'implementation_verified')

    def test_placeholder_is_never_a_technology_and_the_banner_is_provider_reported(self):
        signals = {item['key']: item for item in atlas.classify_levels(shodan_data(), 'shodan')}
        self.assertNotIn('auto', signals)
        self.assertEqual(signals['ssh']['level'], 'provider_reported')
        self.assertEqual(signals['ssh']['quality'], 'provider_reported')
        self.assertIn('not confirmed live', signals['ssh']['basis'])
        carried = atlas.classify_levels({'service_type': 'modbus', 'metadata': {'reachable': True}}, 'fetch:check')
        self.assertEqual((carried[0]['level'], carried[0]['quality']), ('candidate', 'unverified'))
        self.assertEqual(atlas.classify({'service_type': 'auto', 'metadata': {}}, 'shodan'), [('unknown', 'Unknown identity', 'unknown', 'unknown')])

    def test_search_tags_group_results_and_cannot_raise_a_level(self):
        search = 'lookup:aa129322-a0c2-44e6-8901-5b3aded2ff22'
        self.assertEqual(attribution.tag_role('cardanonode', search)['role'], 'grouping')
        self.assertEqual(attribution.tag_role('protocol-confirmed', search)['role'], 'grouping')
        self.assertEqual(attribution.tag_role('identity-candidate', search)['level'], 'candidate')
        self.assertEqual(attribution.tag_role('protocol-confirmed', 'campaign:cardano')['level'], 'protocol_confirmed')
        self.assertEqual(attribution.tag_role('implementation-unverified', 'campaign:cardano')['role'], 'limit')
        port_match = attribution.endpoint_level([], [('cardanonode', search), ('protocol-confirmed', search)])
        self.assertEqual(port_match['level'], 'candidate')
        peer = attribution.endpoint_level(['protocol_confirmed'], [('protocol-confirmed', 'campaign:x'), ('implementation-unverified', 'campaign:x'),
                                                                     ('implementation-verified', 'manual')])
        self.assertEqual((peer['level'], peer['implementation_limited']), ('protocol_confirmed', True))
        self.assertEqual(attribution.endpoint_level(['provider_reported'], [('implementation-verified', 'manual')])['level'], 'implementation_verified')


class CaptureStatusTests(SimpleTestCase):
    ssh = {'address': '192.0.2.44', 'port': 12798, 'transport': 'tcp', 'scheme': '', 'service_type': 'ssh'}
    web = {'address': 'app.example.net', 'port': 443, 'transport': 'tcp', 'scheme': 'https', 'service_type': 'https'}

    @staticmethod
    def job(state, actions, results=None, errors=None, when='2026-11-12T00:51:40+00:00'):
        return {'id': 'run-' + when, 'state': state, 'created_at': when, 'finished_at': when, 'error': '',
                'parameters': {'endpoint_id': 'e', 'actions': actions, 'origin': 'lookup:x', 'collection_mode': 'full'},
                'result': {'results': results or {}, 'error_details': errors or []}}

    def test_states_without_any_attempt_say_why(self):
        status = capture_status.summarize(self.ssh)
        self.assertEqual((status['screenshot']['state'], status['protocol']['state'], status['state']), ('not_applicable', 'not_attempted', 'not_attempted'))
        self.assertIn('nothing to screenshot', status['screenshot']['reason'])
        self.assertEqual(capture_status.summarize(self.web)['state'], 'not_attempted')
        self.assertIn('Passive mode', capture_status.summarize(self.web, lookup_modes=['passive'])['reason'])
        smtp = capture_status.summarize(dict(self.ssh, service_type='smtp'))
        self.assertEqual((smtp['state'], smtp['screenshot']['state']), ('unsupported', 'not_applicable'))
        self.assertIn('no native SMTP reader', smtp['reason'])
        rdp = capture_status.summarize(dict(self.ssh, service_type='rdp'))
        self.assertEqual(rdp['screenshot']['state'], 'unsupported')
        self.assertEqual(capture_status.summarize(dict(self.ssh, service_type=''))['screenshot']['state'], 'not_applicable')

    def test_failed_attempts_keep_their_reason_and_timeouts_are_named(self):
        failed = self.job('failed', ['check', 'tcp'], errors=[{'action': 'check', 'code': 'timeout', 'message': 'The capture timed out because its deadline expired.'},
                                                              {'action': 'tcp', 'code': 'timeout', 'message': 'The capture timed out because its deadline expired.'}])
        status = capture_status.summarize(self.ssh, runs=[failed])
        self.assertEqual((status['state'], status['label']), ('failed', 'Capture failed · timed out'))
        self.assertIn('The TCP connection check also failed', status['reason'])
        # An older record that only says "failed or timed out" is not reported as a timeout.
        vague = self.job('failed', ['tcp'], errors=[{'action': 'tcp', 'code': 'capture_failed', 'message': 'Wire exchange failed or timed out'}])
        unclear = capture_status.summarize(self.ssh, runs=[vague])
        self.assertEqual(unclear['label'], 'Capture failed')
        self.assertIn('Last: Wire exchange failed or timed out.', unclear['reason'])
        self.assertEqual(status['attempts'], {'total': 1, 'failed': 1, 'retried': False, 'last_at': '2026-11-12T00:51:40+00:00'})
        self.assertEqual([action['state'] for action in status['history'][0]['actions']], ['failed', 'failed'])
        refused = self.job('failed', ['identity'], errors=[{'action': 'identity', 'code': 'connection_refused', 'message': 'The selected endpoint refused the connection.'}])
        self.assertEqual(capture_status.summarize(self.ssh, runs=[refused])['label'], 'Capture failed')

    def test_retries_partial_captures_and_pending_jobs(self):
        failure = self.job('failed', ['identity'], errors=[{'action': 'identity', 'code': 'timeout', 'message': 'timed out'}], when='2026-11-12T00:00:00+00:00')
        success = self.job('succeeded', ['identity'], results={'identity': {'identity_verified': True}}, when='2026-11-12T02:00:00+00:00')
        status = capture_status.summarize(self.ssh, runs=[success, failure, failure])
        self.assertEqual(status['state'], 'succeeded')
        self.assertIn('after 2 failed attempts', status['reason'])
        self.assertTrue(status['attempts']['retried'])
        partial = self.job('succeeded', ['identity'], results={'identity': {'partial': True, 'error': 'The service closed the identity handshake'}},
                           errors=[{'action': 'identity', 'code': 'protocol_rejected', 'message': 'The service closed the identity handshake'}])
        status = capture_status.summarize(self.ssh, runs=[partial])
        self.assertEqual(status['state'], 'partial')
        self.assertIn('closed the identity handshake', status['reason'])
        self.assertEqual(capture_status.summarize(self.web, runs=[self.job('queued', ['screenshot'])])['state'], 'pending')
        # A collector that keeps its exchange in the observation still counts as a protocol capture.
        peer = capture_status.summarize(dict(self.ssh, service_type='cardano-n2n'), confirmed_at='2026-11-12T01:09:47+00:00')
        self.assertEqual((peer['state'], peer['protocol']['last_success_at']), ('succeeded', '2026-11-12T01:09:47+00:00'))

    def test_images_are_classified_by_how_they_were_made(self):
        self.assertEqual(capture_status.capture_class('screenshot', 'playwright', {}), 'browser')
        self.assertEqual(capture_status.capture_class('screenshot', 'native', {'protocol': 'rfb', 'adapter': 'leakscope-rfb/1'}), 'framebuffer')
        self.assertEqual(capture_status.capture_class('evidence-render', 'leakscope-render', {}), 'rendered')
        self.assertIsNone(capture_status.capture_class('wire-transcript', 'native', {}))
        blocked = {'kind': 'screenshot', 'tool': 'playwright', 'created_at': '2026-11-12T01:00:00+00:00',
                   'metadata': {'partial': True, 'capture_quality': {'reasons': ['The browser received HTTP 403; the image may show an error.']}}}
        status = capture_status.summarize(self.web, artifacts=[blocked])
        self.assertEqual((status['state'], status['visual']['class']), ('partial', 'browser'))
        self.assertIn('HTTP 403', status['reason'])
        drawn = {'kind': 'evidence-render', 'tool': 'leakscope-render', 'created_at': '2026-11-12T03:00:00+00:00', 'metadata': {}}
        status = capture_status.summarize(self.ssh, artifacts=[drawn])
        self.assertEqual((status['state'], status['visual']['class'], status['images']['rendered']), ('not_attempted', 'rendered', 1))
        self.assertIn('not a screenshot', status['visual']['note'])


@override_settings(ROOT_URLCONF=__name__)
class EvidenceImageTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup); self.root = Path(folder.name)
        setting = override_settings(LEAKSCOPE_CAPTURE_DIR=self.root); setting.enable(); self.addCleanup(setting.disable)
        self.endpoint = ServiceEndpoint.objects.create(key='image-1', address='192.0.2.44', port=12798, service_type='auto', title='OpenSSH')
        self.observation = ServiceObservation.objects.create(endpoint=self.endpoint, provider='shodan', fingerprint='a', data=shodan_data(),
                                                             observed_at=SEEN, last_observed_at=SEEN, first_collected_at=SAVED, last_collected_at=SAVED)
        self.lookup = SavedLookup.objects.create(name='Port 12798', provider='shodan', query='port:12798', collection_mode='full')
        for name in ('identity-candidate', 'cardanonode'):
            TagAssignment.objects.create(tag=Tag.objects.create(name=name), target_kind='endpoint', target_key=str(self.endpoint.pk), origin='lookup:' + str(self.lookup.pk))
        Run.objects.create(task_id='failed-1', kind='capture', state='failed', error='All fetch actions failed',
            parameters=json.dumps({'endpoint_id': str(self.endpoint.pk), 'actions': ['check', 'tcp'], 'origin': 'lookup:' + str(self.lookup.pk), 'collection_mode': 'full'}),
            result=json.dumps({'results': {}, 'error_details': [{'action': 'check', 'code': 'capture_failed', 'message': 'TCP/TLS reachability check failed or timed out'},
                                                                 {'action': 'tcp', 'code': 'capture_failed', 'message': 'Wire exchange failed or timed out'}]}))

    def transcript(self, received=b'SSH-2.0-OpenSSH_7.4\r\n', sent=b'', metadata=None, provider='fetch:identity'):
        observation = ServiceObservation.objects.create(endpoint=self.endpoint, provider=provider, fingerprint='w' + str(ServiceObservation.objects.count()),
            data={'service_type': 'ssh', 'metadata': metadata or {'operation': 'server identification only; no authentication', 'identity_verified': True,
                                                                  'banner': 'SSH-2.0-OpenSSH_7.4', 'sftp_subsystem_verified': False}})
        document = {'format': 'leakscope-wire/1', 'summary': {'bytes_sent': len(sent), 'bytes_received': len(received), 'transport': 'tcp', 'tls': False,
                    'termination': 'identity_verified'}, 'events': [], 'sent': {'hex': sent.hex()}, 'received': {'hex': received.hex()}}
        body = json.dumps(document).encode(); name = 'wire-%d.json' % EvidenceArtifact.objects.count(); (self.root / name).write_bytes(body)
        artifact = EvidenceArtifact.objects.create(endpoint=self.endpoint, kind='wire-transcript', relative_path=name, mime='application/json', size=len(body),
            sha256=hashlib.sha256(body).hexdigest(), tool='native', metadata={'observation_pk': observation.pk.hex})
        return artifact, observation, document

    def test_provider_banner_document_states_source_times_and_that_nothing_was_sent(self):
        document = evidence_jobs.banner_document(self.endpoint, self.observation)
        facts = dict(document['facts'])
        self.assertEqual((document['target'], document['state']), ('192.0.2.44:12798', 'reported'))
        self.assertEqual(facts['Seen by source'], '2026-11-06 06:49:56 UTC')
        self.assertEqual(facts['Saved by LeakScope'], '2026-11-12 00:51:28 UTC')
        self.assertIn('LeakScope sent nothing', facts['Operation'])
        self.assertIn('Not checked live', facts['Outcome'])
        self.assertIn(str(self.observation.pk), facts['Provenance'])
        self.assertEqual(document['sections'][0]['body'], SSH_BANNER)
        self.observation.last_observed_at = SAVED.replace(hour=9)
        later = dict(evidence_jobs.banner_document(self.endpoint, self.observation)['facts'])['Seen by source']
        self.assertIn("time zone may not be UTC", later)
        self.observation.last_observed_at = SEEN
        self.assertEqual(document['sections'][1]['body'], SSH_BANNER.encode())
        self.assertEqual(document['sections'][2]['body']['software'], 'OpenSSH_7.4')
        text = evidence_render.describe(document) + ' '.join(document['notes'])
        self.assertIn('not a screenshot', text)
        self.assertIn('No login, key exchange or authentication is shown or implied', text)
        for claim in ('logged in', 'authenticated', 'session opened', '$ ', 'root@'):
            self.assertNotIn(claim, text)

    def test_png_is_viewable_self_describing_and_previewed_from_its_heading(self):
        from PIL import Image
        png = evidence_render.render_document(evidence_jobs.banner_document(self.endpoint, self.observation))
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.width, evidence_render.WIDTH)
            self.assertGreaterEqual(image.height, evidence_render.POSTER_HEIGHT)
            self.assertIn('Rendered protocol evidence (not a screenshot)', image.text['Description'])
            self.assertIn(SSH_BANNER, image.text['Description'])
            self.assertEqual(image.text['Software'], evidence_render.RENDERER)
        self.assertEqual(evidence_render.poster(png).size, (evidence_render.WIDTH, evidence_render.POSTER_HEIGHT))
        self.assertEqual(evidence_render.hex_lines(b'SSH-2.0-OpenSSH_7.4')[0][0], '0000  53 53 48 2d 32 2e 30 2d  4f 70 65 6e 53 53 48 5f  SSH-2.0-OpenSSH_')
        self.assertEqual(evidence_render.text_lines('a\x00b\x1b[31m中')[0], ['a·b·[31m\\u{4e2d}'])
        lines, omitted = evidence_render.hex_lines(bytes(1000))
        self.assertEqual((len(lines), omitted), (24, 616))

    def test_exchange_documents_keep_failures_and_never_overstate_them(self):
        artifact, observation, transcript = self.transcript(received=b'\x00\x01\x02garbage', sent=b'\x01\x02', metadata={
            'operation': 'native identity attempt', 'identity_verified': False, 'partial': True, 'error': 'The capture timed out because its deadline expired.',
            'error_code': 'timeout', 'protocol_hint': {'service': 'ssh'}})
        observation.data['service_type'] = 'auto'
        document = evidence_jobs.transcript_document(self.endpoint, artifact, transcript, observation)
        self.assertEqual(document['headline'], 'SSH exchange attempted by LeakScope')
        facts = dict(document['facts'])
        self.assertEqual(document['state'], 'partial')
        self.assertIn('Did not complete', facts['Outcome'])
        self.assertIn('10 bytes were received', facts['Outcome'])
        self.assertIn('not a verification', facts['Reader chosen by'])
        self.assertEqual([section['kind'] for section in document['sections'][:2]], ['hex', 'hex'])
        artifact, observation, transcript = self.transcript()
        confirmed = evidence_jobs.transcript_document(self.endpoint, artifact, transcript, observation)
        self.assertEqual(confirmed['state'], 'confirmed')
        self.assertIn('No credentials were sent', confirmed['notes'][0])
        peer = ServiceObservation.objects.create(endpoint=self.endpoint, provider='native:cardano-query', fingerprint='c', data={'service_type': 'cardano-n2n', 'metadata': {
            'request_hex': '0000', 'response_hex': '33068bbb', 'cardano_protocol_confirmed': True, 'handshake_outcome': 'supported-version query reply',
            'implementation': 'not established by a shared protocol handshake', 'method': 'one bounded handshake version query', 'protocol_versions': [14, 15]}})
        document = evidence_jobs.exchange_document(self.endpoint, peer)
        facts = dict(document['facts'])
        self.assertEqual(document['state'], 'confirmed')
        self.assertEqual(facts['Implementation'], 'not established by a shared protocol handshake')
        self.assertIn('does not identify the software', ' '.join(document['notes']))
        # LeakScope did not run this exchange, so it claims nothing about how it was made.
        self.assertEqual(document['sections'][0]['title'], 'Sent by the collector')
        self.assertNotIn('No credentials were sent', ' '.join(document['notes']))
        self.assertIn('LeakScope did not run this exchange', ' '.join(document['notes']))
        self.assertIn('native:cardano-query', attribution.observation_level(peer.provider, peer.data)['basis'])

    def test_drawing_is_local_idempotent_and_leaves_sources_untouched(self):
        self.transcript()
        before = list(ServiceObservation.objects.order_by('pk').values('pk', 'data', 'observed_at', 'first_collected_at', 'last_collected_at'))
        with patch('socket.socket', side_effect=AssertionError('no network')), patch('requests.get', side_effect=AssertionError('no network')):
            first = evidence_jobs.render_endpoint(self.endpoint)
            second = evidence_jobs.render_endpoint(self.endpoint)
        self.assertEqual((first['drawn'], first['failed'], second['drawn'], second['existing']), (2, 0, 0, 2))
        self.assertEqual(before, list(ServiceObservation.objects.order_by('pk').values('pk', 'data', 'observed_at', 'first_collected_at', 'last_collected_at')))
        self.assertEqual(Run.objects.get(task_id='failed-1').state, 'failed', 'the failed attempt is kept')
        banner = EvidenceArtifact.objects.get(kind='evidence-render', metadata__render__source_kind='provider_banner')
        self.assertEqual(banner.metadata['render']['source_observed_at'], SEEN.isoformat())
        self.assertEqual(banner.metadata['render']['source_collected_at'], SAVED.isoformat())
        self.assertEqual((banner.metadata['render']['network_contact'], banner.metadata['capture_class'], banner.mime), (False, 'rendered', 'image/png'))
        self.assertEqual(banner.metadata['observation_pk'], self.observation.pk.hex)
        # Listed at the time its evidence was saved; the draw time is kept beside it.
        self.assertEqual(banner.created_at, SAVED)
        self.assertGreater(banner.metadata['render']['rendered_at'], SAVED.isoformat())
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        self.assertEqual(evidence_jobs.evidence_time((now - timedelta(days=45)).isoformat(), now), now, 'older than retention keeps the draw time')
        self.assertEqual(evidence_jobs.evidence_time(None, now), now)
        self.assertEqual(banner.metadata['gallery']['state'], 'ready')
        self.assertTrue((self.root / banner.relative_path).read_bytes().startswith(b'\x89PNG'))
        self.assertTrue(EvidenceArtifact.objects.filter(kind='wire-transcript').exists(), 'raw wire file stays beside the image')

    def test_hidden_sources_and_web_responses_are_not_drawn(self):
        self.observation.data = shodan_data(banner='HTTP/1.1 200 OK\r\nServer: nginx\r\n\r\n')
        self.observation.save()
        self.assertEqual(evidence_jobs.render_endpoint(self.endpoint)['drawn'], 0)
        self.observation.data = shodan_data(); self.observation.hidden = True; self.observation.save()
        self.assertEqual(list(evidence_jobs.endpoint_documents(self.endpoint)), [])

    def test_backfill_command_and_job_draw_saved_evidence_only(self):
        from io import StringIO
        out = StringIO(); call_command('render_evidence', '--dry-run', stdout=out)
        self.assertIn("'drawable': 1", out.getvalue())
        self.assertFalse(EvidenceArtifact.objects.filter(kind='evidence-render').exists())
        response = self.client.post('/api/atlas/evidence/render', json.dumps({'limit': 10}), content_type='application/json')
        self.assertEqual(response.status_code, 202, response.content)
        run = Run.objects.get(kind='evidence_render')
        result = json.loads(run.result)
        self.assertEqual((run.state, result['drawn'], result['network_contact']), ('succeeded', 1, False))
        out = StringIO(); call_command('render_evidence', stdout=out)
        self.assertIn("'drawn': 0, 'existing': 1", out.getvalue())

    def test_endpoint_details_explain_attribution_and_the_missing_capture(self):
        evidence_jobs.render_endpoint(self.endpoint)
        data = self.client.get('/api/operations/endpoints/' + str(self.endpoint.pk)).json()
        self.assertEqual(data['endpoint']['service_type'], 'auto')
        self.assertEqual((data['endpoint']['effective_service_type'], data['endpoint']['service_type_basis']), ('ssh', 'provider_hint'))
        self.assertIn('identity', data['endpoint']['fetch_actions'])
        ladder = {step['level']: step for step in data['attribution']['ladder']}
        self.assertEqual(data['attribution']['level'], 'provider_reported')
        self.assertTrue(ladder['candidate']['established'])
        self.assertIn('Port 12798', ladder['candidate']['items'][0]['text'])
        self.assertTrue(ladder['provider_reported']['established'])
        self.assertFalse(ladder['protocol_confirmed']['established'])
        self.assertEqual(data['attribution']['implementation']['state'], 'unverified')
        roles = {row['tag']: row['role'] for row in data['attribution']['tags']}
        self.assertEqual(roles, {'cardanonode': 'grouping', 'identity-candidate': 'level'})
        self.assertIn('not an identity or ownership claim', data['attribution']['caution'])
        status = data['capture_status']
        self.assertEqual((status['state'], status['channel'], status['screenshot']['state']), ('failed', 'protocol', 'not_applicable'))
        self.assertEqual(status['images'], {'browser': 0, 'framebuffer': 0, 'rendered': 1})
        self.assertEqual(status['history'][0]['actions'][1]['message'], 'Wire exchange failed or timed out')
        image = next(item for item in data['artifacts'] if item['kind'] == 'evidence-render')
        self.assertEqual(image['capture_label'], 'Rendered protocol evidence')
        self.assertEqual(TagAssignment.objects.filter(target_key=str(self.endpoint.pk)).count(), 2, 'existing tags and their origins are kept')

    def test_gallery_and_technology_results_label_every_class(self):
        artifact, observation, _ = self.transcript()
        evidence_jobs.render_endpoint(self.endpoint)
        web = ServiceEndpoint.objects.create(key='image-web', address='198.51.100.9', port=443, scheme='https', service_type='https')
        proof = ServiceObservation.objects.create(endpoint=web, provider='fetch:screenshot', fingerprint='s', data={'service_type': 'https', 'metadata': {'status': 200}})
        vnc = ServiceEndpoint.objects.create(key='image-vnc', address='198.51.100.10', port=5900, service_type='vnc')
        frame = ServiceObservation.objects.create(endpoint=vnc, provider='fetch:screenshot', fingerprint='v', data={'service_type': 'vnc', 'metadata': {'identity_verified': True}})
        for owner, source, metadata, tool in ((web, proof, {}, 'playwright'), (vnc, frame, {'protocol': 'rfb', 'adapter': 'leakscope-rfb/1'}, 'native')):
            EvidenceArtifact.objects.create(endpoint=owner, kind='screenshot', relative_path=owner.key + '.png', mime='image/png', size=1, sha256='0' * 64, tool=tool,
                                            metadata=dict(metadata, observation_pk=source.pk.hex))
        listing = self.client.get('/api/atlas/screenshots').json()
        self.assertEqual(listing['class_counts'], {'browser': 1, 'framebuffer': 1, 'rendered': 2})
        self.assertEqual(self.client.get('/api/atlas/screenshots', {'capture_class': 'rendered'}).json()['class_counts']['browser'], 1, 'counts ignore the class filter')
        rows = listing['results']
        self.assertEqual(sorted(row['capture_class'] for row in rows), ['browser', 'framebuffer', 'rendered', 'rendered'])
        drawn = self.client.get('/api/atlas/screenshots', {'capture_class': 'rendered'}).json()['results']
        self.assertEqual({row['capture_label'] for row in drawn}, {'Rendered protocol evidence'})
        exchange = next(row for row in drawn if row['evidence']['source_kind'] == 'wire_transcript')
        self.assertEqual(exchange['evidence']['state'], 'confirmed')
        self.assertEqual([item['kind'] for item in exchange['evidence']['raw_files']], ['wire-transcript'])
        self.assertTrue(all(row['thumbnail_url'] for row in drawn))
        self.assertEqual(self.client.get(drawn[0]['thumbnail_url']).status_code, 200)
        self.assertEqual([row['capture_class'] for row in self.client.get('/api/atlas/screenshots', {'capture_class': 'framebuffer'}).json()['results']], ['framebuffer'])
        self.assertEqual([row['capture_class'] for row in self.client.get('/api/atlas/screenshots', {'capture_class': 'browser'}).json()['results']], ['browser'])
        self.assertEqual(sorted(row['capture_class'] for row in self.client.get('/api/atlas/screenshots', {'capture_class': 'captured'}).json()['results']), ['browser', 'framebuffer'])
        self.assertEqual(self.client.get('/api/atlas/screenshots', {'capture_class': 'terminal'}).status_code, 400)
        data = self.client.get('/api/atlas/technologies').json()
        groups = {group['key']: group for group in data['groups']}
        self.assertNotIn('auto', groups)
        self.assertEqual(groups['ssh']['levels']['protocol_confirmed'], 1)
        quiet = ServiceEndpoint.objects.create(key='image-quiet', address='198.51.100.77', port=12798, service_type='auto')
        ServiceObservation.objects.create(endpoint=quiet, provider='shodan', fingerprint='q', data={'service_type': 'auto', 'metadata': {}})
        unnamed = self.client.get('/api/atlas/technologies').json()
        self.assertEqual(next(group for group in unnamed['groups'] if group['key'] == 'unknown')['levels']['none'], 1)
        self.assertEqual(unnamed['totals']['levels']['none'], 1)
        self.assertEqual(sum(unnamed['totals']['levels'].values()), unnamed['totals']['endpoints'], 'every saved endpoint is in exactly one bucket')
        self.assertEqual(groups['ssh']['images']['rendered'], 2)
        self.assertEqual(groups['ssh']['preview']['capture_class'], 'rendered')
        self.assertEqual(groups['vnc']['preview']['capture_class'], 'framebuffer')
        self.assertEqual(groups['https']['captures']['succeeded'], 1)
        self.assertEqual([item['key'] for item in data['legend']['levels']], list(attribution.LEVELS))
        self.assertEqual(data['totals']['images'], {'browser': 1, 'framebuffer': 1, 'rendered': 2})
        confirmed = self.client.get('/api/atlas/technologies', {'attribution': 'protocol_confirmed'}).json()
        self.assertEqual(sorted(group['key'] for group in confirmed['groups']), ['https', 'ssh', 'vnc'])
        self.assertEqual(self.client.get('/api/atlas/technologies', {'attribution': 'owned'}).status_code, 400)
