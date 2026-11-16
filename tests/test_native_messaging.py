import socket
import struct
import time

from django.test import SimpleTestCase
from tests.test_phase3_capture import tcp_fixture
from leakscope.phase3.native import Reader
from leakscope.phase3.wire import RecordedSocket, WireRecorder
from leakscope.phase3.native_messaging import TCP_ADAPTERS


def longstr(value):
    return struct.pack('!I',len(value))+value


def amqp_start(table=b'',mechanisms=b'PLAIN AMQPLAIN',locales=b'en_US'):
    body=struct.pack('!HHBB',10,10,0,9)+longstr(table)+longstr(mechanisms)+longstr(locales)
    return struct.pack('!BHI',1,0,len(body))+body+b'\xce'


class MessagingIdentityTests(SimpleTestCase):
    def probe(self,service,dialogue):
        with tcp_fixture(dialogue) as port,socket.create_connection(('127.0.0.1',port),timeout=1) as raw:
            recorder=WireRecorder();deadline=time.monotonic()+1
            wire=RecordedSocket(raw,recorder,deadline)
            metadata,verified=TCP_ADAPTERS[service](wire,Reader(wire,deadline),'127.0.0.1',port)
            return metadata,verified,recorder

    def test_amqp_stops_before_start_ok_or_authentication(self):
        table=b'\x07productS'+longstr(b'RabbitMQ')+b'\x07versionS'+longstr(b'4.0.0')
        def dialogue(sock):
            self.assertEqual(sock.recv(1024),b'AMQP\x00\x00\x09\x01')
            sock.sendall(amqp_start(table))
            self.assertEqual(sock.recv(1024),b'')
        metadata,verified,wire=self.probe('amqp',dialogue)
        self.assertEqual(verified,'amqp')
        self.assertEqual(metadata['server_properties'],{'product':'RabbitMQ','version':'4.0.0'})
        self.assertEqual(metadata['authentication_mechanisms'],['PLAIN','AMQPLAIN'])
        self.assertEqual(bytes(wire.sent),b'AMQP\x00\x00\x09\x01')
        self.assertIn('no authentication',metadata['operation'])

    def test_amqp_version_advertisement_does_not_retry_or_start_sasl(self):
        for header in (b'AMQP\x00\x01\x00\x00',b'AMQP\x03\x01\x00\x00'):
            with self.subTest(header=header):
                def dialogue(sock):
                    self.assertEqual(sock.recv(1024),b'AMQP\x00\x00\x09\x01');sock.sendall(header)
                    self.assertEqual(sock.recv(1024),b'')
                metadata,verified,_=self.probe('amqp',dialogue)
                self.assertEqual(verified,'amqp');self.assertEqual(metadata['protocol_version'],'1.0.0')
                self.assertEqual(metadata['sasl_required'],header[4]==3)

    def test_amqp_nested_capabilities_are_bounded_without_exporting_unknown_fields(self):
        nested=b'\x12publisher_confirmst\x01'
        table=b'\x0ccapabilitiesF'+longstr(nested)+b'\x07productS'+longstr(b'RabbitMQ')
        def dialogue(sock):sock.recv(1024);sock.sendall(amqp_start(table))
        metadata,verified,_=self.probe('amqp',dialogue)
        self.assertEqual(verified,'amqp');self.assertEqual(metadata['server_properties'],{'product':'RabbitMQ'})

    def test_amqp_empty_or_duplicate_nested_field_names_are_rejected(self):
        for table in (b'\0V',b'\x0ccapabilitiesF'+longstr(b'\x01xt\x01\x01xt\x00')):
            with self.subTest(table=table):
                def dialogue(sock):sock.recv(1024);sock.sendall(amqp_start(table))
                with self.assertRaises(ValueError):self.probe('amqp',dialogue)

    def test_amqp_rejects_invalid_frame_method_lengths_and_trailing_data(self):
        frames=[b'AMQP\x01\x02\x03\x04',struct.pack('!BHI',1,0,65536),
                amqp_start().replace(b'\x00\x0a\x00\x0a',b'\x00\x14\x00\x0a',1),
                amqp_start()[:-1]+b'\x00',amqp_start(b'\x03fooS\x00\x01\x00\x00')]
        for frame in frames:
            with self.subTest(frame=frame):
                def dialogue(sock):sock.recv(1024);sock.sendall(frame)
                with self.assertRaises(ValueError):self.probe('amqp',dialogue)

    def test_generic_echo_service_cannot_verify_amqp(self):
        def dialogue(sock):sock.sendall(sock.recv(8))
        with self.assertRaises(ValueError):self.probe('amqp',dialogue)

    def test_kafka_api_versions_validates_correlation_without_topic_requests(self):
        def dialogue(sock):
            size=struct.unpack('!I',sock.recv(4))[0];request=sock.recv(size)
            self.assertEqual(request[:4],struct.pack('!hh',18,0))
            correlation=struct.unpack('!i',request[4:8])[0]
            body=struct.pack('!ihi',correlation,0,2)+struct.pack('!hhh',18,0,4)+struct.pack('!hhh',3,0,12)
            sock.sendall(struct.pack('!I',len(body))+body)
            self.assertEqual(sock.recv(1024),b'')
        metadata,verified,wire=self.probe('kafka',dialogue)
        self.assertEqual(verified,'kafka');self.assertEqual(len(metadata['api_versions']),2)
        self.assertEqual(len(wire.sent),4+struct.unpack('!I',wire.sent[:4])[0])

    def test_kafka_rejects_wrong_correlation_count_and_version_ranges(self):
        for kind in ('correlation','count','range','length'):
            with self.subTest(kind=kind):
                def dialogue(sock):
                    size=struct.unpack('!I',sock.recv(4))[0];request=sock.recv(size)
                    corr=struct.unpack('!i',request[4:8])[0]+(1 if kind=='correlation' else 0)
                    body=struct.pack('!ihi',corr,0,257 if kind=='count' else 1)+struct.pack('!hhh',18,4,0 if kind=='range' else 4)
                    sock.sendall(struct.pack('!I',65536 if kind=='length' else len(body))+body)
                with self.assertRaises(ValueError):self.probe('kafka',dialogue)

    def test_zookeeper_server_status_does_not_open_a_session_or_read_znodes(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(1024),b'srvr')
            sock.sendall(b'Zookeeper version: 3.9.3-abc, built on 2026\nLatency min/avg/max: 0/1/2\nMode: follower\nNode count: 42\n')
        metadata,verified,wire=self.probe('zookeeper',dialogue)
        self.assertEqual(verified,'zookeeper');self.assertEqual(metadata['version'],'3.9.3-abc')
        self.assertEqual(metadata['mode'],'follower');self.assertEqual(bytes(wire.sent),b'srvr')

    def test_zookeeper_whitelist_refusal_is_explicit_not_fabricated_status(self):
        def dialogue(sock):
            sock.recv(1024);sock.sendall(b'srvr is not executed because it is not in the whitelist.\n')
        metadata,verified,_=self.probe('zookeeper',dialogue)
        self.assertEqual(verified,'zookeeper');self.assertTrue(metadata['command_restricted'])
        self.assertNotIn('version',metadata)

    def test_zookeeper_generic_http_or_truncated_version_is_not_protocol_identity(self):
        for response in (b'HTTP/1.1 200 OK\n\nZooKeeper',b'Zookeeper version:',b'OK\n'):
            with self.subTest(response=response):
                def dialogue(sock):sock.recv(1024);sock.sendall(response)
                with self.assertRaises(ValueError):self.probe('zookeeper',dialogue)

    def test_zookeeper_raw_socket_trickle_cannot_extend_total_deadline(self):
        def dialogue(sock):
            self.assertEqual(sock.recv(4),b'srvr')
            try:
                for chunk in (b'Zookeeper version: 3.9.',b'3-abc\n',b'Mode: follower\n'):
                    sock.sendall(chunk);time.sleep(.025)
            except OSError:pass
        with tcp_fixture(dialogue) as port,socket.create_connection(('127.0.0.1',port),timeout=.2) as raw:
            with self.assertRaises((ValueError,TimeoutError)):
                TCP_ADAPTERS['zookeeper'](raw,Reader(raw,time.monotonic()+.03),'127.0.0.1',port)
