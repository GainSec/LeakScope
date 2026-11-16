"""Bounded read-only BACnet property and explicitly mapped Modbus point reads."""
import json
import math
import socket
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

from .native import Reader, _bacnet as identify_bacnet
from .wire import WireRecorder, RecordedSocket, connect_tcp, unicast_destination

MAX_REQUESTS = 32
MAX_POINTS = 256
PROTOCOL_SOURCES = (
    'https://www.modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf',
    'https://github.com/bacnet-stack/bacnet-stack/blob/master/src/bacnet/rp.c',
    'https://github.com/bacnet-stack/bacnet-stack/blob/master/src/bacnet/bacenum.h',
)


def _integer(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label} must be an integer from {low} to {high}')
    return value


def _number(value, label):
    try: valid=not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)
    except OverflowError: valid=False
    if not valid:
        raise ValueError(label + ' must be finite')
    return value


def _text(value, limit, label):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid ' + label)
    return value


def validate_inspection(service_type, options=None):
    if options is None: options = {}
    if not isinstance(options, dict): raise ValueError('Inspection options must be an object')
    service_type = {'modbus-tcp':'modbus', 'bacnet-ip':'bacnet'}.get(service_type, service_type)
    common = {'timeout_seconds'}
    if service_type == 'modbus': allowed = common | {'unit_id', 'blocks', 'mappings', 'include_device_identity'}
    elif service_type == 'bacnet': allowed = common | {'device_instance', 'objects', 'max_objects'}
    else: raise ValueError('Read-only point inspection supports Modbus TCP and BACnet/IP')
    if set(options) - allowed: raise ValueError('Unsupported inspection option')
    timeout = _number(options.get('timeout_seconds', 10), 'timeout_seconds')
    if not 1 <= timeout <= 20: raise ValueError('Inspection timeout must be from 1 to 20 seconds')
    result = {'timeout_seconds':timeout}
    if service_type == 'bacnet':
        instance = options.get('device_instance')
        if instance is not None: instance = _integer(instance, 0, 4194303, 'device_instance')
        objects = options.get('objects', [])
        if not isinstance(objects, list) or len(objects) > 8: raise ValueError('Select at most eight BACnet objects')
        normalized = []
        for item in objects:
            if not isinstance(item, dict) or set(item) != {'type', 'instance'}: raise ValueError('An object needs type and instance only')
            value = {'type':_integer(item['type'], 0, 1023, 'object type'),
                     'instance':_integer(item['instance'], 0, 4194303, 'object instance')}
            if value not in normalized: normalized.append(value)
        return dict(result, device_instance=instance, objects=normalized,
                    max_objects=_integer(options.get('max_objects',len(normalized) if normalized else 4),0,8,'max_objects'))
    unit = _integer(options.get('unit_id',1),1,255,'unit_id')
    identity = options.get('include_device_identity',True)
    if not isinstance(identity,bool): raise ValueError('include_device_identity must be a boolean')
    blocks = options.get('blocks',[])
    if not isinstance(blocks,list) or len(blocks)>8: raise ValueError('Select at most eight Modbus blocks')
    normalized=[];total=0
    for block in blocks:
        if not isinstance(block,dict) or set(block)!={'function','address','count'}: raise ValueError('A read block needs function, address and count')
        function=_integer(block['function'],1,4,'read function')
        address=_integer(block['address'],0,65535,'zero-based address')
        count=_integer(block['count'],1,125 if function in (3,4) else 256,'read count')
        if address+count>65536: raise ValueError('Modbus block exceeds its address space')
        if any(b['function']==function and address<b['address']+b['count'] and b['address']<address+count for b in normalized):
            raise ValueError('Read blocks for one function must not overlap')
        total+=count
        if total>MAX_POINTS: raise ValueError('Modbus inspection is limited to 256 requested values')
        normalized.append({'function':function,'address':address,'count':count})
    mappings=options.get('mappings',[])
    if not isinstance(mappings,list) or len(mappings)>MAX_POINTS: raise ValueError('Too many point mappings')
    mapped=[];seen=set()
    for item in mappings:
        if not isinstance(item,dict) or set(item)-{'function','address','label','units','datatype','byte_order','word_order','scale','offset'}:
            raise ValueError('Invalid point mapping')
        function=_integer(item.get('function'),1,4,'mapped read function')
        address=_integer(item.get('address'),0,65535,'mapped address')
        datatype=item.get('datatype','bool' if function in (1,2) else 'uint16')
        if datatype not in {'bool','uint16','int16','uint32','int32','float32'}: raise ValueError('Unsupported mapped datatype')
        if (function in (1,2)) != (datatype=='bool'): raise ValueError('Coils/discrete inputs require bool; register mappings require numeric datatypes')
        width=2 if datatype in {'uint32','int32','float32'} else 1
        if not any(b['function']==function and b['address']<=address and address+width<=b['address']+b['count'] for b in normalized):
            raise ValueError('Mapping must fit inside an explicitly selected read block')
        addresses={(function,offset) for offset in range(address,address+width)}
        if seen & addresses: raise ValueError('Point mappings must not overlap')
        seen.update(addresses)
        byte_order=item.get('byte_order','big');word_order=item.get('word_order','big')
        if byte_order not in ('big','little') or word_order not in ('big','little'): raise ValueError('Byte and word orders must be big or little')
        scale=_number(item.get('scale',1),'scale');offset=_number(item.get('offset',0),'offset')
        if datatype=='bool' and (scale!=1 or offset!=0): raise ValueError('Boolean points cannot be scaled')
        mapped.append({'function':function,'address':address,'datatype':datatype,'byte_order':byte_order,
            'word_order':word_order,'label':_text(item.get('label',''),200,'point label'),
            'units':_text(item.get('units',''),80,'point units'),'scale':scale,'offset':offset})
    if not identity and not normalized: raise ValueError('Select device identification or explicit read blocks')
    return dict(result,unit_id=unit,blocks=normalized,mappings=mapped,include_device_identity=identity)


