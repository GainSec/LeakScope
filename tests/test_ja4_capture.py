import importlib,unittest,pathlib,struct,socket

class JA4CaptureTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.find_spec('leakscope.phase3.ja4_capture');self.assertIsNotNone(spec,'JA4 capture analyzer is missing');return importlib.import_module(spec.name)
    def test_recorded_control_capture_matches_pinned_foxio_outputs(self):
        m=self.module();rows=m.analyze_pcap((pathlib.Path(__file__).parent/'fixtures/ja4/controls.pcap').read_bytes())
        fps=[f for r in rows for f in r['fingerprints']]
        self.assertEqual({f['value'] for f in fps if f['kind']=='ja4s'},{'t1205o1_c02f_1ece00aee4e9','t1205o1_c030_1ece00aee4e9'})
        self.assertEqual({f['value'] for f in fps if f['kind']=='ja4'},{'t12d0308o1_0f9cd282b7f5_e7e480e5a997'})
        self.assertIn('65160_2-4-8-1-3_1460_7',{f['value'] for f in fps if f['kind']=='ja4ts'})
        self.assertTrue(all(f.get('subject')=='probe' for f in fps if f['kind']=='ja4'))
    def test_malformed_and_oversized_captures_are_rejected(self):
        m=self.module()
        for data in (b'',b'not pcap',b'a'*(4*1024*1024+1)):
            with self.assertRaises(ValueError):m.analyze_pcap(data)
    def test_truncated_packet_is_rejected(self):
        m=self.module();data=(pathlib.Path(__file__).parent/'fixtures/ja4/controls.pcap').read_bytes()
        with self.assertRaises(ValueError):m.analyze_pcap(data[:-3])
    def test_reused_four_tuple_with_reversed_roles_keeps_fingerprints_separate(self):
        m=self.module()
        def packet(src,dst,sp,dp,seq,flags,window,ack=0):
            tcp=struct.pack('!HHIIBBHHH',sp,dp,seq,ack,0x50,flags,window,0,0)
            ip=struct.pack('!BBHHHBBH4s4s',0x45,0,40,1,0,64,6,0,socket.inet_aton(src),socket.inet_aton(dst))
            frame=b'\0'*12+b'\x08\0'+ip+tcp
            return struct.pack('<IIII',0,0,len(frame),len(frame))+frame
        a,b='192.0.2.1','192.0.2.2'
        pcap=bytes.fromhex('d4c3b2a1')+struct.pack('<HHIIII',2,4,0,0,262144,1)
        pcap+=packet(a,b,12345,443,100,2,1000)+packet(b,a,443,12345,200,18,2000,101)
        pcap+=packet(b,a,443,12345,300,2,3000)+packet(a,b,12345,443,400,18,4000,301)
        rows=m.analyze_pcap(pcap)
        mapped={(r['address'],r['port']):{f['value'] for f in r['fingerprints'] if f['kind']=='ja4ts'} for r in rows}
        self.assertIn((b,443),mapped);self.assertIn((a,12345),mapped)
        self.assertEqual(mapped[(b,443)],{'2000_00_00_00'})
        self.assertEqual(mapped[(a,12345)],{'4000_00_00_00'})
