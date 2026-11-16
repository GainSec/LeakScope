"""Bounded database transport identification, stopping before authentication.

Adapters share native.Reader's exact/until interface, and the caller supplies
the recorded socket and deadline. Protocol identity is deliberately narrower
than a requested database product. Primary references are in CAPABILITIES.
"""
import re
import ssl
import struct


def _oracle(sock, read, address, port):
    # Nmap's original listener VERSION probe contains no CID, user or service.
    # TNS has no transaction-id field; only this command's bounded, structured
    # REFUSE descriptor is accepted. Never retry or follow a REDIRECT/RESEND.
    prefix = bytes.fromhex('005a0000010000000136012c000008007fff7f08000000010020003a0000000000000000000000000000000034e6000000010000000000000000')
    sock.sendall(prefix+b'(CONNECT_DATA=(COMMAND=version))')
    size, checksum, kind, reserved, header_checksum = struct.unpack('!HHBBH', read.exact(8))
    if kind != 4 or checksum or reserved or header_checksum or not 13 <= size <= 16384:
        raise ValueError('Invalid bounded Oracle TNS listener response header')
    body = read.exact(size-8)
    length = struct.unpack('!H', body[2:4])[0]
    if length != len(body)-4:
        raise ValueError('Oracle TNS REFUSE descriptor length mismatch')
    try:
        descriptor = body[4:].removesuffix(b'\0').decode('ascii')
    except UnicodeDecodeError:
        raise ValueError('Invalid Oracle TNS listener descriptor encoding') from None
    if not descriptor.startswith('(DESCRIPTION=') or not descriptor.endswith(')'):
        raise ValueError('Oracle listener returned no DESCRIPTION descriptor')
    depth = 0
    for position, char in enumerate(descriptor):
        if not 32 <= ord(char) <= 126:
            raise ValueError('Invalid Oracle listener descriptor text')
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        if not 0 <= depth <= 32 or (depth == 0 and position != len(descriptor)-1):
            raise ValueError('Invalid Oracle listener descriptor nesting')
    if depth:
        raise ValueError('Unbalanced Oracle listener descriptor')
    versions = re.findall(r'\(VSNNUM=([0-9]{1,10})\)', descriptor)
    errors = re.findall(r'\(ERR=([0-9]{1,5})\)', descriptor)
    if len(versions) > 1 or len(errors) != 1:
        raise ValueError('Oracle listener response has ambiguous status metadata')
    error = int(errors[0])
    metadata = {'operation': 'TNS listener VERSION only; no service selection, login or database session',
                'listener_error': error, 'version_command_succeeded': error == 0,
                'database_verified': False}
    if versions:
        version = int(versions[0])
        if not 0 < version <= 0xffffffff:
            raise ValueError('Invalid Oracle listener VSNNUM')
        metadata['listener_version'] = '.'.join(str(value) for value in
            (version >> 24, (version >> 20) & 15, (version >> 12) & 255, (version >> 8) & 15, version & 255))
    elif error not in (1189, 1194, 12504, 12505, 12514):
        raise ValueError('Oracle response has no listener version or recognized listener error')
    return metadata, 'oracle'


