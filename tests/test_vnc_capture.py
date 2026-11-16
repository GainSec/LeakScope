"""Loopback-only RFB peers exercise a display-only screenshot client."""
import hashlib
import importlib
import importlib.util
import json
import socket
import socketserver
import struct
import tempfile
import threading
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase


FORMAT=struct.pack('!BBBBHHHBBB3x',32,24,0,1,255,255,255,16,8,0)


def exact(sock,size):
    value=b''
    while len(value)<size:
        part=sock.recv(size-len(value))
        if not part:raise AssertionError('Client closed before fixture exchange completed')
        value+=part
    return value


@contextmanager
def peer(dialogue):
    errors=[]
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            try:dialogue(self.request)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as exc:errors.append(exc)
    with socketserver.TCPServer(('127.0.0.1',0),Handler) as server:
        thread=threading.Thread(target=server.handle_request,daemon=True);thread.start()
        try:yield server.server_address[1]
        finally:thread.join(3)
    if thread.is_alive():raise AssertionError('RFB fixture did not finish')
    if errors:raise errors[0]


def handshake(sock,version='003.008',width=2,height=1,security=1):
    sock.sendall(('RFB '+version+'\n').encode())
    selected=exact(sock,12)
    negotiated=selected.decode()[4:11]
    if negotiated=='003.003':sock.sendall(struct.pack('!I',security))
    else:
        sock.sendall(bytes([1,security]))
        if security!=1:return selected
        assert exact(sock,1)==b'\x01'
        if negotiated=='003.008':sock.sendall(b'\0'*4)
    if security!=1:return selected
    assert exact(sock,1)==b'\x01', 'Shared desktop is required'
    sock.sendall(struct.pack('!HH',width,height)+FORMAT+struct.pack('!I',7)+b'Fixture')
    return selected


def display_requests(sock,width=2,height=1):
    assert exact(sock,20)==b'\0'*4+FORMAT
    header=exact(sock,4);assert header[:2]==b'\x02\0'
    count=struct.unpack('!H',header[2:])[0]
    assert struct.unpack('!'+str(count)+'i',exact(sock,count*4))==(0,), 'Only RAW encoding is requested'
    assert exact(sock,10)==struct.pack('!BBHHHH',3,0,0,0,width,height)


def png_pixels(path):
    data=Path(path).read_bytes();assert data[:8]==b'\x89PNG\r\n\x1a\n'
    compressed=b'';offset=8;size=None
    while offset<len(data):
        length=struct.unpack('!I',data[offset:offset+4])[0];kind=data[offset+4:offset+8];body=data[offset+8:offset+8+length]
        assert zlib.crc32(kind+body)&0xffffffff==struct.unpack('!I',data[offset+8+length:offset+12+length])[0]
        if kind==b'IHDR':size=struct.unpack('!IIBBBBB',body);assert size[2:]==(8,2,0,0,0)
        if kind==b'IDAT':compressed+=body
        offset+=length+12
    return size[:2],zlib.decompress(compressed)


