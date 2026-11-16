"""Searchable response alternatives for protocols without a checked Shodan module.

Native response terms and HTTP companions are different evidence classes.
These definitions cannot make a provider scan a protocol or index a new path.
"""
from .profiles_database import PROFILES as DATABASE
from .profiles_ot import PROFILES as OT

NMAP = 'https://github.com/nmap/nmap/blob/master/nmap-service-probes'
MONET = 'https://github.com/MonetDB/MonetDB-PHP/blob/master/protocol_doc/README.md'
AEROSPIKE = 'https://raw.githubusercontent.com/aerospike/aerospike-prometheus-exporter/master/README.md'
MILVUS = 'https://raw.githubusercontent.com/milvus-io/milvus/master/internal/http/webui/index.html'
HANA = 'https://help.sap.com/doc/f32a797a5ec94d8ba4d7ed240d95bfae/2.0.04/en-US/tutorial-HANA.html'
RFC_SMUX = 'https://www.rfc-editor.org/rfc/rfc1227.html'


def _quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _banner(term):
    return '(\\*.banner:' + _quote(term) + ' OR raw.strings:' + _quote(term) + ')'


def _native_terms(*terms):
    return {
        'shodan': ' '.join(_quote(term) for term in terms),
        'zoomeye': ' && '.join('banner=' + _quote(term) for term in terms),
        'netlas': ' AND '.join(_banner(term) for term in terms),
    }


def _response(key, queries, explanation, *sources):
    original = (DATABASE if key in DATABASE else OT)[key]
    previous = dict(original['overrides'])
    combined = {}
    notes = {}
    limit = ' Candidate only: provider indexing, scanner probes, path coverage and response variants limit recall; current indexed matches are not live-validated.'
    for provider, query in queries.items():
        if provider == 'shodan':
            combined[provider] = query
            notes[provider] = explanation + limit
        else:
            combined[provider] = '(' + previous[provider] + ') ' + ('||' if provider == 'zoomeye' else 'OR') + ' (' + query + ')'
            notes[provider] = 'Native protocol-classifier candidate OR response alternative. ' + explanation + ' Classifier aliases are not live-validated.' + limit
    return {
        **original,
        'summary': explanation + ' ZoomEye and Netlas also retain native protocol-classifier alternatives.' + limit,
        'sources': tuple(dict.fromkeys((*original['sources'], *sources))),
        'overrides': tuple(combined.items()),
        'provider_notes': tuple(notes.items()),
    }


PROFILES = {
    'monetdb': _response('monetdb', {
        'shodan': 'all:":mserver:9:",":merovingian:9:"',
        'zoomeye': 'banner=":mserver:9:" || banner=":merovingian:9:"',
        'netlas': _banner(':mserver:9:') + ' OR ' + _banner(':merovingian:9:'),
    }, 'Native MonetDB MAPI version-9 challenge candidate from mserver or merovingian endpoint text. '
       'Shodan all searches indexed banner/metadata text; this does not validate the full binary framing, authentication or other MAPI versions.', MONET),
    'maxdb': _response('maxdb', _native_terms('Rejected bad connect packet'),
        'Native MaxDB-compatible rejection-text candidate documented by the Nmap service matcher. '
        'This rejection is not a version or successful database login; it requires that a scanner elicit and index the response.', NMAP),
    'informix': _response('informix', _native_terms('lsrvinfx', 'asfecho'),
        'Native Informix SQLI response-text candidate: server identifier lsrvinfx plus asfecho from documented Nmap response matchers. '
        'These are printable fragments inside binary replies, not full handshake verification or an InformixHQ page. Probe and response variants limit coverage.', NMAP),
    'aerospike': _response('aerospike', {
        'shodan': 'http.html:"aerospike_node_up"',
        'zoomeye': 'http.body="aerospike_node_up"',
        'netlas': 'http.body:"aerospike_node_up"',
    }, 'Aerospike Prometheus exporter HTTP companion candidate matching aerospike_node_up. '
       'The exporter can report this metric when its database is unreachable; this does not identify a reachable native Aerospike endpoint. '
       'The provider must index the metrics path and authentication may hide it.', AEROSPIKE),
    'milvus': _response('milvus', {
        'shodan': 'http.title:"Milvus Management"',
        'zoomeye': 'title="Milvus Management"',
        'netlas': 'http.title:"Milvus Management"',
    }, 'Milvus Management HTTP WebUI companion candidate from the shipped HTML title. '
       'Requires indexing /webui/ or a landing page with that title; it does not identify the native Milvus gRPC service, version, access permission or database contents.', MILVUS),
    'hana': _response('hana', {
        'shodan': '"x-sap-login-page:"',
        'zoomeye': 'http.header="x-sap-login-page:"',
        'netlas': 'http.unknown_headers.key:x_sap_login_page',
    }, 'SAP HANA XS HTTP companion candidate matching the x-sap-login-page response header used by the documented XS API. '
       'This is an XS authentication/management response marker, not native HANA SQL identification, an exact header value or a claim about every HANA deployment.', HANA),
    'ads': _response('ads', {
        'shodan': '"WEB SERVER V" "title: BC"',
        'zoomeye': 'http.header="WEB SERVER V" && http.header="title: BC"',
        'netlas': 'http.headers.server:"WEB SERVER V" AND http.unknown_headers.key:title AND http.unknown_headers.value:BC*',
    }, 'Legacy Beckhoff BC bus-controller HTTP companion candidate from its WEB SERVER V server header and custom title: BC header. '
       'The custom title is an HTTP header, not an HTML title. This family hint does not establish native ADS reachability or identify all modern TwinCAT systems.', NMAP),
    'smux': {
        **OT['smux'],
        'sources': (*OT['smux']['sources'], RFC_SMUX, NMAP),
        'unavailable': (('shodan', 'No Shodan preset: SMUX uses binary BER responses; the checked scanner list has no SMUX module. '
            'A usable all-port search requires a documented SMUX classifier, indexed decoded field, or representative indexed banner signature. '
            'The short binary ClosePDU is not a verified searchable text fingerprint; SNMP or port-only filters would not establish SMUX.'),),
    },
}
