import json
import socketserver
import struct
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from django.test import SimpleTestCase

from leakscope.phase3.ot_inspection import validate_inspection, inspect_endpoint


@contextmanager
def peer(dialogue,udp=False):
    errors=[]
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:dialogue(self.request,self.client_address)
            except Exception as exc:errors.append(exc)
    cls=socketserver.ThreadingUDPServer if udp else socketserver.ThreadingTCPServer
    with cls(('127.0.0.1',0),Handler) as server:
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield server.server_address[1]
        finally:server.shutdown();thread.join(2)
    if errors:raise errors[0]


def exact(sock,n):
    data=b''
    while len(data)<n:
        part=sock.recv(n-len(data))
        if not part:raise AssertionError('Unexpected EOF')
        data+=part
    return data


class ValidationTests(SimpleTestCase):
    def test_default_has_no_modbus_register_ranges(self):
        spec=validate_inspection('modbus')
        self.assertEqual(spec['blocks'],[])
        self.assertTrue(spec['include_device_identity'])

    def test_write_functions_bad_units_and_unbounded_ranges_are_rejected(self):
        for options in [{'blocks':[{'function':6,'address':0,'count':1}]},{'unit_id':0},
                        {'blocks':[{'function':3,'address':65535,'count':2}]},
                        {'blocks':[{'function':3,'address':0,'count':126}]},
                        {'blocks':[{'function':1,'address':0,'count':257}]},
                        {'timeout_seconds':21},{'unexpected':'value'}]:
            with self.assertRaises(ValueError):validate_inspection('modbus',options)
        with self.assertRaises(ValueError):validate_inspection('modbus',{'timeout_seconds':10**1000})

    def test_mapping_must_fit_explicit_read_and_be_finite(self):
        base={'blocks':[{'function':3,'address':10,'count':1}]}
        for item in [{'function':3,'address':10,'datatype':'float32'},
                     {'function':3,'address':11,'datatype':'uint16'},
                     {'function':3,'address':10,'scale':float('inf')}]:
            with self.assertRaises(ValueError):validate_inspection('modbus',dict(base,mappings=[item]))

    def test_overlapping_blocks_and_mappings_are_rejected(self):
        with self.assertRaises(ValueError):validate_inspection('modbus',{'blocks':[
            {'function':3,'address':0,'count':2},{'function':3,'address':1,'count':1}]})
        with self.assertRaises(ValueError):validate_inspection('modbus',{'blocks':[{'function':3,'address':0,'count':3}],
            'mappings':[{'function':3,'address':0,'datatype':'float32'},{'function':3,'address':1,'datatype':'uint16'}]})

    def test_bacnet_options_are_bounded_and_do_not_offer_writes(self):
        self.assertEqual(validate_inspection('bacnet')['max_objects'],4)
        for item in [{'objects':[{'type':8,'instance':4194304}]},{'max_objects':9},
                     {'objects':[{'type':0,'instance':1,'value':0}]},{'write':True}]:
            with self.assertRaises(ValueError):validate_inspection('bacnet',item)