def _maxdb(sock, read, address, port):
    # SAP ven42.c en42pingRequest -> ven96.c RSQL_PING_REPLY. This is
    # standalone X-server PING, not the DBM control connection in maxdb-info.
    body = b'\0\x02'+struct.pack('<HBBBBIIII', 40, 0, 11, 0, 0, 16384, 0, 0, 0)+b' '*16
    header = struct.pack('<IBBBBIIHBBI', 64, 3, 53, 0, 0, 0, 0, 0, 4, 0, 64)
    sock.sendall(header+body)
    header = read.exact(24)
    if header[18] not in (3, 4):
        raise ValueError('Unsupported MaxDB X-server byte order')
    endian = '<' if header[18] == 4 else '>'
    size, protocol, kind, flags, residual, _, receiver, status, _, filler, total = struct.unpack(endian+'IBBBBIIHBBI', header)
    if (protocol, kind, flags, residual, receiver, status, filler) != (3, 54, 0, 0, 0, 0, 0) or not 64 <= size <= 16384 or total != size:
        raise ValueError('Invalid bounded MaxDB X-server PING response')
    body = read.exact(size-24)
    if body[:2] != (b'\0\x02' if endian == '<' else b'\0\x01'):
        raise ValueError('Inconsistent MaxDB X-server character code or byte order')
    length, service, _, filler1, filler2 = struct.unpack(endian+'HBBBB', body[2:8])
    if length != len(body) or service or filler1 or filler2:
        raise ValueError('Invalid MaxDB X-server PING connect packet')
    position = 40
    version = None
    fields = 0
    while position < len(body):
        if position+2 > len(body):
            raise ValueError('Truncated MaxDB X-server PING option')
        length, tag = body[position:position+2]
        fields += 1
        if length < 2 or position+length > len(body) or fields > 32:
            raise ValueError('Invalid MaxDB X-server PING option length')
        value = body[position+2:position+length]
        position += length
        if tag == 0x56:
            if version is not None or not value.endswith(b'\0'):
                raise ValueError('Invalid MaxDB X-server version option')
            try:
                version = value[:-1].decode('ascii')
            except UnicodeDecodeError:
                raise ValueError('Invalid MaxDB X-server version encoding') from None
            if not version or any(not 32 <= ord(char) <= 126 for char in version) or not re.search(r'\d+\.\d+', version):
                raise ValueError('Invalid MaxDB X-server version text')
    if version is None:
        raise ValueError('MaxDB X-server PING returned no version option')
    return {'operation': 'X-server PING/version only; empty database names; no DBM connection or authentication',
            'version': version, 'database_verified': False}, 'maxdb'


def _mssql(sock, read, address, port):
    # MS-TDS 2.2.6.5: VERSION first, ENCRYPTION, TERMINATOR; no LOGIN7.
    options = bytes.fromhex('00000b00060100110001ff04000000000000')
    sock.sendall(struct.pack('!BBHHBB', 0x12, 1, len(options)+8, 0, 1, 0)+options)
    kind, status, size, _, packet, window = struct.unpack('!BBHHBB', read.exact(8))
    if kind != 4 or status != 1 or packet != 1 or window or not 14 <= size <= 16384:
        raise ValueError('Invalid bounded TDS PRELOGIN response header')
    body = read.exact(size-8)
    position = 0
    entries = {}
    while position < len(body):
        token = body[position]
        position += 1
        if token == 0xff:
            break
        if position+4 > len(body) or token in entries or len(entries) >= 16:
            raise ValueError('Invalid TDS PRELOGIN option table')
        if not entries and token != 0:
            raise ValueError('TDS PRELOGIN VERSION must be first')
        offset, length = struct.unpack('!HH', body[position:position+4])
        entries[token] = (offset, length)
        position += 4
    else:
        raise ValueError('TDS PRELOGIN option terminator is missing')
    intervals = []
    for offset, length in entries.values():
        if offset < position or offset+length > len(body):
            raise ValueError('TDS PRELOGIN option exceeds response bounds')
        if length:
            if any(offset < end and start < offset+length for start, end in intervals):
                raise ValueError('TDS PRELOGIN options overlap')
            intervals.append((offset, offset+length))
    if 0 not in entries or 1 not in entries or entries[0][1] != 6 or entries[1][1] != 1:
        raise ValueError('TDS PRELOGIN requires VERSION and ENCRYPTION')
    offset = entries[0][0]
    major, minor, build, subbuild = struct.unpack('!BBHH', body[offset:offset+6])
    encryption = body[entries[1][0]]
    if encryption not in (0, 1, 2, 3):
        raise ValueError('Invalid TDS PRELOGIN encryption mode')
    return {'operation': 'TDS PRELOGIN only; no TLS continuation or LOGIN7',
            'version': f'{major}.{minor}.{build}.{subbuild}', 'encryption': encryption,
            'product_verified': False}, 'mssql'


