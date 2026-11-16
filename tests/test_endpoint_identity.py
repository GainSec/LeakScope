"""One address and port is one saved result unless two different schemes were seen there."""
from django.test import TestCase
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import ServiceEndpoint


def row(scheme='', port=80, **extra):
    return {'address': '192.0.2.10', 'port': port, 'scheme': scheme, 'service_type': extra.pop('service_type', ''), 'metadata': extra}


class EndpointIdentityTests(TestCase):
    def test_a_scan_without_a_scheme_joins_the_row_a_provider_saved(self):
        first = ingest_observation('shodan', row('http', service_type='http'))
        again = ingest_observation('port_scan', row('', open=True))
        self.assertEqual((again.pk, ServiceEndpoint.objects.count(), again.scheme), (first.pk, 1, 'http'))

    def test_a_row_saved_without_a_scheme_takes_the_scheme_seen_later(self):
        bare = ingest_observation('port_scan', row('', open=True))
        named = ingest_observation('shodan', row('https', service_type='http'))
        self.assertEqual((named.pk, ServiceEndpoint.objects.count()), (bare.pk, 1))
        self.assertEqual(ServiceEndpoint.objects.get().scheme, 'https')
        self.assertEqual(ingest_observation('port_scan', row('', open=True, again=1)).pk, bare.pk)

    def test_two_real_schemes_on_one_port_stay_apart_and_a_bare_row_stays_bare(self):
        a, b = ingest_observation('shodan', row('http')), ingest_observation('shodan', row('https'))
        self.assertNotEqual(a.pk, b.pk)
        bare = ingest_observation('port_scan', row('', open=True))
        self.assertEqual((ServiceEndpoint.objects.count(), bare.scheme), (3, ''))
        other = ingest_observation('port_scan', row('', port=9929, open=True))
        self.assertEqual((other.scheme, ServiceEndpoint.objects.filter(port=9929).count()), ('', 1))
        udp = ingest_observation('port_scan', {'address': '192.0.2.10', 'port': 80, 'transport': 'udp', 'metadata': {}})
        self.assertEqual((udp.transport, udp.scheme), ('udp', ''))
