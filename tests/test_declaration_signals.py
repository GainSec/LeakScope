"""Declared service origins are local metadata evidence, with no endpoint requests."""
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from leakscope.phase3.declaration_signals import declaration_signals


class DeclarationSignalTests(SimpleTestCase):
    def signals(self, value):
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        with patch('requests.get', side_effect=AssertionError('No HTTP')), patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')):
            return declaration_signals(data)

    def test_openapi_servers_and_oauth_flows_keep_role_protocol_and_nonstandard_port(self):
        values = self.signals({
            'openapi': '3.1.1',
            'servers': [{'url': 'https://API.Example.com.:9443/v1?token=private#fragment'}],
            'components': {'securitySchemes': {
                'sso': {'type': 'oauth2', 'flows': {'authorizationCode': {
                    'authorizationUrl': 'https://id.example.net/authorize?client_id=discarded',
                    'tokenUrl': 'https://auth.example.net:8443/token',
                }}},
                'unrelated': {'type': 'apiKey', 'tokenUrl': 'https://ignored.example.net/'},
            }},
            'description': 'https://unrelated.example.net/',
        })
        self.assertEqual(values, [
            ('declaration.openapi.oauth_authorize', 'https://id.example.net'),
            ('declaration.openapi.oauth_token', 'https://auth.example.net:8443'),
            ('declaration.openapi.server', 'https://api.example.com:9443'),
        ])
        self.assertNotIn('private', str(values))
        self.assertNotIn('discarded', str(values))

    def test_swagger_host_basepath_and_oauth_scheme_have_format_specific_names(self):
        values = self.signals({
            'swagger': '2.0', 'host': 'API.Example.com:8443', 'basePath': '/v2', 'schemes': ['https'],
            'securityDefinitions': {'login': {'type': 'oauth2',
                'authorizationUrl': 'https://sso.example.com/authorize',
                'tokenUrl': 'https://sso.example.com/token'}},
        })
        self.assertEqual(values, [
            ('declaration.swagger.oauth_authorize', 'https://sso.example.com'),
            ('declaration.swagger.oauth_token', 'https://sso.example.com'),
            ('declaration.swagger.server', 'https://api.example.com:8443'),
        ])
        self.assertEqual(self.signals({'swagger': '2.0', 'host': 'api.example.com:8443'}),
                         [('declaration.swagger.server', 'api.example.com:8443')])

    def test_asyncapi_two_and_three_server_fields_support_non_http_protocols(self):
        for version, server in [
            ('2.6.0', {'url': 'broker.example.com:1884/events', 'protocol': 'mqtt'}),
            ('3.0.0', {'host': 'broker.example.com:1884', 'pathname': '/events', 'protocol': 'mqtt'}),
        ]:
            with self.subTest(version=version):
                self.assertEqual(self.signals({'asyncapi': version, 'servers': {'production': server}}),
                                 [('declaration.asyncapi.server', 'mqtt://broker.example.com:1884')])
        self.assertEqual(self.signals({'asyncapi': '2.6.0', 'servers': {'production': {
            'url': 'amqps://queue.example.net:15671/messages', 'protocol': 'amqps',
        }}}), [('declaration.asyncapi.server', 'amqps://queue.example.net:15671')])

    def test_wsdl_requires_known_namespaces_and_only_service_endpoint_fields(self):
        wsdl = b'''<definitions xmlns="http://schemas.xmlsoap.org/wsdl/" xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/">
          <documentation>https://ignored.example.net/</documentation>
          <service name="Example"><port name="Port"><soap:address location="https://soap.example.com:9443/service"/></port></service>
        </definitions>'''
        self.assertEqual(self.signals(wsdl), [('declaration.wsdl.server', 'https://soap.example.com:9443')])
        modern = b'''<description xmlns="http://www.w3.org/ns/wsdl"><service name="Example"><endpoint name="Port" address="https://soap.example.net/v2"/></service></description>'''
        self.assertEqual(self.signals(modern), [('declaration.wsdl.server', 'https://soap.example.net')])
        self.assertEqual(self.signals(b'<definitions><address location="https://wrong.example.net/"/></definitions>'), [])

    def test_unrelated_json_html_arrays_yaml_and_invalid_markers_do_not_create_signals(self):
        for value in [
            {'servers': [{'url': 'https://random.example.net/'}]},
            {'openapi': 3, 'servers': [{'url': 'https://random.example.net/'}]},
            {'openapi': 'not-a-version', 'servers': [{'url': 'https://random.example.net/'}]},
            [{'openapi': '3.1.0', 'servers': [{'url': 'https://random.example.net/'}]}],
            b'<html><script>const openapi="3.1.0"; const servers="https://random.example.net"</script></html>',
            b'openapi: 3.1.0\nservers:\n  - url: https://random.example.net/',
        ]:
            with self.subTest(value=value):
                self.assertEqual(self.signals(value), [])

    def test_private_addresses_templates_relative_urls_and_unsafe_schemes_are_omitted(self):
        urls = [
            'https://localhost/v1', 'http://127.0.0.1:9000/', 'http://10.0.0.1/',
            'http://[::1]/', 'http://169.254.169.254/', 'https://api.local/', 'https://corp.internal/',
            'https://{environment}.example.com/', 'https://api.example.com/{version}',
            '/relative/v1', 'file://api.example.com/tmp', 'javascript://api.example.com/',
            '//api.example.com/v1', 'https://[2606:4700:4700::1111%eth0]/',
            'https://api.example.com:0/', 'https://api.example.com:99999/', 'https://127.1/',
            'https://api.example.com\n.evil.net/',
        ]
        self.assertEqual(self.signals({'openapi': '3.1.0', 'servers': [{'url': url} for url in urls]}), [])
        self.assertEqual(self.signals({'asyncapi': '3.0.0', 'servers': {'prod': {
            'host': 'https://broker.example.com', 'protocol': 'mqtt',
        }}}), [])

    def test_normalized_origins_strip_credentials_and_canonicalize_known_default_ports(self):
        self.assertEqual(self.signals({'openapi': '3.0.3', 'servers': [
            {'url': 'https://name:secret@api.example.com:443/v1?key=private'},
            {'url': 'https://api.example.com/'},
            {'url': 'https://BÜCHER.example.com:8443/'},
            {'url': 'https://8.8.8.8:9443/path'},
        ]}), [
            ('declaration.openapi.server', 'https://8.8.8.8:9443'),
            ('declaration.openapi.server', 'https://api.example.com'),
            ('declaration.openapi.server', 'https://xn--bcher-kva.example.com:8443'),
        ])

    def test_http_envelope_is_supported_without_regex_scanning_body_or_fetching_references(self):
        body = json.dumps({'openapi': '3.1.0', 'servers': [{'url': 'https://api.example.com:7443/'}],
                           'components': {'schemas': {'Account': {'$ref': 'https://ignored.example.net/account.json'}}}}).encode()
        envelope = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n' + body
        self.assertEqual(self.signals(envelope), [('declaration.openapi.server', 'https://api.example.com:7443')])
        self.assertEqual(self.signals(b'HTTP/1.1 200 OK\r\nX-API: https://ignored.example.net/'), [])

    def test_httpx_wrapped_full_body_is_parsed_but_previews_and_nested_wrappers_are_not(self):
        body = json.dumps({'openapi': '3.1.0', 'servers': [{'url': 'https://api.example.com:7443/'}]})
        wrapper = {'url': 'https://docs.example.com/openapi.json', 'status_code': 200, 'body': body}
        self.assertEqual(self.signals(wrapper), [('declaration.openapi.server', 'https://api.example.com:7443')])
        self.assertEqual(self.signals({'body': body}), [])
        self.assertEqual(self.signals({**wrapper, 'body': '', 'body_preview': body}), [])
        self.assertEqual(self.signals({**wrapper, 'body': json.dumps(wrapper)}), [])

    def test_xml_entities_document_size_depth_and_signal_count_are_bounded(self):
        entity = b'''<!DOCTYPE definitions [<!ENTITY ext SYSTEM "https://ignored.example.net/entity">]><definitions xmlns="http://schemas.xmlsoap.org/wsdl/">&ext;</definitions>'''
        self.assertEqual(self.signals(entity), [])
        self.assertEqual(self.signals(b'{' + b'x' * (1024 * 1024)), [])
        self.assertEqual(self.signals(b'[' * 2000 + b']' * 2000), [])
        result = self.signals({'openapi': '3.1.0', 'servers': [
            {'url': f'https://host{number}.example.com:9443/'} for number in range(300)
        ]})
        self.assertLessEqual(len(result), 64)
        self.assertTrue(all(len(value) <= 128 for _, value in result))
        long_host = '.'.join(['x' * 63, 'y' * 63, 'example', 'com'])
        self.assertEqual(self.signals({'openapi': '3.1.0', 'servers': [{'url': 'https://' + long_host}]}), [])