def _cql(sock, read, address, port):
    # CQL v4 OPTIONS is expressly permitted before STARTUP.
    sock.sendall(struct.pack('!BBhBI', 4, 0, 1, 5, 0))
    version, flags, stream, opcode, size = struct.unpack('!BBhBI', read.exact(9))
    if (version, flags, stream, opcode) != (0x84, 0, 1, 6) or not 2 <= size <= 16384:
        raise ValueError('Invalid bounded CQL SUPPORTED frame')
    body = read.exact(size)
    position = 0
    def short():
        nonlocal position
        if position+2 > len(body):
            raise ValueError('Truncated CQL SUPPORTED count')
        value = struct.unpack('!H', body[position:position+2])[0]
        position += 2
        return value
    def string():
        nonlocal position
        length = short()
        if length > 1024 or position+length > len(body):
            raise ValueError('Invalid CQL SUPPORTED string length')
        try:
            value = body[position:position+length].decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('Invalid UTF-8 in CQL SUPPORTED') from None
        position += length
        return value
    count = short()
    if not 1 <= count <= 32:
        raise ValueError('Invalid CQL SUPPORTED map count')
    supported = {}
    for _ in range(count):
        key = string()
        length = short()
        if not key or key in supported or length > 32:
            raise ValueError('Invalid CQL SUPPORTED map entry')
        supported[key] = [string() for _ in range(length)]
    if position != len(body) or not supported.get('CQL_VERSION') or not all(supported['CQL_VERSION']):
        raise ValueError('CQL SUPPORTED has no valid CQL_VERSION')
    return {'operation': 'CQL v4 OPTIONS/SUPPORTED only; no STARTUP, authentication or queries',
            'protocol_version': 4, 'supported': supported, 'product_verified': False}, 'cassandra'


def _aerospike(sock, read, address, port):
    payload = b'build\nversion\n'
    sock.sendall(b'\x02\x01'+len(payload).to_bytes(6, 'big')+payload)
    header = read.exact(8)
    size = int.from_bytes(header[2:], 'big')
    if header[:2] != b'\x02\x01' or not 1 <= size <= 16384:
        raise ValueError('Invalid bounded Aerospike info response')
    body = read.exact(size)
    if not body.endswith(b'\n'):
        raise ValueError('Aerospike info response has no line terminator')
    try:
        lines = body.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        raise ValueError('Invalid UTF-8 in Aerospike identity') from None
    values = {}
    for line in lines:
        parts = line.split('\t')
        if len(parts) != 2 or parts[0] not in ('build', 'version') or parts[0] in values:
            raise ValueError('Unexpected Aerospike info response field')
        key, value = parts
        if not value or len(value) > 1024 or value.upper().startswith(('ERROR', 'FAIL')):
            raise ValueError('Aerospike identity metadata was unavailable')
        values[key] = value
    if not (re.fullmatch(r'\d+(?:\.\d+){2,4}(?:[-._A-Za-z0-9]*)', values.get('build', ''))
            or 'aerospike' in values.get('version', '').lower()):
        raise ValueError('Aerospike returned no build or version identity')
    return {'operation': 'Info build/version only; no records, namespaces or configuration',
            **values}, 'aerospike'


def _hana(sock, read, address, port):
    # SAP go-hdb initRequest/initReply; only the fixed protocol prolog.
    sock.sendall(bytes.fromhex('ffffffff04140004010000010101'))
    product_major, product_minor, major, minor, reserved = struct.unpack('<bhbh2s', read.exact(8))
    if not 1 <= product_major <= 127 or product_minor < 0 or major != 4 or not 0 <= minor <= 1 or reserved != b'\0\0':
        raise ValueError('Invalid SAP HANA initialization reply')
    return {'operation': 'Protocol initialization only; no authentication, session or SQL',
            'product_version': f'{product_major}.{product_minor}',
            'protocol_version': f'{major}.{minor}', 'product_release_verified': False}, 'hana'


