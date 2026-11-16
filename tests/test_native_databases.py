"""Real loopback peers for database identification; no service credentials."""
import importlib
import errno
import socket
import struct
import threading
import time
from contextlib import contextmanager

from django.test import SimpleTestCase
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, StreamEnded

from leakscope.phase3.native import Reader
from leakscope.phase3.wire import RecordedSocket, WireRecorder


def exact(sock, size):
    body = b''
    while len(body) < size:
        chunk = sock.recv(size - len(body))
        if not chunk:
            raise AssertionError('Client closed before the complete request')
        body += chunk
    return body


@contextmanager
def peer(dialogue):
    errors = []
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(2)
        def serve():
            try:
                with listener.accept()[0] as server:
                    server.settimeout(2)
                    dialogue(server)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1]
        finally:
            thread.join(3)
            if thread.is_alive():
                raise AssertionError('Loopback peer did not terminate')
            if errors:
                raise errors[0]


def tds(body, *, kind=4, status=1):
    return struct.pack('!BBHHBB', kind, status, len(body) + 8, 0, 1, 0) + body


def cql(body, *, opcode=6, flags=0, stream=1, version=0x84, size=None):
    return struct.pack('!BBhBI', version, flags, stream, opcode,
                       len(body) if size is None else size) + body


def string(value):
    return struct.pack('!H', len(value)) + value


def supported():
    return b'\x00\x02' + string(b'CQL_VERSION') + b'\x00\x01' + string(b'3.0.0') + string(b'COMPRESSION') + b'\x00\x01' + string(b'lz4')


def aero(body, *, prefix=b'\x02\x01', size=None):
    return prefix + (len(body) if size is None else size).to_bytes(6, 'big') + body


def ddm(code, body):
    return struct.pack('!HH', len(body) + 4, code) + body


def drda(body, *, fmt=2, correlation=1):
    body = ddm(0x1443, body)
    return struct.pack('!HBBH', len(body) + 6, 0xd0, fmt, correlation) + body


def tns_refuse(description, *, declared=None, kind=4):
    body = b'\x22\0'+struct.pack('!H', len(description) if declared is None else declared)+description
    return struct.pack('!HHBBH', len(body)+8, 0, kind, 0, 0)+body


def maxdb_packet(version=b'XServer 7.6.00 Build 037', *, endian='<', kind=54,
                 receiver=0, status=0, residual=0, variable=None):
    if variable is None:
        variable = bytes([len(version)+3, 0x56])+version+b'\0'
    body = bytes([0, 2 if endian == '<' else 1])+struct.pack(endian+'HBBBBIIII',
        40+len(variable), 0, 11, 0, 0, 16384, 0, 0, 0)+b' '*16+variable
    return struct.pack(endian+'IBBBBIIHBBI', len(body)+24, 3, kind, 0,
        residual, 0, receiver, status, 4 if endian == '<' else 3, 0, len(body)+24)+body


def das_record(tag, payload):
    return struct.pack('!III', len(payload)+12, 12, tag)+payload


def das_version_body(version=b'SQL09071', *, status=0, blank=b'     '):
    def text(value): return das_record(4, struct.pack('!I', 1208)+value+b'\0')
    fields = das_record(0x4c, struct.pack('!I', status))+das_record(0x4f, b'\0'*24)
    fields += das_record(0x4c, b'\0'*4)+text(version)+text(blank)+text(b' '*11)
    features = b''.join(das_record(0x48, struct.pack('!I', i)) for i in (9, 7, 0, 1, 1))
    return das_record(0x5a, fields)+das_record(8, features)


def das_packet(body, *, opcode=0x24, declared=None):
    return DAS_REQUEST[:36]+bytes([opcode])+struct.pack('<I', len(body) if declared is None else declared)+body


