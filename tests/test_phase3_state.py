from django.test import TestCase
from leakscope_app.models import ServiceEndpoint, ServiceObservation, TagAssignment
from leakscope.phase3.state import ingest_observation, set_tags


class ObservationStateTests(TestCase):
    def test_capture_header_file_hash_is_not_a_service_change(self):
        row = {'address': '192.0.2.5', 'port': 80, 'metadata': {'body_sha256': 'a' * 64,
            'artifacts': [{'kind': 'headers', 'sha256': 'b' * 64}]}}
        ingest_observation('fetch:http', row)
        row['metadata']['artifacts'][0]['sha256'] = 'c' * 64
        ingest_observation('fetch:http', row)
        self.assertEqual(ServiceObservation.objects.count(), 1)
    def test_observations_preserve_changes_but_deduplicate_repeats(self):
        row = {'address': 'EXAMPLE.TEST.', 'port': 443, 'scheme': 'https', 'service_type': 'https', 'title': 'one', 'metadata': {'server': 'fixture'}}
        first = ingest_observation('netlas', row)
        second = ingest_observation('netlas', row)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ServiceEndpoint.objects.count(), 1)
        self.assertEqual(ServiceObservation.objects.count(), 1)
        row['metadata']['server'] = 'changed'
        ingest_observation('netlas', row)
        self.assertEqual(ServiceObservation.objects.count(), 2)
        self.assertEqual(ServiceEndpoint.objects.get().address, 'example.test')

    def test_scheme_port_transport_and_host_identity_are_distinct(self):
        row = {'address': '192.0.2.1', 'port': 443, 'scheme': 'https', 'metadata': {}}
        ingest_observation('shodan', row)
        ingest_observation('zoomeye', dict(row, port=8443))
        ingest_observation('shodan', dict(row, transport='udp'))
        self.assertEqual(ServiceEndpoint.objects.count(), 3)

    def test_manual_tags_survive_inherited_tag_refresh(self):
        row = ingest_observation('netlas', {'address': '192.0.2.1', 'port': 80})
        key = str(row.pk)
        set_tags('endpoint', key, ['Review', 'Storage'], origin='manual')
        set_tags('endpoint', key, ['External'], origin='lookup:a')
        set_tags('endpoint', key, [], origin='lookup:a')
        self.assertEqual(set(TagAssignment.objects.values_list('tag__name', flat=True)), {'review', 'storage'})

    def test_invalid_identity_and_unknown_tag_target_rejected(self):
        for row in ({'address': 'http://example.test/path', 'port': 80}, {'address': 'example.test', 'port': 0}, {'address': 'a b', 'port': 443}):
            with self.assertRaises(ValueError): ingest_observation('netlas', row)
        with self.assertRaises(ValueError): set_tags('bogus', 'unknown', ['x'])