def _monetdb(sock, read, address, port):
    pieces = []
    total = 0
    for _ in range(4):
        header = struct.unpack('<H', read.exact(2))[0]
        size = header >> 1
        total += size
        if size > 8190 or total > 16384:
            raise ValueError('MonetDB greeting exceeds its metadata limit')
        pieces.append(read.exact(size))
        if header & 1:
            break
    else:
        raise ValueError('MonetDB greeting exceeds its block limit')
    try:
        fields = b''.join(pieces).decode('ascii').split(':')
    except UnicodeDecodeError:
        raise ValueError('Invalid MonetDB greeting encoding') from None
    if (len(fields) < 7 or not fields[0] or len(fields[0]) > 1024
            or fields[1] not in ('mserver', 'merovingian') or fields[2] != '9'
            or fields[4] not in ('LIT', 'BIG') or not fields[3] or not fields[5]
            or not b''.join(pieces).endswith(b':')):
        raise ValueError('Invalid MonetDB MAPI v9 server challenge')
    if any(not re.fullmatch(r'[A-Za-z0-9_,=.-]{1,1024}', value) for value in (fields[3], fields[5])):
        raise ValueError('Invalid MonetDB advertised authentication algorithms')
    return {'operation': 'Server MAPI greeting only; no challenge response, login or database selection',
            'endpoint_kind': fields[1], 'protocol_version': 9,
            'byte_order': fields[4], 'advertised_algorithms': fields[3].split(',')[:32],
            'password_hash_algorithm': fields[5]}, 'monetdb'


def _ceph(sock, read, address, port):
    prefix = read.exact(8)
    if prefix == b'ceph v02' and read.exact(1) == b'7':
        return {'operation': 'Receive-only Ceph messenger v1 banner; no authentication or cluster access',
                'messenger_version': 1, 'wire_revision': '027', 'monitor_role_verified': False}, 'ceph'
    if prefix != b'ceph v2\n':
        raise ValueError('Invalid Ceph messenger greeting')
    size = struct.unpack('<H', read.exact(2))[0]
    if size != 16:
        raise ValueError('Unsupported bounded Ceph messenger v2 banner size')
    supported, required = struct.unpack('<QQ', read.exact(size))
    if required & ~supported:
        raise ValueError('Ceph banner requires features it does not support')
    return {'operation': 'Receive-only Ceph messenger v2 banner; no authentication or cluster access',
            'messenger_version': 2, 'supported_features': supported,
            'required_features': required, 'monitor_role_verified': False}, 'ceph'


def _ddm(code, payload):
    return struct.pack('!HH', len(payload)+4, code)+payload


def _das_records(data):
    """Known DAS typed records: length, header length, type; network order."""
    position = 0
    records = []
    while position < len(data):
        if position+12 > len(data) or len(records) >= 64:
            raise ValueError('Invalid bounded Db2 DAS record header')
        length, header_length, tag = struct.unpack('!III', data[position:position+12])
        if header_length != 12 or length < 12 or position+length > len(data):
            raise ValueError('Invalid Db2 DAS typed record length')
        records.append((tag, data[position+12:position+length]))
        position += length
    return records


