import json
import socket
import struct
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase,TestCase
from leakscope.phase3.native import identity_capability, identity_transports
from leakscope.phase3.capture import capture_endpoint, supported_actions, CaptureEvidenceError
from leakscope.phase3.catalog import catalog_entry
from leakscope.phase3.collection_policy import plan_collection
from tests.test_phase3_capture import tcp_fixture
from tests.test_native_messaging import amqp_start


def endpoint(service,transport='tcp',port=42137,scheme=''):
    return {'address':'127.0.0.1','port':port,'service_type':service,'transport':transport,'scheme':scheme}


class ProtocolClosureTests(SimpleTestCase):
    def test_full_selects_implemented_identity_on_arbitrary_ports(self):
        for service in ('mssql','oracle','cassandra','scylla','aerospike','vertica','db2','drda','hana','maxdb','monetdb','milvus','timescaledb','ceph','dnp3','iec61850','mms','ads','fox','codesys','proconos','pcworx','amqp','kafka','zookeeper'):
            with self.subTest(service=service):
                self.assertIn('identity',supported_actions(endpoint(service)))
                self.assertEqual(plan_collection(endpoint(service),'full')['actions'],['identity'])
                self.assertTrue(identity_capability(service)['operation'])

    def test_observed_provider_protocol_aliases_use_the_implemented_family(self):
        for observed,protocol in (('ms-sql-s','mssql'),('niagara-fox','fox'),('hart-ip','hartip'),
                                  ('IEC 61850-8-1 MMS','mms'),('cql','cassandra'),('sap-hana','hana')):
            with self.subTest(observed=observed):
                self.assertIn('identity',supported_actions(endpoint(observed)))
                self.assertEqual(identity_capability(observed)['protocol'],protocol)

    def test_supported_udp_is_not_bacnet_only_and_wrong_transport_stays_explicit(self):
        for service in ('bacnet','fins','melsec','hartip','lantronix-config','vertx-edge','coap'):
            with self.subTest(service=service):
                self.assertIn('udp',identity_transports(service))
                self.assertEqual(plan_collection(endpoint(service,'udp'),'full')['actions'],['identity'])
        self.assertIn('identity',supported_actions(endpoint('coap','tcp')))
        self.assertNotIn('identity',supported_actions(endpoint('lantronix-config','tcp')))
        self.assertNotIn('identity',supported_actions(endpoint('dnp3','udp')))

    def test_catalog_and_skips_describe_genuine_constraints(self):
        for service in ('informix','firebird','srtp','plc','lantronix','smux','stomp','cipio'):
            with self.subTest(service=service):
                record=catalog_entry(service)
                self.assertFalse(record['identity'])
                self.assertTrue(record['identity_limitation'])
                target=endpoint(service,'udp' if service=='cipio' else 'tcp')
                self.assertTrue(any(record['identity_limitation'] in note for note in plan_collection(target,'full')['skipped']))

    def test_db2_query_excludes_the_observed_ceph_service_taxonomy_false_positive(self):
        query=catalog_entry('db2')['query_variants']['fingerprint']['zoomeye']['query']
        self.assertIn('banner!="ceph v2"',query)
        self.assertIn('banner!="ceph v027"',query)
        self.assertNotIn('port=',query)

    def test_generic_protocol_aliases_keep_identification_without_claiming_products(self):
        self.assertEqual(identity_capability('db2')['protocol'],'drda')
        self.assertEqual(identity_capability('iec61850')['protocol'],'mms')
        self.assertEqual(identity_capability('milvus')['protocol'],'grpc')
        self.assertEqual(identity_capability('timescaledb')['protocol'],'postgresql')
        for alias in ('drda','mms','postgresql','grpc'):
            self.assertIn('tcp',identity_transports(alias))

    def test_actual_amqp_capture_retains_response_and_stops_before_auth(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(8),b'AMQP\x00\x00\x09\x01');sock.sendall(amqp_start())
            self.assertEqual(sock.recv(1),b'')
        with tempfile.TemporaryDirectory() as directory,tcp_fixture(dialogue) as port:
            result=capture_endpoint(endpoint('amqp',port=port),'identity',{'CAPTURE_DIR':directory})
            self.assertEqual(result['verified_type'],'amqp')
            self.assertTrue(result['metadata']['identity_verified'])
            artifacts={a['kind']:Path(a['path']).read_bytes() for a in result['artifacts']}
            self.assertEqual(artifacts['wire-request'],b'AMQP\x00\x00\x09\x01')
            self.assertEqual(artifacts['wire-response'],amqp_start())

    def test_actual_coap_capture_retains_single_correlated_datagram(self):
        errors=[]
        with tempfile.TemporaryDirectory() as directory,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1',0));server.settimeout(2)
            def serve():
                try:
                    request,peer=server.recvfrom(4096);token_length=request[0]&15
                    response=bytes((0x60|token_length,69))+request[2:4]+request[4:4+token_length]+b'\xc1\x28\xff</test>;rt="fixture"'
                    server.sendto(response,peer)
                except Exception as exc:errors.append(exc)
            thread=threading.Thread(target=serve);thread.start()
            try:
                result=capture_endpoint(endpoint('coap','udp',server.getsockname()[1]),'identity',{'CAPTURE_DIR':directory,'CAPTURE_TIMEOUT':1})
                self.assertEqual(result['verified_type'],'coap')
                self.assertEqual(result['metadata']['wire_summary']['events_sent'],1)
                self.assertEqual(result['metadata']['wire_summary']['events_received'],1)
                self.assertIn('wire-response',{a['kind'] for a in result['artifacts']})
            finally:thread.join(3)
        self.assertFalse(errors)

    def test_malformed_native_reply_remains_partial_wire_not_verified_identity(self):
        def dialogue(sock):sock.recv(1024);sock.sendall(b'not an AMQP response')
        with tempfile.TemporaryDirectory() as directory,tcp_fixture(dialogue) as port:
            with self.assertRaises(CaptureEvidenceError) as caught:
                capture_endpoint(endpoint('amqp',port=port),'identity',{'CAPTURE_DIR':directory})
            self.assertFalse(caught.exception.result['metadata']['identity_verified'])
            self.assertTrue(caught.exception.result['partial'])

    def test_proconos_capture_stops_before_unrequested_project_metadata(self):
        reply=b'\xcc\x01'+b'\0'*10+b'ProConOS V2.0\0'.ljust(32,b'\0')+b'PLC fixture\0'.ljust(33,b'\0')
        def dialogue(sock):sock.recv(1024);sock.sendall(reply+b'SENSITIVE_PROJECT_METADATA')
        with tempfile.TemporaryDirectory() as directory,tcp_fixture(dialogue) as port:
            result=capture_endpoint(endpoint('proconos',port=port),'identity',{'CAPTURE_DIR':directory})
            response=next(Path(a['path']).read_bytes() for a in result['artifacts'] if a['kind']=='wire-response')
            self.assertEqual(response,reply)
            for item in result['artifacts']:self.assertNotIn(b'SENSITIVE_PROJECT_METADATA',Path(item['path']).read_bytes())

    def test_explicit_milvus_tls_uses_the_same_http2_tls_boundary_as_grpc(self):
        with patch('leakscope.phase3.capture.identify',side_effect=ValueError('fixture stop')) as identify:
            with tempfile.TemporaryDirectory() as directory,self.assertRaises(ValueError):
                capture_endpoint(endpoint('milvus',scheme='grpcs'),'identity',{'CAPTURE_DIR':directory})
        self.assertEqual(identify.call_args.kwargs['tls'],True)


class EndpointCapabilityTests(TestCase):
    def test_saved_preset_from_generic_verified_protocol_keeps_native_capability(self):
        from leakscope_app.models import DiscoveryPreset
        from leakscope_app.discovery_catalog import catalog
        preset=DiscoveryPreset.objects.create(name='Fixture MMS',service_type='mms',mode='all',signals=[{'field':'title','value':'Fixture MMS'}])
        record=next(row for row in catalog() if row['key']=='preset:'+str(preset.pk))
        self.assertTrue(record['identity'])
        self.assertEqual(record['identity_protocol'],'mms')
        self.assertTrue(record['identity_operation'])

    def test_result_exposes_the_applicable_operation_and_its_limits(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.operations import _endpoint
        row=ingest_observation('fixture',endpoint('coap','udp'))
        data=_endpoint(row)
        self.assertTrue(data['native_identity']['available'])
        self.assertIn('/.well-known/core',data['native_identity']['operation'])
        restricted=ingest_observation('fixture',endpoint('stomp'))
        data=_endpoint(restricted)
        self.assertFalse(data['native_identity']['available'])
        self.assertIn('no-login',data['native_identity']['limitation'])