class _RemoteError(ValueError): pass
class _Stop(ValueError): pass


def _modbus(endpoint,options,recorder,metadata,deadline):
    request_count=0
    with connect_tcp(endpoint['address'],endpoint['port'],deadline) as raw:
        recorder.connected=True;sock=RecordedSocket(raw,recorder,deadline);reader=Reader(sock,deadline)
        def exchange(pdu):
            nonlocal request_count
            if request_count>=MAX_REQUESTS or time.monotonic()>=deadline: raise _Stop('Inspection request/time budget exhausted')
            request_count+=1;metadata['requests']=request_count
            sock.sendall(struct.pack('!HHHB',request_count,0,len(pdu)+1,options['unit_id'])+pdu)
            tid,protocol,length,unit=struct.unpack('!HHHB',reader.exact(7))
            if tid!=request_count or protocol or unit!=options['unit_id'] or not 2<=length<=254:
                raise ValueError('Modbus response transaction/unit/header mismatch')
            body=reader.exact(length-1)
            if body[0]==(pdu[0]|0x80) and len(body)==2:
                metadata['responses']+=1
                raise _RemoteError('Modbus exception '+str(body[1]))
            if body[0]!=pdu[0]: raise ValueError('Modbus function mismatch')
            metadata['responses']+=1
            return body
        if options['include_device_identity']:
            try:
                body=exchange(bytes.fromhex('2b0e0100'))
                if len(body)<7 or body[:3]!=b'\x2b\x0e\x01' or body[6]>32: raise ValueError('Invalid Modbus device identification')
                objects=[];position=7
                for _ in range(body[6]):
                    if position+2>len(body): raise ValueError('Truncated Modbus identification object')
                    key,size=body[position:position+2];position+=2
                    if position+size>len(body): raise ValueError('Truncated Modbus identification value')
                    objects.append({'id':key,'value':body[position:position+size].decode('utf-8',errors='replace')});position+=size
                if position!=len(body): raise ValueError('Unexpected Modbus identification bytes')
                metadata['device']={'objects':objects,'more_follows':bool(body[4])}
                if body[4]: metadata['errors'].append('Additional device-identification pages were not requested')
            except _RemoteError as exc: metadata['errors'].append(str(exc))
        metadata['blocks']=[]
        for block in options['blocks']:
            try:
                body=exchange(struct.pack('!BHH',block['function'],block['address'],block['count']))
                count=(block['count']+7)//8 if block['function'] in (1,2) else block['count']*2
                if len(body)!=count+2 or body[1]!=count: raise ValueError('Modbus response byte count does not match selected range')
                raw_values=[bool(body[2+i//8]&(1<<(i%8))) for i in range(block['count'])] if block['function'] in (1,2) else list(struct.unpack('!'+str(block['count'])+'H',body[2:]))
                metadata['blocks'].append(dict(block,raw_values=raw_values))
                index=0
                while index<len(raw_values):
                    address=block['address']+index
                    mapping=next((m for m in options['mappings'] if m['function']==block['function'] and m['address']==address),None)
                    width=2 if mapping and mapping['datatype'] in {'uint32','int32','float32'} else 1
                    values=raw_values[index:index+width]
                    point={'function':block['function'],'address':address,'address_basis':'zero-based wire address',
                           'raw_value':values[0] if width==1 else values,'value':values[0],
                           'datatype':'bool' if block['function'] in (1,2) else 'uint16','label':'','units':'','mapping_source':'unmapped'}
                    if mapping:
                        datatype=mapping['datatype']
                        if datatype=='bool': value=bool(values[0])
                        else:
                            words=[struct.pack('!H',value) for value in values]
                            if mapping['byte_order']=='little': words=[word[::-1] for word in words]
                            if mapping['word_order']=='little': words.reverse()
                            value=struct.unpack('!'+{'uint16':'H','int16':'h','uint32':'I','int32':'i','float32':'f'}[datatype],b''.join(words))[0]
                            value=value*mapping['scale']+mapping['offset']
                            try: finite=math.isfinite(value)
                            except OverflowError: finite=False
                            if not finite: raise ValueError('Mapped point decoded to a non-finite value')
                        point.update(mapping,value=value,mapping_source='user-supplied')
                    metadata['points'].append(point);index+=width
            except _RemoteError as exc: metadata['errors'].append(dict(block,error=str(exc)))


def _context_value(tag,value):
    raw=value.to_bytes(max(1,(value.bit_length()+7)//8),'big')
    return bytes([(tag<<4)|8|len(raw)])+raw


def _tag(data,position=0):
    if position>=len(data): raise ValueError('Truncated BACnet tag')
    byte=data[position];position+=1;number=byte>>4;context=bool(byte&8);length=byte&7
    if number==15: raise ValueError('Extended BACnet tag numbers are unsupported')
    if context and length in (6,7): return number,context,length,b'',position
    if not context and number==1: return number,context,length,b'',position
    if length==5:
        if position>=len(data): raise ValueError('Truncated BACnet extended length')
        length=data[position];position+=1
        if length in (254,255):
            size=2 if length==254 else 4
            if position+size>len(data): raise ValueError('Truncated BACnet extended length')
            length=int.from_bytes(data[position:position+size],'big');position+=size
    if length>8192 or position+length>len(data): raise ValueError('Truncated/oversized BACnet value')
    return number,context,length,data[position:position+length],position+length


def _application(data):
    tag,context,length,raw,end=_tag(data)
    if context or end!=len(data): raise ValueError('Unsupported compound BACnet property value')
    if tag==0 and length==0: value=None
    elif tag==1 and length in (0,1): value=bool(length)
    elif tag in (2,3,9) and 1<=length<=4: value=int.from_bytes(raw,'big',signed=tag==3)
    elif tag in (4,5) and length==(4 if tag==4 else 8):
        value=struct.unpack('!f' if tag==4 else '!d',raw)[0]
        if not math.isfinite(value): raise ValueError('Non-finite BACnet real value')
    elif tag==7 and raw:
        codec={0:'utf-8',3:'utf-32-be',4:'utf-16-be',5:'iso-8859-1'}.get(raw[0])
        value=raw[1:].decode(codec,errors='replace') if codec else {'encoding':raw[0],'hex':raw[1:].hex()}
    elif tag==12 and length==4:
        number=int.from_bytes(raw,'big');value={'type':number>>22,'instance':number&0x3fffff}
    elif tag in (6,8,10,11): value={'application_tag':tag,'hex':raw.hex()}
    else: raise ValueError('Unsupported BACnet application datatype')
    return value,tag


def _readproperty_response(body,invoke,obj,prop,index):
    if len(body)<9 or body[:2] not in (b'\x81\x0a',b'\x81\x0b') or struct.unpack('!H',body[2:4])[0]!=len(body):
        raise ValueError('Invalid BACnet BVLC response')
    if body[4:6]!=b'\x01\0': raise ValueError('Routed BACnet response is unsupported by direct unicast inspection')
    apdu=body[6:]
    if len(apdu)<3: raise ValueError('Truncated BACnet response APDU')
    kind=apdu[0]&0xf0
    if kind==0x30 and apdu[0]&8: raise ValueError('Segmented BACnet response is unsupported; no reassembly was attempted')
    if apdu[1]!=invoke: raise ValueError('BACnet invoke ID mismatch')
    if kind in (0x50,0x60,0x70): raise _RemoteError({0x50:'BACnet Error',0x60:'BACnet Reject',0x70:'BACnet Abort'}[kind]+': '+apdu[2:32].hex())
    if kind!=0x30 or apdu[2]!=12: raise ValueError('BACnet response is not an unsegmented ReadProperty ACK')
    data=apdu[3:];position=0
    for expected,value in ((0,obj),(1,prop)):
        tag,context,length,raw,position=_tag(data,position)
        valid_length=length==4 if expected==0 else 1<=length<=4
        if not context or tag!=expected or not valid_length or int.from_bytes(raw,'big')!=value:
            raise ValueError('BACnet ACK object/property mismatch')
    if index is not None:
        tag,context,length,raw,position=_tag(data,position)
        if not context or tag!=2 or not 1<=length<=4 or int.from_bytes(raw,'big')!=index: raise ValueError('BACnet ACK array index mismatch')
    if data[position:position+1]!=b'\x3e' or data[-1:]!=b'\x3f': raise ValueError('Invalid BACnet property value enclosure')
    return _application(data[position+1:-1])


def _bacnet(endpoint,options,recorder,metadata,deadline):
    instance=options['device_instance'];requests=0
    if instance is None:
        metadata['requests']=requests=1
        identity,_=identify_bacnet(endpoint['address'],endpoint['port'],min(3,max(.05,deadline-time.monotonic())),recorder)
        metadata['responses']+=1
        metadata['device'].update(identity);instance=identity['device_instance']
    metadata['device']['device_instance']=instance
    family,kind,protocol,_,destination=unicast_destination(endpoint['address'],endpoint['port'],deadline)
    if family!=socket.AF_INET: raise ValueError('BACnet inspection supports IPv4 unicast only')
    with socket.socket(family,kind,protocol) as raw:
        raw.connect(destination);wire=RecordedSocket(raw,recorder,deadline)
        def read(obj_type,obj_instance,prop,index=None):
            nonlocal requests
            if requests>=MAX_REQUESTS or time.monotonic()>=deadline: raise _Stop('BACnet request/time budget exhausted')
            requests+=1;metadata['requests']=requests;invoke=requests
            obj=(obj_type<<22)|obj_instance
            apdu=bytes([0,5,invoke,12,0x0c])+struct.pack('!I',obj)+_context_value(1,prop)
            if index is not None: apdu+=_context_value(2,index)
            payload=b'\x01\x04'+apdu
            wire.send(b'\x81\x0a'+struct.pack('!H',len(payload)+4)+payload)
            raw.settimeout(min(2,max(.001,deadline-time.monotonic())))
            # RecordedSocket's total-deadline setter is replaced for this read
            # only so one unresponsive property cannot consume the whole job.
            wire.deadline=min(deadline,time.monotonic()+2)
            try: response=wire.recv(8193)
            finally: wire.deadline=deadline
            decoded=_readproperty_response(response,invoke,obj,prop,index)
            metadata['responses']+=1
            return decoded
        def value(obj_type,obj_instance,prop,index=None):
            try:return read(obj_type,obj_instance,prop,index)[0]
            except _Stop:raise
            except (ValueError,OSError) as exc:
                metadata['errors'].append({'object_type':obj_type,'object_instance':obj_instance,'property':prop,'array_index':index,'error':str(exc)[:500]})
                return None
        for prop,name in ((77,'object_name'),(121,'vendor_name'),(120,'vendor_identifier'),(70,'model_name'),(44,'firmware_revision'),(28,'description'),(12,'application_software_version')):
            item=value(8,instance,prop)
            if item is not None:metadata['device'][name]=item
        objects=list(options['objects'])
        if not objects and options['max_objects']:
            count=value(8,instance,76,0)
            if isinstance(count,int) and not isinstance(count,bool) and count>=0:
                metadata['device']['object_count']=count
                for index in range(1,min(count,options['max_objects'])+1):
                    obj=value(8,instance,76,index)
                    if isinstance(obj,dict) and set(obj)=={'type','instance'} and obj['type']!=8:objects.append(obj)
                    elif obj is not None and not (isinstance(obj,dict) and obj.get('type')==8):metadata['errors'].append('Object-list element was not an object identifier')
                if count>options['max_objects']:metadata['errors'].append('Object list was limited to the selected subset')
            elif count is not None:metadata['errors'].append('Object-list length was not unsigned')
        for obj in objects[:options['max_objects']]:
            point={'object_type':obj['type'],'object_instance':obj['instance'],'object_identifier':str(obj['type'])+':'+str(obj['instance']),
                   'type':obj['type'],'instance':obj['instance'],'label':'','name':'','value':None,'raw_value':None,'units':'','units_code':None,'mapping_source':'device-property'}
            metadata['points'].append(point)
            name=value(obj['type'],obj['instance'],77)
            if isinstance(name,str):point['label']=point['name']=name
            actual=value(obj['type'],obj['instance'],85)
            point['value']=point['raw_value']=actual
            units=value(obj['type'],obj['instance'],117)
            if isinstance(units,int) and not isinstance(units,bool):
                point['units_code']=units;point['units']={62:'degrees Celsius',64:'degrees Fahrenheit',95:'no units',98:'percent'}.get(units,'')


def inspect_endpoint(endpoint,options=None,config=None):
    from .capture import _endpoint, _enforce_blacklist, _persist, _wire_artifacts
    address,port,scheme,service,transport=_endpoint(endpoint)
    service={'modbus-tcp':'modbus','bacnet-ip':'bacnet'}.get(service,service)
    options=validate_inspection(service,options)
    if (service=='bacnet' and transport!='udp') or (service=='modbus' and transport!='tcp'):
        raise ValueError('Inspection transport does not match the selected protocol')
    config=config or {};_enforce_blacklist(address,port,transport,config)
    root=Path(config.get('CAPTURE_DIR') or '/data/artifacts/captures').expanduser().resolve();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    recorder=WireRecorder(transport);metadata={'protocol':service,'operation':'ReadProperty only' if service=='bacnet' else 'Read device identification and explicit functions 1–4 only',
        'points':[],'device':{},'errors':[],'requests':0,'responses':0,'read_only':True,'observed_at':datetime.now(timezone.utc).isoformat(),
        'limits':{'max_requests':MAX_REQUESTS,'max_points':MAX_POINTS,'timeout_seconds':options['timeout_seconds']},'protocol_sources':list(PROTOCOL_SOURCES)}
    deadline=time.monotonic()+options['timeout_seconds']
    try:
        ( _bacnet if service=='bacnet' else _modbus)(dict(endpoint,address=address,port=port),options,recorder,metadata,deadline)
    except (ValueError,OSError) as exc:metadata['errors'].append(str(exc)[:500])
    metadata['partial']=bool(metadata['errors']);recorder.termination='inspection_partial' if metadata['partial'] else 'read_only_inspection_complete';metadata['wire_summary']=recorder.summary()
    artifacts=[]
    try:
        artifacts.append(_persist(root,json.dumps(metadata,ensure_ascii=False,allow_nan=False).encode(),'application/json','ot-inspection','native',{'service_type':service},'.json'))
        if recorder.sent or recorder.received:artifacts.extend(_wire_artifacts(root,recorder,{'service_type':service,'operation':'read-only inspection'}))
    except Exception:
        for item in artifacts:Path(item['path']).unlink(missing_ok=True)
        raise
    return {'metadata':metadata,'artifacts':artifacts,'partial':metadata['partial']}
