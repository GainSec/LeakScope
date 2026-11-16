"""Protocol classifier searches must remain independent of conventional ports."""
from django.test import SimpleTestCase
from leakscope.phase3.catalog import profile_query


class OTQueryProfilesTests(SimpleTestCase):
    def test_native_shodan_modules_use_real_protocol_identifiers(self):
        expected = {
            'modbus':'modbus', 'bacnet':'bacnet', 'opcua':'opc-ua',
            'ethernetip':'ethernetip', 'dnp3':'dnp3', 'iec104':'iec-104',
            'iec61850':'iec-61850', 'melsec':'melsec-q-udp',
            'hartip':'hart-ip-udp', 'codesys':'codesys',
            'proconos':'proconos', 'pcworx':'pcworx',
            'srtp':'general-electric-srtp', 'coap':'coap-dtls',
        }
        for key, module in expected.items():
            with self.subTest(key=key):
                dto=profile_query(key,'shodan')
                self.assertTrue(dto['supported'],dto['reason'])
                self.assertIn(module,dto['query'].partition('shodan.module:')[2].split(','))
                self.assertNotRegex(dto['query'],r'\b(?:port|product)\s*[:=]')
                self.assertTrue(dto['basis'])

    def test_zoom_protocol_classifier_is_not_a_product_or_port_filter(self):
        for key in ('modbus','bacnet','opcua','ethernetip','dnp3','iec104','iec61850',
                    'fins','melsec','ads','hartip','codesys','proconos','pcworx','srtp','coap'):
            with self.subTest(key=key):
                dto=profile_query(key,'zoomeye')
                self.assertTrue(dto['supported'],dto['reason'])
                self.assertIn('service=',dto['query'])
                self.assertIn('candidate',dto['reason'].lower())
                self.assertNotRegex(dto['query'],r'\b(?:port|product|app)\s*[:=]')

    def test_udp_family_search_does_not_claim_cyclic_io_identity(self):
        dto=profile_query('cipio','shodan')
        self.assertTrue(dto['supported'])
        self.assertIn('ethernetip-udp',dto['query'])
        self.assertIn('family',dto['reason'].lower())
        self.assertIn('cyclic',dto['reason'].lower())
        self.assertIn('not verified',dto['reason'].lower())

    def test_fins_tcp_companion_is_explicit(self):
        dto=profile_query('fins','shodan')
        self.assertTrue(dto['supported'])
        self.assertIn('omron-tcp',dto['query'])
        self.assertIn('TCP',dto['reason'])
        self.assertIn('UDP',dto['reason'])

    def test_decoded_netlas_queries_are_preserved(self):
        self.assertEqual(profile_query('modbus','netlas')['query'],'modbus.function_code:43 AND modbus.length:>0')
        self.assertEqual(profile_query('bacnet','netlas')['query'],'bacnet.is_bacnet:true')

    def test_optional_exclusion_keeps_independent_module_evidence(self):
        base=profile_query('opcua','shodan')
        optional=profile_query('opcua','shodan','nonstandard')
        self.assertTrue(base['supported'])
        self.assertIn(base['query'],optional['query'])
        self.assertIn('-port:4840',optional['query'])
        self.assertNotIn('port:',base['query'])

    def test_netlas_classifier_queries_use_documented_common_fields(self):
        for key in ('opcua','ethernetip','dnp3','iec104','iec61850','fins','melsec','ads','hartip','codesys','proconos','pcworx','srtp','coap'):
            dto=profile_query(key,'netlas')
            self.assertTrue(dto['supported'],dto['reason'])
            self.assertIn('prot7:',dto['query'])
            self.assertRegex(dto['reason'],r'not live-(?:verified|validated)')
            self.assertNotRegex(dto['query'],r'\b(?:port|product)\s*[:=]')
        self.assertNotIn('\"mms\"',profile_query('iec61850','netlas')['query'])
        self.assertNotIn('\"srtp\"',profile_query('srtp','netlas')['query'])

    def test_udp_io_family_keeps_transport_constraint(self):
        self.assertIn('protocol=\"UDP\"',profile_query('cipio','zoomeye')['query'])
        self.assertIn('prot4:udp',profile_query('cipio','netlas')['query'])

    def test_ads_does_not_invent_a_shodan_module(self):
        dto=profile_query('ads','shodan')
        self.assertTrue(dto['supported'])
        self.assertNotIn('shodan.module:',dto['query'])
        self.assertIn('companion',dto['reason'])