TDS_OPTIONS = bytes.fromhex('00000b00060100110001ff100003e8000003')
TDS_REQUEST = bytes.fromhex('1201001a0000010000000b00060100110001ff04000000000000')
CQL_REQUEST = bytes.fromhex('040000010500000000')
AERO_REQUEST = aero(b'build\nversion\n')
HANA_REQUEST = bytes.fromhex('ffffffff04140004010000010101')
HANA_REPLY = bytes.fromhex('0414000401000000')
MONET_CHALLENGE = b'fixtureRandom:mserver:9:SHA512,SHA256:LIT:SHA512:'
TNS_REQUEST = bytes.fromhex('005a0000010000000136012c000008007fff7f08000000010020003a0000000000000000000000000000000034e6000000010000000000000000')+b'(CONNECT_DATA=(COMMAND=version))'
MAXDB_REQUEST = bytes.fromhex('40000000033500000000000000000000000004004000000000022800000b000000400000000000000000000000000000')+b' '*16
DAS_REQUEST = bytes.fromhex('00000000444232444153202020202020010400000010397a000100000000000000000000010c0000000000000c0000000c00000004')


class DatabaseIdentityTests(SimpleTestCase):
    def grpc_exchange(self, **response):
        adapter = self.adapter('milvus')
        request = {}
        def dialogue(server):
            connection = H2Connection(config=H2Configuration(client_side=False, header_encoding='utf-8'))
            connection.initiate_connection()
            server.sendall(connection.data_to_send())
            ended = False
            while not ended:
                data = server.recv(4096)
                self.assertTrue(data)
                for event in connection.receive_data(data):
                    if isinstance(event, RequestReceived):
                        self.assertNotIn('headers', request)
                        request['headers'] = dict(event.headers)
                    elif isinstance(event, DataReceived):
                        request['body'] = request.get('body', b'')+event.data
                    elif isinstance(event, StreamEnded):
                        ended = True
            if response.get('close_early'):
                server.shutdown(socket.SHUT_WR)
            else:
                connection.send_headers(1, [(':status', '200'),
                    ('content-type', response.get('content_type', 'application/grpc'))])
                connection.send_data(1, response.get('body', b'\0\0\0\0\x02\x08\x01'))
                connection.send_headers(1, [('grpc-status', '0')], end_stream=True)
                payload = connection.data_to_send()
                for value in payload:
                    server.sendall(bytes([value]))
                server.shutdown(socket.SHUT_WR)
            # Drain the client's SETTINGS ACK before closing TCP, avoiding
            # an RST caused by unread acknowledgement bytes on macOS.
            while data := server.recv(4096):
                for event in connection.receive_data(data):
                    self.assertNotIsInstance(event, RequestReceived)
        with peer(dialogue) as port:
            with socket.create_connection(('127.0.0.1', port), timeout=1) as raw:
                deadline = time.monotonic()+1
                wire = RecordedSocket(raw, WireRecorder(), deadline)
                result = adapter(wire, Reader(wire, deadline), '127.0.0.1', port)
        return result, request

    def adapter(self, key):
        try:
            module = importlib.import_module('leakscope.phase3.native_databases')
        except ModuleNotFoundError:
            self.fail('Database identity adapter module has not been implemented')
        self.assertIn(key, module.TCP_ADAPTERS)
        return module.TCP_ADAPTERS[key]

    def exchange(self, key, request, response, *, fragmented=False):
        adapter = self.adapter(key)
        recorder = WireRecorder()
        def dialogue(server):
            if request:
                self.assertEqual(exact(server, len(request)), request)
            if fragmented:
                for value in response:
                    server.sendall(bytes([value]))
            else:
                server.sendall(response)
            try:
                server.shutdown(socket.SHUT_WR)
                followup = server.recv(1)
            except OSError as exc:
                # Rejecting a malformed header leaves its trailing response
                # unread; closing TCP may reset instead of producing EOF.
                if exc.errno not in (errno.ECONNRESET, errno.ENOTCONN, errno.EPIPE):
                    raise
                followup = b''
            self.assertEqual(followup, b'', 'Adapter sent a follow-up operation')
        with peer(dialogue) as port:
            with socket.create_connection(('127.0.0.1', port), timeout=1) as raw:
                deadline = time.monotonic() + 1
                wire = RecordedSocket(raw, recorder, deadline)
                try:
                    result = adapter(wire, Reader(wire, deadline), '127.0.0.1', port)
                finally:
                    # Check the recorded sends even when parsing rejects the
                    # reply and TCP reset prevents observing a clean EOF.
                    self.assertEqual(bytes(recorder.sent), request)
        return result, recorder

    def reject(self, key, request, responses):
        for response in responses:
            with self.subTest(key=key, response=response[:32].hex()):
                with self.assertRaises(ValueError):
                    self.exchange(key, request, response)

    def test_tds_prelogin_only_and_fragmented_response(self):
        (metadata, verified), recorder = self.exchange('mssql', TDS_REQUEST, tds(TDS_OPTIONS), fragmented=True)
        self.assertEqual(verified, 'mssql')
        self.assertEqual(metadata['version'], '16.0.1000.0')
        self.assertEqual(metadata['encryption'], 3)
        self.assertIn('PRELOGIN', metadata['operation'])
        self.assertEqual(bytes(recorder.received), tds(TDS_OPTIONS))

    def test_tds_rejects_framing_option_bounds_and_truncation(self):
        bad_offset = bytes.fromhex('00ffff00060100110001ff100003e8000003')
        overlap = bytes.fromhex('00000b000601000b0001ff100003e8000003')
        self.reject('mssql', TDS_REQUEST, [b'', b'\x04', tds(TDS_OPTIONS)[:-1],
            tds(TDS_OPTIONS, kind=0x12), tds(TDS_OPTIONS, status=0),
            bytes.fromhex('0401ffff00000100'), tds(b'\xff'), tds(bad_offset),
            tds(overlap), tds(TDS_OPTIONS[:-1] + b'\x04')])

    def test_cassandra_and_scylla_only_send_options(self):
        for key in ('cassandra', 'scylla'):
            with self.subTest(key=key):
                (metadata, verified), _ = self.exchange(key, CQL_REQUEST, cql(supported()), fragmented=True)
                self.assertEqual(verified, 'cassandra')
                self.assertEqual(metadata['supported']['CQL_VERSION'], ['3.0.0'])
                self.assertFalse(metadata['product_verified'])

    def test_cql_rejects_wrong_frames_lengths_and_malformed_maps(self):
        self.reject('cassandra', CQL_REQUEST, [b'', cql(supported())[:-1],
            cql(supported(), version=4), cql(supported(), opcode=2),
            cql(supported(), stream=2), cql(supported(), flags=1),
            cql(b'', size=16385), cql(b'\xff\xff'), cql(supported()+b'x'),
            cql(b'\x00\x01'+string(b'CQL_VERSION')+b'\x00\x01'+string(b''))])

    def test_aerospike_only_requests_two_identity_info_fields(self):
        (metadata, verified), _ = self.exchange('aerospike', AERO_REQUEST,
            aero(b'build\t8.1.1.0\nversion\tAerospike Community Edition build 8.1.1.0\n'))
        self.assertEqual(verified, 'aerospike')
        self.assertEqual(metadata['build'], '8.1.1.0')

    def test_aerospike_rejects_wrong_header_unrequested_data_and_oversize(self):
        good = b'build\t8.1.1.0\n'
        self.reject('aerospike', AERO_REQUEST, [b'', aero(good)[:-1],
            aero(good, prefix=b'\x02\x03'), aero(b'', size=16385),
            aero(b'build\n'), aero(b'build\tERROR:80:not authenticated\n'),
            aero(b'namespace\tprivate\n'), aero(good+good), aero(b'build\t8.1.1.0')])

    def test_hana_initial_protocol_exchange_only(self):
        (metadata, verified), _ = self.exchange('hana', HANA_REQUEST, HANA_REPLY, fragmented=True)
        self.assertEqual(verified, 'hana')
        self.assertEqual(metadata['protocol_version'], '4.1')
        self.assertEqual(metadata['product_version'], '4.20')

    def test_hana_rejects_truncated_reserved_and_unsupported_protocol(self):
        self.reject('hana', HANA_REQUEST, [b'', HANA_REPLY[:-1], b'\0'*8,
            HANA_REPLY[:-1]+b'\x01', bytes.fromhex('0414000301000000')])

    def test_monetdb_reads_challenge_without_answering_it(self):
        response = struct.pack('<H', (len(MONET_CHALLENGE)<<1)|1)+MONET_CHALLENGE
        (metadata, verified), recorder = self.exchange('monetdb', b'', response, fragmented=True)
        self.assertEqual(verified, 'monetdb')
        self.assertEqual(metadata['endpoint_kind'], 'mserver')
        self.assertEqual(metadata['protocol_version'], 9)
        self.assertEqual(bytes(recorder.sent), b'')
        self.assertNotIn('challenge', metadata)

    def test_monetdb_supports_split_message_blocks(self):
        first, second = MONET_CHALLENGE[:13], MONET_CHALLENGE[13:]
        response = struct.pack('<H', len(first)<<1)+first+struct.pack('<H', (len(second)<<1)|1)+second
        (metadata, verified), _ = self.exchange('monetdb', b'', response)
        self.assertEqual(verified, 'monetdb')
        self.assertEqual(metadata['byte_order'], 'LIT')

    def test_monetdb_rejects_invalid_challenges_and_block_limits(self):
        def block(body): return struct.pack('<H', (len(body)<<1)|1)+body
        self.reject('monetdb', b'', [b'', b'\x01', block(MONET_CHALLENGE)[:-1],
            b'\xff\xff', b'\x00\x00'*5, block(b'anything:else:9:SHA256:LIT:SHA512:'),
            block(MONET_CHALLENGE.replace(b':9:', b':8:')),
            block(MONET_CHALLENGE.replace(b':LIT:', b':BAD:'))])

    def test_ceph_reads_v1_or_v2_banner_and_never_authenticates(self):
        for response, version in ((b'ceph v027', 1),
                                  (b'ceph v2\n\x10\x00'+struct.pack('<QQ', 3, 1), 2)):
            with self.subTest(version=version):
                (metadata, verified), recorder = self.exchange('ceph', b'', response, fragmented=True)
                self.assertEqual(verified, 'ceph')
                self.assertEqual(metadata['messenger_version'], version)
                self.assertFalse(metadata['monitor_role_verified'])
                self.assertEqual(bytes(recorder.sent), b'')

    def test_ceph_rejects_wrong_banner_lengths_and_features(self):
        self.reject('ceph', b'', [b'', b'ceph v02', b'ceph v026',
            b'ceph v2\n\xff\xff', b'ceph v2\n\x0f\x00'+b'x'*15,
            b'ceph v2\n\x10\x00'+struct.pack('<QQ', 1, 2),
            b'ceph v2\n\x10\x00'+b'\0'*15])

    def test_postgres_family_aliases_do_not_claim_product(self):
        for key in ('vertica', 'timescaledb'):
            with self.subTest(key=key):
                (metadata, verified), _ = self.exchange(key, bytes.fromhex('0000000804d2162f'), b'N')
                self.assertEqual(verified, 'postgresql')
                self.assertFalse(metadata['product_verified'])
        self.reject('vertica', bytes.fromhex('0000000804d2162f'), [b'', b'X'])

    def test_drda_excsat_sends_no_security_or_database_access_command(self):
        adapter = self.adapter('db2')
        recorder = WireRecorder()
        fields = ddm(0x1147, 'DB2/LINUXX8664'.encode('cp037')) + ddm(0x115a, 'SQL11050'.encode('cp037')) + ddm(0x1404, struct.pack('!HH', 0x1403, 3))
        def dialogue(server):
            header = exact(server, 6)
            size, magic, fmt, correlation = struct.unpack('!HBBH', header)
            self.assertEqual((magic, fmt, correlation), (0xd0, 1, 1))
            request = exact(server, size-6)
            self.assertEqual(struct.unpack('!HH', request[:4]), (size-6, 0x1041))
            offset = 4
            while offset < len(request):
                length, code = struct.unpack('!HH', request[offset:offset+4])
                self.assertIn(code, (0x115e, 0x116d, 0x115a, 0x1404, 0x1147))
                self.assertGreaterEqual(length, 4)
                offset += length
            self.assertEqual(offset, len(request))
            server.sendall(drda(fields))
            server.shutdown(socket.SHUT_WR)
            self.assertEqual(server.recv(1), b'')
        with peer(dialogue) as port:
            with socket.create_connection(('127.0.0.1', port), timeout=1) as raw:
                deadline = time.monotonic()+1
                wire = RecordedSocket(raw, recorder, deadline)
                metadata, verified = adapter(wire, Reader(wire, deadline), '127.0.0.1', port)
        self.assertEqual(verified, 'drda')
        self.assertEqual(metadata['server_class'], 'DB2/LINUXX8664')
        self.assertEqual(metadata['server_release'], 'SQL11050')
        self.assertFalse(metadata['product_verified'])
        # Replay the exact observed request against malformed real peers.
        self.reject('drda', bytes(recorder.sent), [b'', drda(fields)[:-1],
            drda(fields, correlation=2), drda(fields, fmt=3),
            bytes.fromhex('7fffd0020001'), drda(ddm(0x1147, b'X')[:-1]),
            drda(ddm(0x1404, b'x')), drda(ddm(0x1147, b'X')*2)])

    def test_capabilities_are_explicit_and_constraints_are_not_adapters(self):
        self.adapter('mssql')
        module = importlib.import_module('leakscope.phase3.native_databases')
        self.assertEqual(module.SUPPORTED_TYPES, frozenset(module.TCP_ADAPTERS))
        for key in module.SUPPORTED_TYPES:
            for field in ('operation', 'protocol', 'limitation', 'sources'):
                self.assertTrue(module.CAPABILITIES[key][field], (key, field))
        for key in ('informix', 'firebird'):
            self.assertNotIn(key, module.SUPPORTED_TYPES)
            self.assertTrue(module.CONSTRAINTS[key]['reason'])

    def test_oracle_listener_version_without_credentials_or_service(self):
        descriptor = b'(DESCRIPTION=(TMP=)(VSNNUM=318767104)(ERR=0))'
        (metadata, verified), recorder = self.exchange('oracle', TNS_REQUEST, tns_refuse(descriptor), fragmented=True)
        self.assertEqual(verified, 'oracle')
        self.assertEqual(metadata['listener_version'], '19.0.0.0.0')
        self.assertFalse(metadata['database_verified'])
        for forbidden in (b'USER=', b'SERVICE_NAME=', b'SID=', b'AUTH'):
            self.assertNotIn(forbidden, bytes(recorder.sent))

    def test_oracle_listener_command_denial_preserves_identity_without_retry(self):
        descriptor = b'(DESCRIPTION=(TMP=)(VSNNUM=318767104)(ERR=1189))'
        (metadata, verified), _ = self.exchange('oracle', TNS_REQUEST, tns_refuse(descriptor))
        self.assertEqual(verified, 'oracle')
        self.assertEqual(metadata['listener_error'], 1189)
        self.assertFalse(metadata['version_command_succeeded'])
        self.assertFalse(metadata['database_verified'])

    def test_oracle_rejects_truncation_unrelated_and_malformed_descriptors(self):
        good = b'(DESCRIPTION=(TMP=)(VSNNUM=318767104)(ERR=0))'
        self.reject('oracle', TNS_REQUEST, [b'', b'\0\x10', tns_refuse(good)[:-1],
            tns_refuse(good, declared=1), tns_refuse(good, kind=6),
            bytes.fromhex('ffff000004000000'), tns_refuse(b'not a listener'),
            tns_refuse(good[:-1]), tns_refuse(good.replace(b'318767104', b'9999999999')),
            tns_refuse(b'(DESCRIPTION=(ERR=0))'), tns_refuse(b'(DESCRIPTION=(ERR=123))')])

    def test_maxdb_xserver_ping_with_no_database_or_dbm_commands(self):
        for endian in ('<', '>'):
            with self.subTest(endian=endian):
                (metadata, verified), recorder = self.exchange('maxdb', MAXDB_REQUEST, maxdb_packet(endian=endian), fragmented=True)
                self.assertEqual(verified, 'maxdb')
                self.assertEqual(metadata['version'], 'XServer 7.6.00 Build 037')
                self.assertFalse(metadata['database_verified'])
                self.assertEqual(bytes(recorder.sent)[48:64], b' '*16)
                self.assertNotIn(b'dbm_', bytes(recorder.sent))

    def test_maxdb_rejects_wrong_routing_lengths_truncation_and_bad_tlv(self):
        good = maxdb_packet()
        bad_length = b'\xff\xff\xff\x7f'+good[4:]
        wrong_connect_length = good[:26]+b'\x00\x00'+good[28:]
        self.reject('maxdb', MAXDB_REQUEST, [b'', good[:23], good[:-1],
            maxdb_packet(kind=52), maxdb_packet(receiver=1), maxdb_packet(status=1),
            maxdb_packet(residual=1), bad_length, wrong_connect_length,
            maxdb_packet(variable=b'\0V'), maxdb_packet(variable=b'\x05Vabc'),
            maxdb_packet(variable=b'\x03X\0'), maxdb_packet(version=b'bad\nvalue')])

    def test_milvus_candidate_uses_generic_health_without_product_claim(self):
        (metadata, verified), request = self.grpc_exchange()
        self.assertEqual(verified, 'grpc')
        self.assertEqual(metadata['health_status'], 'SERVING')
        self.assertFalse(metadata['product_verified'])
        self.assertNotIn('product', metadata)
        self.assertEqual(request['headers'][':path'], '/grpc.health.v1.Health/Check')
        self.assertEqual(request['body'], b'\0\0\0\0\0')

    def test_milvus_health_rejects_non_grpc_truncated_and_oversized_replies(self):
        for response in ({'content_type': 'application/json'}, {'body': b'\0\0\0\0\x02\x08'},
                         {'body': b'\0\0\x01\0\0'}, {'close_early': True}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.grpc_exchange(**response)

    def test_db2_das_single_ping_identifies_administration_protocol_only(self):
        (metadata, verified), recorder = self.exchange('db2-das', DAS_REQUEST,
            das_packet(das_version_body()), fragmented=True)
        self.assertEqual(verified, 'db2-das')
        self.assertEqual(metadata['version'], '9.7.1')
        self.assertEqual(metadata['product_id'], 'SQL09071')
        self.assertFalse(metadata['database_verified'])
        self.assertEqual(bytes(recorder.sent), DAS_REQUEST)
        self.assertEqual(len(recorder.sent), 53)
        for forbidden in (b'db2dasKnownDscv', b'db2KnownDscvSrv', b'USER', b'AUTH'):
            self.assertNotIn(forbidden, bytes(recorder.sent))

    def test_db2_das_and_drda_remain_distinct_adapters(self):
        module = importlib.import_module('leakscope.phase3.native_databases')
        self.assertIn('db2-das', module.SUPPORTED_TYPES)
        self.assertIsNot(module.TCP_ADAPTERS['db2-das'], module.TCP_ADAPTERS['drda'])
        self.assertEqual(module.CAPABILITIES['db2-das']['protocol'], 'db2-das')
        self.assertEqual(module.CAPABILITIES['db2']['protocol'], 'drda')
        (metadata, _), _ = self.exchange('db2-das', DAS_REQUEST, das_packet(das_version_body(b'SQL11050')))
        self.assertEqual(metadata['version'], '11.5.0')

    def test_db2_das_rejects_bad_headers_lengths_status_and_profile_content(self):
        good = das_packet(das_version_body())
        bad_magic = b'\x01'+good[1:]
        bad_marker = good[:22]+b'zz'+good[24:]
        bad_echo = good[:25]+b'\x02'+good[26:]
        invalid_record = das_record(0x5a, struct.pack('!III', 0, 12, 4))
        self.reject('db2-das', DAS_REQUEST, [b'', good[:40], good[:-1], DAS_REQUEST,
            bad_magic, bad_marker, bad_echo, das_packet(b'', declared=16385),
            das_packet(das_version_body(), opcode=6), das_packet(invalid_record),
            das_packet(das_version_body(status=1)), das_packet(das_version_body(b'not-db2')),
            das_packet(das_version_body(blank=b'[db>private]')),
            das_packet(das_version_body()+das_record(4, b'private profile')),
            das_packet(das_version_body()+b'\x00')])