def _db2_das(sock, read, address, port):
    # Official Nmap ibm-db2-das version probe, not db2-das-info.nse's
    # discovery/profile-export RPC sequence. One 41-byte envelope plus a
    # 12-byte empty typed record; no credentials, names or callable routines.
    request = bytes.fromhex('00000000444232444153202020202020010400000010397a000100000000000000000000010c0000000000000c0000000c00000004')
    sock.sendall(request)
    header = read.exact(41)
    if header[:36] != request[:36] or header[36] != 0x24:
        raise ValueError('Unsupported Db2 DAS ping reply envelope or routing bytes')
    size = struct.unpack('<I', header[37:41])[0]
    if not 12 <= size <= 16384:
        raise ValueError('Db2 DAS ping response exceeds its metadata limit')
    records = _das_records(read.exact(size))
    if not 1 <= len(records) <= 2 or records[0][0] != 0x5a:
        raise ValueError('Db2 DAS did not return the known ping/version record')
    fields = _das_records(records[0][1])
    if (len(fields) != 6 or fields[:3] != [(0x4c, b'\0'*4), (0x4f, b'\0'*24), (0x4c, b'\0'*4)]
            or any(tag != 4 for tag, _ in fields[3:])):
        raise ValueError('Db2 DAS ping status or version record layout is unsupported')
    strings = []
    for _, value in fields[3:]:
        if len(value) < 5 or value[:4] != struct.pack('!I', 1208) or not value.endswith(b'\0') or b'\0' in value[4:-1]:
            raise ValueError('Invalid Db2 DAS ping version string')
        strings.append(value[4:-1])
    # The known ping layout has only a product id and two blank strings.
    # Never interpret arbitrary text as a profile or accept discovery results.
    if any(value.strip(b' ') for value in strings[1:]):
        raise ValueError('Db2 DAS ping returned unexpected profile text')
    match = re.fullmatch(rb'SQL([0-9]{2})([0-9]{2})([0-9]{1,4})', strings[0])
    if not match:
        raise ValueError('Db2 DAS ping has no recognized product identifier')
    if len(records) == 2:
        if records[1][0] != 8:
            raise ValueError('Unexpected Db2 DAS trailing record')
        capabilities = _das_records(records[1][1])
        if len(capabilities) > 32 or any(tag != 0x48 or len(value) != 4 for tag, value in capabilities):
            raise ValueError('Invalid Db2 DAS ping capability records')
    return {'operation': 'Single Db2 DAS ping/version only; no authentication, profile export, instance/database enumeration or RPC routines',
            'product_id': strings[0].decode('ascii'),
            'version': '.'.join(str(int(value)) for value in match.groups()),
            'database_verified': False}, 'db2-das'


def _drda(sock, read, address, port):
    # Initial DDM strings use EBCDIC CCSID 37, before Unicode negotiation.
    fields = b''.join(_ddm(code, value.encode('cp037')) for code, value in
                      ((0x115e, 'LeakScope'), (0x116d, 'LeakScope'), (0x115a, 'LSC01000')))
    managers = b''.join(struct.pack('!HH', code, level) for code, level in
                        ((0x1403, 3), (0x2407, 6), (0x240f, 3), (0x1440, 5), (0x1c08, 0)))
    fields += _ddm(0x1404, managers)+_ddm(0x1147, 'LeakScope'.encode('cp037'))
    command = _ddm(0x1041, fields)
    sock.sendall(struct.pack('!HBBH', len(command)+6, 0xd0, 1, 1)+command)
    size, magic, fmt, correlation = struct.unpack('!HBBH', read.exact(6))
    if magic != 0xd0 or fmt != 2 or correlation != 1 or not 10 <= size <= 16384:
        raise ValueError('Invalid bounded DRDA EXCSAT response DSS')
    body = read.exact(size-6)
    length, code = struct.unpack('!HH', body[:4])
    if length != len(body) or code != 0x1443:
        raise ValueError('DRDA did not return EXCSATRD')
    metadata = {}
    names = {0x1147: 'server_class', 0x116d: 'server_name', 0x115a: 'server_release', 0x115e: 'external_name'}
    seen = set()
    position = 4
    while position < len(body):
        if position+4 > len(body):
            raise ValueError('Truncated DRDA EXCSATRD field')
        length, code = struct.unpack('!HH', body[position:position+4])
        if length < 4 or position+length > len(body) or code in seen or code not in {*names, 0x1404}:
            raise ValueError('Invalid DRDA EXCSATRD field bounds or codepoint')
        seen.add(code)
        value = body[position+4:position+length]
        position += length
        if code == 0x1404:
            if not value or len(value) % 4 or len(value) > 128:
                raise ValueError('Invalid DRDA manager level list')
            managers = {}
            for offset in range(0, len(value), 4):
                manager, level = struct.unpack('!HH', value[offset:offset+4])
                name = f'0x{manager:04x}'
                if name in managers:
                    raise ValueError('Duplicate DRDA manager')
                managers[name] = level
            metadata['manager_levels'] = managers
        else:
            if len(value) > 1024:
                raise ValueError('DRDA identity string exceeds its metadata limit')
            text = value.decode('cp037').strip()
            if any(ord(char) < 32 or ord(char) == 127 for char in text):
                raise ValueError('Invalid DRDA identity text')
            metadata[names[code]] = text
    if not any(metadata.get(name) for name in ('server_class', 'server_release', 'manager_levels')):
        raise ValueError('DRDA EXCSATRD returned no identifying attributes')
    return {'operation': 'DRDA EXCSAT only; no ACCSEC, SECCHK, database access or SQL',
            'product_verified': False, **metadata}, 'drda'