class ModbusInspectionTests(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config={'CAPTURE_DIR':self.tmp.name}

    def endpoint(self,port):return {'address':'127.0.0.1','port':port,'transport':'tcp','service_type':'modbus'}

    def test_explicit_read_only_ranges_decode_mapping_and_preserve_wire(self):
        def dialogue(sock,address):
            header=exact(sock,7);tid,proto,length,unit=struct.unpack('!HHHB',header)
            self.assertEqual((proto,unit),(0,7));self.assertEqual(exact(sock,length-1),bytes.fromhex('03000a0002'))
            body=b'\x03\x04'+struct.pack('!f',12.5)
            sock.sendall(struct.pack('!HHHB',tid,0,len(body)+1,unit)+body)
        options={'unit_id':7,'include_device_identity':False,'blocks':[{'function':3,'address':10,'count':2}],
                 'mappings':[{'function':3,'address':10,'datatype':'float32','label':'Tank level','units':'m','scale':2,'offset':1}]}
        with peer(dialogue) as port:result=inspect_endpoint(self.endpoint(port),options,self.config)
        point=result['metadata']['points'][0]
        self.assertEqual(point['value'],26.0);self.assertEqual(point['label'],'Tank level')
        self.assertEqual(point['units'],'m');self.assertFalse(result['partial'])
        wire=json.loads(Path(next(x['path'] for x in result['artifacts'] if x['kind']=='wire-transcript')).read_text())
        self.assertIn('03000a0002',wire['sent']['hex'])
        self.assertTrue(all(Path(a['path']).is_file() for a in result['artifacts']))

    def test_coils_and_exception_are_reported_without_control_requests(self):
        def dialogue(sock,address):
            for index in range(2):
                tid,proto,length,unit=struct.unpack('!HHHB',exact(sock,7));request=exact(sock,length-1)
                self.assertIn(request[0],(1,4))
                body=b'\x01\x01\x05' if request[0]==1 else b'\x84\x02'
                sock.sendall(struct.pack('!HHHB',tid,0,len(body)+1,unit)+body)
        options={'include_device_identity':False,'blocks':[{'function':1,'address':0,'count':3},{'function':4,'address':10,'count':1}]}
        with peer(dialogue) as port:result=inspect_endpoint(self.endpoint(port),options,self.config)
        self.assertEqual([p['value'] for p in result['metadata']['points']],[True,False,True])
        self.assertTrue(result['partial']);self.assertIn('exception 2',str(result['metadata']['errors']))

    def test_transaction_mismatch_is_not_accepted_as_point_data(self):
        def dialogue(sock,address):
            tid,proto,length,unit=struct.unpack('!HHHB',exact(sock,7));exact(sock,length-1)
            sock.sendall(struct.pack('!HHHB',tid+1,0,5,unit)+b'\x03\x02\x00\x01')
        with peer(dialogue) as port:
            result=inspect_endpoint(self.endpoint(port),{'include_device_identity':False,'blocks':[{'function':3,'address':0,'count':1}]},self.config)
        self.assertTrue(result['partial']);self.assertEqual(result['metadata']['points'],[])

    def test_endianness_and_signed_words_are_explicit_user_mappings(self):
        def dialogue(sock,address):
            tid,proto,length,unit=struct.unpack('!HHHB',exact(sock,7));exact(sock,length-1)
            body=b'\x04\x08'+bytes.fromhex('ffff00003f800000')
            sock.sendall(struct.pack('!HHHB',tid,0,len(body)+1,unit)+body)
        opts={'include_device_identity':False,'blocks':[{'function':4,'address':0,'count':4}],
              'mappings':[{'function':4,'address':0,'datatype':'int16'},
                          {'function':4,'address':1,'datatype':'float32','word_order':'little'}]}
        with peer(dialogue) as port:result=inspect_endpoint(self.endpoint(port),opts,self.config)
        self.assertEqual([point['value'] for point in result['metadata']['points']],[-1,1.0,0])

    def test_device_identity_uses_selected_unit_without_register_reads_by_default(self):
        def dialogue(sock,address):
            tid,proto,length,unit=struct.unpack('!HHHB',exact(sock,7));self.assertEqual(unit,255)
            self.assertEqual(exact(sock,length-1),bytes.fromhex('2b0e0100'))
            body=bytes([43,14,1,1,0,0,1,0,4])+b'Acme'
            sock.sendall(struct.pack('!HHHB',tid,0,len(body)+1,unit)+body)
        with peer(dialogue) as port:result=inspect_endpoint(self.endpoint(port),{'unit_id':255},self.config)
        self.assertEqual(result['metadata']['device']['objects'][0]['value'],'Acme')
        self.assertEqual(result['metadata']['requests'],1);self.assertEqual(result['metadata']['points'],[])

    def test_nonfinite_mapped_value_is_partial_with_wire_artifacts(self):
        def dialogue(sock,address):
            tid,proto,length,unit=struct.unpack('!HHHB',exact(sock,7));exact(sock,length-1)
            body=b'\x03\x02\xff\xff';sock.sendall(struct.pack('!HHHB',tid,0,len(body)+1,unit)+body)
        opts={'include_device_identity':False,'blocks':[{'function':3,'address':0,'count':1}],
              'mappings':[{'function':3,'address':0,'datatype':'uint16','scale':10**308}]}
        with peer(dialogue) as port:result=inspect_endpoint(self.endpoint(port),opts,self.config)
        self.assertTrue(result['partial']);self.assertIn('non-finite',str(result['metadata']['errors']))
        self.assertTrue(any(a['kind']=='wire-transcript' for a in result['artifacts']))


def ctx(tag,value):
    raw=value.to_bytes(max(1,(value.bit_length()+7)//8),'big')
    return bytes([(tag<<4)|8|len(raw)])+raw


def app_string(value):
    data=b'\0'+value.encode()
    return (bytes([0x70|len(data)]) if len(data)<=4 else b'\x75'+bytes([len(data)]))+data


class BacnetInspectionTests(SimpleTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)

    def test_readproperty_decodes_object_name_present_value_and_units(self):
        requests=[]
        def dialogue(request,address):
            data,sock=request;requests.append(data)
            self.assertEqual(data[:2],b'\x81\x0a');self.assertEqual(data[4:6],b'\x01\x04')
            self.assertEqual(data[9],12)
            invoke=data[8];obj=struct.unpack('!I',data[11:15])[0];prop=data[16]
            if obj>>22==8:value=app_string('Fixture device') if prop in (77,121,70,44,28,12) else b'\x21\x7b'
            elif prop==77:value=app_string('Supply temperature')
            elif prop==85:value=b'\x44'+struct.pack('!f',21.5)
            else:value=b'\x91\x3e'
            apdu=bytes([0x30,invoke,12])+b'\x0c'+struct.pack('!I',obj)+ctx(1,prop)+b'\x3e'+value+b'\x3f'
            payload=b'\x01\0'+apdu
            sock.sendto(b'\x81\x0a'+struct.pack('!H',len(payload)+4)+payload,address)
        options={'device_instance':42,'objects':[{'type':0,'instance':1}]}
        with peer(dialogue,udp=True) as port:
            result=inspect_endpoint({'address':'127.0.0.1','port':port,'service_type':'bacnet','transport':'udp'},options,{'CAPTURE_DIR':self.tmp.name})
        point=result['metadata']['points'][0]
        self.assertEqual(point['value'],21.5);self.assertEqual(point['label'],'Supply temperature')
        self.assertEqual(point['units_code'],62);self.assertFalse(result['partial'])
        self.assertLessEqual(len(requests),32)

    def test_segmented_ack_is_explicit_partial_not_fabricated_data(self):
        def dialogue(request,address):
            data,sock=request;payload=b'\x01\0'+bytes([0x38,data[8],0,0,12])
            sock.sendto(b'\x81\x0a'+struct.pack('!H',len(payload)+4)+payload,address)
        with peer(dialogue,udp=True) as port:
            result=inspect_endpoint({'address':'127.0.0.1','port':port,'service_type':'bacnet','transport':'udp'},
                {'device_instance':42,'max_objects':0},{'CAPTURE_DIR':self.tmp.name})
        self.assertTrue(result['partial']);self.assertIn('segmented',str(result['metadata']['errors']).lower())

    def object_server(self,count=3,mismatch=False,request_log=None):
        def dialogue(request,address):
            data,sock=request
            if request_log is not None:request_log.append(data)
            if data==bytes.fromhex('810a000801001008'):
                payload=bytes.fromhex('01001000c4')+struct.pack('!I',(8<<22)|42)+bytes.fromhex('2205c49103217b')
                sock.sendto(b'\x81\x0a'+struct.pack('!H',len(payload)+4)+payload,address);return
            invoke=data[8];obj=struct.unpack('!I',data[11:15])[0];prop=data[16]
            index=data[18] if len(data)>17 else None
            if prop==76:
                value=bytes([0x21,count]) if index==0 else b'\xc4'+struct.pack('!I',index)
            elif obj>>22==8:value=app_string('Fixture') if prop!=120 else b'\x21\x7b'
            elif prop==77:value=app_string('Point '+str(obj&0x3fffff))
            elif prop==85:value=b'\x44'+struct.pack('!f',5.0)
            else:value=b'\x91\x3e'
            apdu=bytes([0x30,invoke,12])+b'\x0c'+struct.pack('!I',obj+(1 if mismatch else 0))+ctx(1,prop)
            if index is not None:apdu+=ctx(2,index)
            apdu+=b'\x3e'+value+b'\x3f';payload=b'\x01\0'+apdu
            sock.sendto(b'\x81\x0a'+struct.pack('!H',len(payload)+4)+payload,address)
        return dialogue

    def test_object_list_reads_only_bounded_array_elements(self):
        requests=[]
        with peer(self.object_server(count=5,request_log=requests),udp=True) as port:
            result=inspect_endpoint({'address':'127.0.0.1','port':port,'service_type':'bacnet','transport':'udp'},
                {'device_instance':42,'max_objects':2},{'CAPTURE_DIR':self.tmp.name})
        self.assertEqual([p['object_instance'] for p in result['metadata']['points']],[1,2])
        self.assertEqual(result['metadata']['device']['object_count'],5)
        self.assertTrue(result['partial']);self.assertEqual(len(requests),16)
        self.assertTrue(all(data[9]==12 for data in requests))

    def test_total_request_budget_retains_partial_point_data(self):
        requests=[]
        with peer(self.object_server(count=8,request_log=requests),udp=True) as port:
            result=inspect_endpoint({'address':'127.0.0.1','port':port,'service_type':'bacnet','transport':'udp'},
                {'device_instance':42,'max_objects':8},{'CAPTURE_DIR':self.tmp.name})
        self.assertEqual(len(requests),32);self.assertTrue(result['partial'])
        self.assertEqual(result['metadata']['points'][0]['value'],5.0)
        self.assertIn('budget',str(result['metadata']['errors']))

    def test_unicast_identity_precedes_reads_when_device_instance_is_unknown(self):
        requests=[]
        with peer(self.object_server(request_log=requests),udp=True) as port:
            result=inspect_endpoint({'address':'127.0.0.1','port':port,'service_type':'bacnet','transport':'udp'},
                {'max_objects':0},{'CAPTURE_DIR':self.tmp.name})
        self.assertEqual(requests[0],bytes.fromhex('810a000801001008'))
        self.assertEqual(result['metadata']['device']['device_instance'],42)
        self.assertEqual(result['metadata']['requests'],8)
        self.assertFalse(result['partial'])

    def test_mismatched_object_ack_cannot_supply_point_values(self):
        with peer(self.object_server(mismatch=True),udp=True) as port:
            result=inspect_endpoint({'address':'127.0.0.1','port':port,'service_type':'bacnet','transport':'udp'},
                {'device_instance':42,'objects':[{'type':0,'instance':1}]},{'CAPTURE_DIR':self.tmp.name})
        self.assertTrue(result['partial']);self.assertIsNone(result['metadata']['points'][0]['value'])
        self.assertIn('object/property mismatch',str(result['metadata']['errors']))
