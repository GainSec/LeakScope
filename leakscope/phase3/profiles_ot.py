"""OT protocol classifier candidates, independent of listening ports.

Shodan identifiers come from its /shodan/protocols list; shodan.module is in
its supported search-filter list. ZoomEye documents service= as its application
protocol filter. Its service aliases below are candidate protocol names, not a
claim that every protocol is present in its current index. Existing decoded
Netlas fields are retained. General application-protocol candidates use only
the documented prot7/prot4 fields; unsupported decoded fields are not fabricated.
"""

SHODAN_PROTOCOLS = 'https://developer.shodan.io/api#shodan-protocols'
SHODAN_FILTERS = 'https://developer.shodan.io/api#shodan-host-search-filters'
ZOOMEYE_SERVICE = 'https://github.com/zoomeye-ai/ZoomEye-python/blob/master/api.md'
NETLAS_FIELDS = 'https://docs.netlas.io/knowledge-base/field-reference/responses/'
NMAP_PROBES = 'https://github.com/nmap/nmap/blob/master/nmap-service-probes'


def _profile(name, modules=(), services=(), *, netlas=None, udp=False, note=''):
    summary = (
        name + ' protocol-classifier candidate on any indexed port. '
        'Where supplied, Shodan uses scanner modules, ZoomEye uses service aliases, and Netlas uses decoded or application-protocol fields. '
        'Indexed coverage is not live-verified; classifier naming can limit recall. '
        'An indexed classifier is not verified current protocol or vendor identity.'
    )
    if note:
        summary += ' ' + note
    overrides=[]
    if modules:
        # Comma values are OR alternatives within one Shodan filter.
        overrides.append(('shodan','shodan.module:'+','.join(modules)))
    if services:
        query=' || '.join('service="'+value+'"' for value in services)
        query='('+query+')' if len(services)>1 else query
        if udp: query='protocol="UDP" && '+query
        overrides.append(('zoomeye',query))
    if netlas is None and services:
        netlas='prot7:('+' OR '.join('"'+value+'"' for value in services)+')'
        if udp: netlas='prot4:udp AND '+netlas
    if netlas:
        overrides.append(('netlas',netlas))
    sources=[SHODAN_PROTOCOLS,SHODAN_FILTERS,ZOOMEYE_SERVICE]
    if netlas:
        sources.append(NETLAS_FIELDS)
    return {'summary':summary,'signals':(), 'sources':tuple(sources),'overrides':tuple(overrides)}


PROFILES = {
    'modbus': _profile('Modbus', ('modbus',), ('modbus','modbus-tcp'),
        netlas='modbus.function_code:43 AND modbus.length:>0'),
    'bacnet': _profile('BACnet/IP', ('bacnet',), ('bacnet','bacnet-ip'),
        netlas='bacnet.is_bacnet:true'),
    'opcua': _profile('OPC UA', ('opc-ua',), ('opc-ua','opcua')),
    'ethernetip': _profile('EtherNet/IP explicit messaging', ('ethernetip',),
        ('ethernetip','ethernetip-1','ethernet-ip','enip')),
    'cipio': _profile('EtherNet/IP UDP family', ('ethernetip-udp',),
        ('ethernetip','ethernet-ip','enip'), udp=True,
        note='Cyclic CIP I/O is not verified by this family search; the classifier may describe explicit UDP identity messaging.'),
    'dnp3': _profile('DNP3', ('dnp3',), ('dnp3',)),
    'iec104': _profile('IEC 60870-5-104', ('iec-104',),
        ('iec-104','iec104','iec-60870-5-104','iec60870-5-104')),
    'iec61850': _profile('IEC 61850 MMS', ('iec-61850',), ('iec-61850','iec61850','iec61850-mms')),
    'fins': _profile('Omron FINS family', ('omron-tcp',), ('fins','omron-fins','omron-tcp'),
        note='The Shodan module identifies the TCP companion; UDP FINS transport is not verified by that module.'),
    'melsec': _profile('Mitsubishi MELSEC/Q family', ('melsec-q-tcp','melsec-q-udp'),
        ('melsec','melsec-q','melsec-q-tcp','melsec-q-udp'),
        note='MELSEC/Q identity is a protocol-family candidate; it does not establish support for every SLMP variant.'),
    'ads': _profile('Beckhoff ADS', (), ('ads','beckhoff-ads'),
        note='No corresponding Shodan scanner module was found in the checked protocol list; only the explicit ZoomEye service-name candidate is supplied.'),
    'hartip': _profile('HART-IP', ('hart-ip-udp',), ('hart-ip','hartip')),
    'codesys': _profile('CODESYS gateway', ('codesys',), ('codesys',)),
    'proconos': _profile('ProConOS', ('proconos',), ('proconos',)),
    'pcworx': _profile('Phoenix Contact PC Worx', ('pcworx',), ('pcworx','pc-worx')),
    'srtp': _profile('GE SRTP', ('general-electric-srtp',), ('general-electric-srtp','ge-srtp')),
    's7': _profile('Siemens S7', ('s7',), ('s7','s7comm','siemens-s7'),
        netlas='s7.module:* AND s7.module_type:*',
        note='A protocol classifier does not establish a physical PLC or Siemens hardware; compatible implementations and emulators can match.'),
    'fox': _profile('Niagara Fox family', ('fox','secure-fox'), ('niagara-fox','fox','secure-fox'),
        note='The family includes the TLS-wrapped Secure Fox companion. A Fox station can be a software supervisor rather than a physical controller.'),
    'lantronix': {
        'summary': 'Lantronix TCP setup banner or protocol-classifier candidate on any indexed port. '
            'Shodan matches the indexed Universal Device Server greeting over TCP; ZoomEye and Netlas use service-name candidates. '
            'Indexed coverage is not live-verified; classifier naming can limit recall. '
            'This does not cover all Lantronix products or establish configuration permission.',
        'signals': (),
        'sources': ('https://book.shodan.io/getting-started/query-syntax/', ZOOMEYE_SERVICE, NETLAS_FIELDS, NMAP_PROBES),
        'overrides': (
            ('shodan', '"Lantronix Universal Device Server"'),
            ('zoomeye', 'protocol="TCP" && service="lantronix"'),
            ('netlas', 'prot4:tcp AND prot7:"lantronix"'),
        ),
    },
    'lantronix-config': _profile('Lantronix UDP setup/configuration', ('lantronix-udp',),
        ('lantronix-config','lantronix_config'), udp=True,
        note='This is an indexed setup/discovery classifier, separate from TCP setup and HTTP management. No configuration operation is performed.'),
    'smux': _profile('SNMP SMUX', (), ('smux',),
        note='SNMP multiplexing is not an ICS-specific protocol; a match does not establish a PLC. No corresponding Shodan scanner module was found in the checked protocol list.'),
    'vertx-edge': _profile('HID VertX/EDGE discovery', ('vertx-edge',), ('vertx-edge','vertx'), udp=True,
        note='HID access-control device discovery classification does not establish a PLC or current access-control state.'),
    'coap': _profile('CoAP', ('coap','coap-dtls'), ('coap',),
        note='CoAP support is a protocol candidate; it does not identify a device technology, and a matching classifier is stronger than an isolated acknowledgement.'),
}