def _postgres_family(sock, read, address, port):
    # Reuse existing bounded SSLRequest implementation without an import cycle.
    from .native import _postgres
    metadata, protocol = _postgres(sock, read, address, port)
    return {**metadata, 'product_verified': False}, protocol


def _milvus(sock, read, address, port):
    from .grpc_native import identify_grpc
    tls = isinstance(getattr(sock, 'sock', sock), ssl.SSLSocket)
    metadata, protocol = identify_grpc(sock, read.deadline, address, port, tls=tls)
    return {**metadata, 'product_verified': False}, protocol


TCP_ADAPTERS = {'oracle': _oracle, 'maxdb': _maxdb,
                'mssql': _mssql, 'cassandra': _cql, 'scylla': _cql,
                'aerospike': _aerospike, 'hana': _hana, 'monetdb': _monetdb,
                'ceph': _ceph, 'db2': _drda, 'drda': _drda, 'db2-das': _db2_das,
                'vertica': _postgres_family, 'timescaledb': _postgres_family,
                'milvus': _milvus}
SUPPORTED_TYPES = frozenset(TCP_ADAPTERS)


def _capability(operation, protocol, limitation, *sources):
    return {'operation': operation, 'protocol': protocol, 'limitation': limitation, 'sources': list(sources)}