class VncCaptureTests(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config={'CAPTURE_DIR':self.tmp.name,'CAPTURE_TIMEOUT':1}

    def capture(self,dialogue,**options):
        self.assertIsNotNone(importlib.util.find_spec('leakscope.phase3.vnc'),'VNC adapter must exist')
        capture=importlib.import_module('leakscope.phase3.vnc').capture_vnc
        with peer(dialogue) as port:
            return capture({'address':'127.0.0.1','port':port,'transport':'tcp','service_type':'vnc','scheme':''},dict(self.config,**options))

    def test_raw_frame_colors_versions_and_no_input_events(self):
        for version in ['003.003','003.007','003.008','003.889']:
            with self.subTest(version=version):
                def dialogue(sock):
                    selected=handshake(sock,version)
                    self.assertEqual(selected,('RFB '+('003.003' if version=='003.889' else version)+'\n').encode())
                    display_requests(sock)
                    sock.sendall(b'\0\0\0\x01'+struct.pack('!HHHHi',0,0,2,1,0)+b'\0\0\xff\0\0\xff\0\0')
                    self.assertEqual(sock.recv(1),b'','Screenshot client must send no input events')
                result=self.capture(dialogue)
                self.assertEqual(result['verified_type'],'vnc')
                self.assertTrue(result['metadata']['read_only'])
                self.assertEqual(result['metadata']['input_events_sent'],0)
                self.assertFalse(result['partial'])
                shot=next(a for a in result['artifacts'] if a['kind']=='screenshot')
                self.assertEqual(png_pixels(shot['path']),((2,1),b'\0\xff\0\0\0\xff\0'))
                self.assertEqual(Path(shot['path']).stat().st_mode&0o777,0o600)

    def test_tiles_are_composited_at_their_actual_coordinates(self):
        def dialogue(sock):
            handshake(sock);display_requests(sock)
            sock.sendall(b'\0\0\0\x02'+struct.pack('!HHHHi',1,0,1,1,0)+b'\xff\0\0\0'+
                         struct.pack('!HHHHi',0,0,1,1,0)+b'\0\0\xff\0')
        result=self.capture(dialogue)
        shot=next(a for a in result['artifacts'] if a['kind']=='screenshot')
        self.assertEqual(png_pixels(shot['path']),((2,1),b'\0\xff\0\0\0\0\xff'))

    def test_large_frame_has_hashed_pixels_and_honest_bounded_transcript(self):
        pixels=b'\x12\x34\x56\0'*(128*129)
        def dialogue(sock):
            handshake(sock,width=128,height=129);display_requests(sock,128,129)
            sock.sendall(b'\0\0\0\x01'+struct.pack('!HHHHi',0,0,128,129,0)+pixels)
        result=self.capture(dialogue)
        wire=json.loads(Path(next(a for a in result['artifacts'] if a['kind']=='wire-transcript')['path']).read_text())
        self.assertGreater(wire['received']['bytes'],65536)
        self.assertFalse(wire['complete_wire_retained'])
        self.assertEqual(wire['pixel_bytes_omitted'],len(pixels))
        self.assertEqual(wire['rectangles'][0]['sha256'],hashlib.sha256(pixels).hexdigest())
        self.assertLess(len(json.dumps(wire)),150000)

    def test_authentication_required_never_sends_password_or_guess(self):
        def dialogue(sock):
            handshake(sock,security=2)
            self.assertEqual(sock.recv(1),b'')
        with self.assertRaisesMessage(ValueError,'authentication') as failure:self.capture(dialogue)
        result=failure.exception.result
        self.assertTrue(result['metadata']['authentication_required'])
        self.assertFalse(any(a['kind']=='screenshot' for a in result['artifacts']))

    def test_oversized_dimensions_are_rejected_before_frame_request(self):
        def dialogue(sock):
            handshake(sock,width=65535,height=65535)
            self.assertEqual(sock.recv(1),b'')
        with self.assertRaisesMessage(ValueError,'dimensions'):self.capture(dialogue)

    def test_unsupported_encoding_and_out_of_bounds_rectangles_fail(self):
        for rectangle,reason in [(struct.pack('!HHHHi',0,0,2,1,5),'encoding'),(struct.pack('!HHHHi',1,0,2,1,0),'bounds')]:
            with self.subTest(reason=reason):
                def dialogue(sock):
                    handshake(sock);display_requests(sock);sock.sendall(b'\0\0\0\x01'+rectangle)
                with self.assertRaisesMessage(ValueError,reason):self.capture(dialogue)

    def test_truncated_pixel_data_keeps_partial_wire_without_fake_screenshot(self):
        def dialogue(sock):
            handshake(sock);display_requests(sock)
            sock.sendall(b'\0\0\0\x01'+struct.pack('!HHHHi',0,0,2,1,0)+b'\0\0\xff')
        with self.assertRaisesMessage(ValueError,'truncated') as failure:self.capture(dialogue)
        self.assertTrue(failure.exception.result['partial'])
        self.assertFalse(any(a['kind']=='screenshot' for a in failure.exception.result['artifacts']))

    def test_partial_coverage_is_reported_and_unreceived_pixels_are_not_claimed(self):
        def dialogue(sock):
            handshake(sock);display_requests(sock)
            sock.sendall(b'\0\0\0\x01'+struct.pack('!HHHHi',0,0,1,1,0)+b'\0\0\xff\0')
        result=self.capture(dialogue)
        self.assertTrue(result['partial'])
        self.assertEqual(result['metadata']['pixels_received'],1)
        self.assertEqual(result['metadata']['pixels_total'],2)

    def test_client_selects_none_when_multiple_security_types_are_offered(self):
        def dialogue(sock):
            sock.sendall(b'RFB 003.008\n');self.assertEqual(exact(sock,12),b'RFB 003.008\n')
            sock.sendall(b'\x03\x02\x10\x01');self.assertEqual(exact(sock,1),b'\x01');sock.sendall(b'\0'*4)
            self.assertEqual(exact(sock,1),b'\x01');sock.sendall(struct.pack('!HH',2,1)+FORMAT+struct.pack('!I',0))
            display_requests(sock);sock.sendall(b'\0\0\0\x01'+struct.pack('!HHHHi',0,0,2,1,0)+b'\0'*8)
        self.assertFalse(self.capture(dialogue)['partial'])

    def test_invalid_endpoint_and_blacklist_do_not_connect(self):
        self.assertIsNotNone(importlib.util.find_spec('leakscope.phase3.vnc'),'VNC adapter must exist')
        module=importlib.import_module('leakscope.phase3.vnc')
        with patch.object(module,'connect_tcp',side_effect=AssertionError('No connection')):
            for endpoint in [{'address':'host; bad','port':5900},{'address':'127.0.0.1','port':0},
                             {'address':'127.0.0.1','port':5900,'transport':'udp'}]:
                with self.assertRaises(ValueError):module.capture_vnc(endpoint,self.config)
            with self.assertRaisesMessage(ValueError,'blacklisted'):
                module.capture_vnc({'address':'127.0.0.1','port':5900},dict(self.config,blacklist=['127.0.0.1']))

    def test_message_and_frame_byte_budgets_are_enforced(self):
        def bells(sock):
            handshake(sock);display_requests(sock);sock.sendall(b'\x02'*17)
        with self.assertRaisesMessage(ValueError,'message budget'):self.capture(bells)
        def rectangles(sock):
            handshake(sock);display_requests(sock)
            sock.sendall(b'\0\0\0\x05'+(struct.pack('!HHHHi',0,0,2,1,0)+b'\0'*8)*5)
        with patch('leakscope.phase3.vnc.MAX_FRAME_BYTES',32):
            with self.assertRaisesMessage(ValueError,'byte budget'):self.capture(rectangles)

    def test_slow_peer_deadline_is_a_bounded_partial_failure(self):
        def dialogue(sock):
            sock.sendall(b'RFB 003.008\n');exact(sock,12);time.sleep(.12)
        started=time.monotonic()
        with self.assertRaisesMessage(ValueError,'timed out') as failure:self.capture(dialogue,VNC_TIMEOUT=.05)
        self.assertLess(time.monotonic()-started,.5)
        self.assertFalse(failure.exception.result['metadata']['screenshot_available'])

    def test_untrusted_server_name_length_is_checked_before_reading_it(self):
        def dialogue(sock):
            sock.sendall(b'RFB 003.008\n');exact(sock,12);sock.sendall(b'\x01\x01');exact(sock,1);sock.sendall(b'\0'*4);exact(sock,1)
            sock.sendall(struct.pack('!HH',2,1)+FORMAT+struct.pack('!I',0xffffffff))
            self.assertEqual(sock.recv(1),b'')
        with self.assertRaisesMessage(ValueError,'desktop name'):self.capture(dialogue)