class DeclarationIndexIntegrationTests(TestCase):
    def test_normal_local_index_keeps_marketing_and_declaration_artifact_proof(self):
        from leakscope.phase3.local_correlations import execute_index
        from leakscope_app.models import EvidenceArtifact, EvidenceCorrelation, Run, ServiceEndpoint, ServiceObservation

        with tempfile.TemporaryDirectory() as directory, override_settings(LEAKSCOPE_CAPTURE_DIR=Path(directory)):
            endpoint = ServiceEndpoint.objects.create(key='declaration-fixture', address='192.0.2.7', port=8443)
            observation = ServiceObservation.objects.create(endpoint=endpoint, provider='fetch:http', fingerprint='definition')
            body = json.dumps({'openapi': '3.1.0', 'servers': [{'url': 'https://api.example.com:9443/v1'}],
                               'info': {'description': 'GTM-ABC123'}}).encode()
            Path(directory, 'api.json').write_bytes(body)
            artifact = EvidenceArtifact.objects.create(
                endpoint=endpoint, kind='response', relative_path='api.json', size=len(body),
                sha256=hashlib.sha256(body).hexdigest(), mime='application/json', tool='fixture',
                metadata={'observation_pk': str(observation.pk)},
            )
            run = Run.objects.create(kind='correlation_index', task_id='declared-origin-index')
            with patch('requests.get', side_effect=AssertionError('No HTTP')), patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')):
                result = execute_index(run.pk, run.task_id, [str(artifact.pk)])
            self.assertEqual(result['indexed'], 1)
            declared = EvidenceCorrelation.objects.get(kind='declaration.openapi.server')
            self.assertEqual(declared.value, 'https://api.example.com:9443')
            self.assertEqual(declared.artifact_id, artifact.pk)
            self.assertEqual(EvidenceCorrelation.objects.get(kind='tracking.google_tag_manager').value, 'GTM-ABC123')