CAPABILITIES = {
    'oracle': _capability('TNS listener VERSION', 'oracle', 'Only a bounded VERSION REFUSE descriptor is accepted; listener version or command denial does not verify a database. No service name, CID, user, login, redirect or retry.',
        'https://nmap.org/nsedoc/scripts/oracle-tns-version.html',
        'https://github.com/nmap/nmap/blob/master/scripts/oracle-tns-version.nse',
        'https://github.com/nmap/nmap/blob/master/nmap-service-probes',
        'https://github.com/nmap/nmap/blob/master/nselib/tns.lua'),
    'maxdb': _capability('Standalone X-server PING/version', 'maxdb', 'SAP RSQL_PING_REQUEST/REPLY only; empty database names, no DBM process connection, database list, login or SQL. X-server version does not verify a database.',
        'https://github.com/GunterMueller/MaxDB_GPL_Releases/blob/master/7.6.00.37/V76_00_37/MaxDB_ORG/sys/src/en/ven42.c',
        'https://github.com/GunterMueller/MaxDB_GPL_Releases/blob/master/7.6.00.37/V76_00_37/MaxDB_ORG/sys/src/en/ven96.c',
        'https://github.com/GunterMueller/MaxDB_GPL_Releases/blob/master/7.6.00.37/V76_00_37/MaxDB_ORG/sys/src/eo/geo003.h'),
    'mssql': _capability('TDS PRELOGIN', 'mssql', 'TDS 7.x prelogin only; no database session or authentication; compatible products are not distinguished.',
        'https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/60f56408-0188-4cd5-8b90-25c6f2423868'),
    'cassandra': _capability('CQL v4 OPTIONS/SUPPORTED', 'cassandra', 'Cassandra-compatible protocol only; no STARTUP, authentication, queries or product distinction.',
        'https://github.com/apache/cassandra/blob/trunk/doc/native_protocol_v4.spec'),
    'aerospike': _capability('Info build and version', 'aerospike', 'Only requested identity fields; access-denied or malformed replies do not verify identity.',
        'https://aerospike.com/docs/database/reference/info/',
        'https://github.com/aerospike/aerospike-client-go/blob/master/types/message.go',
        'https://github.com/aerospike/aerospike-client-go/blob/master/info.go'),
    'hana': _capability('Initial protocol prolog', 'hana', 'Protocol initialization values only; product version is not a database release; no authentication or session.',
        'https://github.com/SAP/go-hdb/blob/main/driver/internal/protocol/init.go',
        'https://github.com/SAP/go-hdb/blob/main/driver/internal/protocol/protocol.go'),
    'monetdb': _capability('Receive-only MAPI v9 server greeting', 'monetdb', 'Never responds to the challenge; no credentials or database selection. Only protocol v9 is supported.',
        'https://github.com/MonetDB/MonetDB-PHP/blob/master/protocol_doc/README.md'),
    'ceph': _capability('Receive-only messenger v1/v2 banner', 'ceph', 'Messenger protocol banner only; monitor role, cluster health and authentication are not verified.',
        'https://github.com/ceph/ceph/blob/main/src/include/msgr.h',
        'https://github.com/ceph/ceph/blob/main/src/msg/async/ProtocolV2.cc',
        'https://docs.ceph.com/en/latest/dev/msgr2/'),
    'db2': _capability('DRDA EXCSAT/EXCSATRD', 'drda', 'DRDA supports multiple products; server attributes are reported without claiming IBM Db2. No security exchange or database access.',
        'https://github.com/apache/derby/blob/trunk/java/org.apache.derby.client/org/apache/derby/client/net/NetConnectionRequest.java',
        'https://github.com/apache/derby/blob/trunk/java/org.apache.derby.client/org/apache/derby/client/net/NetConnectionReply.java'),
    'db2-das': _capability('Single DAS ping/version probe', 'db2-das', 'Separate administration protocol, not DRDA. Only the known little-endian DAS envelope and typed ping/version layout are supported. No database verification, credentials, profile export, discovery routines, instance/database enumeration or state changes.',
        'https://github.com/nmap/nmap/blob/master/nmap-service-probes',
        'https://github.com/nmap/nmap/blob/master/scripts/db2-das-info.nse',
        'https://nmap.org/nsedoc/scripts/db2-das-info.html'),
    'vertica': _capability('PostgreSQL SSLRequest', 'postgresql', 'Only PostgreSQL-compatible transport is verified; Vertica and TimescaleDB require additional product evidence.',
        'https://www.postgresql.org/docs/current/protocol-flow.html#PROTOCOL-FLOW-SSL'),
    'milvus': _capability('gRPC Health/Check', 'grpc', 'Existing generic gRPC health exchange only; no Milvus product identity, collections, authentication or queries.',
        'https://github.com/grpc/grpc/blob/master/doc/health-checking.md'),
}
CAPABILITIES['scylla'] = dict(CAPABILITIES['cassandra'])
CAPABILITIES['drda'] = dict(CAPABILITIES['db2'])
CAPABILITIES['timescaledb'] = dict(CAPABILITIES['vertica'])

CONSTRAINTS = {
    'informix': {'reason': 'No verified primary non-login SQLI identification exchange was established; no SQLI connection credentials, database selection or queries are sent.',
                 'sources': ['https://publib.boulder.ibm.com/epubs/pdf/5343a.pdf']},
    'firebird': {'reason': 'The documented initial connect includes database/service and user identification; modern negotiation carries authentication plugin data. No credential-free, target-free exchange was established within this scope.',
                 'sources': ['https://firebirdsql.org/file/documentation/html/en/firebirddocs/wireprotocol/firebird-wire-protocol.html']},
}
