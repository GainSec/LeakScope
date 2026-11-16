"""Database response/protocol candidates without port or product predicates.

Protocol field values are candidate scanner classifications, not a promise that
an index currently contains every protocol. Families and HTTP companions are
explicitly distinguished from a verified engine or extension.
"""

SHODAN_PROTOCOLS = 'https://developer.shodan.io/api#shodan-protocols'
SHODAN_FILTERS = 'https://www.shodan.io/search/filters'
NETLAS_FIELDS = 'https://docs.netlas.io/knowledge-base/field-reference/responses/'
ZOOMEYE_API = 'https://github.com/zoomeye-ai/ZoomEye-python/blob/master/api.md'

# key, protocol aliases, exact currently published Shodan scanner module.
# ZoomEye service and Netlas prot7/raw.suggested_protocol are native protocol
# classifiers. They are not product-tag predicates or conventional port hints.
_NATIVE = (
    ('mssql', ('mssql', 'ms-sql'), 'ms-sql'),
    ('oracle', ('oracle', 'oracle-tns'), 'oracle-tns'),
    ('mongo', ('mongodb',), 'mongodb'),
    ('cassandra', ('cassandra', 'cql'), 'cassandra'),
    ('scylla', ('cassandra', 'cql'), 'cassandra'),
    ('aerospike', ('aerospike',), None),
    ('riak', ('riak',), 'riak'),
    ('db2', ('db2', 'drda'), 'ibm-db2-drda'),
    ('hana', ('hana', 'sap-hana'), None),
    ('maxdb', ('maxdb', 'sap-maxdb'), None),
    ('informix', ('informix', 'informix-sqli'), None),
    ('firebird', ('firebird',), 'firebird'),
    ('monetdb', ('monetdb', 'mapi'), None),
    ('milvus', ('milvus',), None),
)

PROFILES = {}
for _key, _aliases, _module in _NATIVE:
    _zoom = ' || '.join('service="' + name + '"' for name in _aliases)
    _netlas = '(' + ' OR '.join(
        field + ':"' + name + '"'
        for name in _aliases for field in ('prot7', 'raw.suggested_protocol')) + ')'
    _overrides = (('zoomeye', _zoom), ('netlas', _netlas))
    if _module:
        _overrides += (('shodan', 'shodan.module:' + _module),)
    PROFILES[_key] = {
        'summary': 'Provider-classified ' + _key + ' application-protocol candidate. '
                   'Scanner classification/heuristic evidence is not live identity verification; '
                   'provider naming and protocol scan coverage can yield no indexed matches.',
        'signals': (),
        'sources': (SHODAN_PROTOCOLS, SHODAN_FILTERS, ZOOMEYE_API, NETLAS_FIELDS),
        'overrides': _overrides,
    }

# A real indexed service="ibm-db2-admin" result contained a Ceph messenger
# banner. The service taxonomy remains a candidate, so exclude that known
# conflicting response rather than treating an IANA-like label as identity.
PROFILES['db2']['overrides']=tuple((provider,'('+query+') && banner!="ceph v2" && banner!="ceph v027"' if provider=='zoomeye' else query)
    for provider,query in PROFILES['db2']['overrides'])
PROFILES['db2']['summary']+=' ZoomEye excludes known Ceph messenger banner conflicts; remaining classifier matches still require native confirmation.'

# Preserve the stronger decoded responses already implemented for these types.
_DECODED = {
    'mssql': 'mssql.version:* AND mssql.encrypt_mode:*',
    'oracle': '(oracle.handshake.accept_version:* OR oracle.handshake.refuse_version:*)',
    'mongo': 'mongodb.build_info.version:*',
}
for _key, _query in _DECODED.items():
    PROFILES[_key]['overrides'] = tuple(
        (provider, _query if provider == 'netlas' else query)
        for provider, query in PROFILES[_key]['overrides'])
    PROFILES[_key]['summary'] += ' Netlas uses its decoded database handshake/build-info fields.'

PROFILES['cassandra']['summary'] = (
    'Cassandra-compatible CQL protocol candidate from scanner classification. '
    'Compatible CQL engines can match; this does not identify a particular database engine. '
    'Netlas protocol/heuristic naming and scanner coverage determine whether any records match.')
PROFILES['scylla']['summary'] = (
    'Cassandra-compatible CQL family candidate; this does not identify ScyllaDB. '
    'Other CQL-compatible engines can match. Provider naming and protocol scan coverage '
    'determine whether any records match; use live evidence to distinguish the engine.')

# These are intentionally family searches. They do not pretend that a generic
# PostgreSQL startup negotiation exposes a vendor identity or extension list.
for _key, _engine in (('timescaledb', 'TimescaleDB'), ('vertica', 'Vertica'), ('cratedb', 'CrateDB')):
    PROFILES[_key] = {
        'summary': 'PostgreSQL-compatible protocol family candidate; this does not identify ' + _engine + '. '
                   'A startup response does not establish the engine or installed extensions. '
                   'Plain PostgreSQL and other compatible servers can match.',
        'signals': (),
        'sources': (SHODAN_PROTOCOLS, ZOOMEYE_API, NETLAS_FIELDS),
        'overrides': (
            ('shodan', 'shodan.module:postgresql'),
            ('zoomeye', '(service="postgresql" || service="postgres")'),
            ('netlas', 'postgres.is_ssl:* AND (postgres.supported_versions:* OR postgres.startup_error:* OR postgres.protocol_error:*)'),
        ),
    }

# HTTP title candidates describe their actual matching evidence rather than
# claiming that a generic HTTP transport is a native database fingerprint.
for _key, _name in (('pinot', 'Pinot'), ('trino', 'Trino'), ('presto', 'Presto'), ('opentsdb', 'OpenTSDB')):
    PROFILES[_key] = {
        'summary': 'HTTP title candidate containing ' + _name + '; a related web management/branding '
                   'interface, not proof of a reachable native database. Other pages using this '
                   'title can match; custom titles/authentication and scan coverage reduce recall.',
        'signals': (('title', _name),),
        'sources': (SHODAN_FILTERS, ZOOMEYE_API, NETLAS_FIELDS),
        'overrides': (),
    }

PROFILES['victoriametrics'] = {
    'summary': 'VictoriaMetrics single-node root HTTP response candidate: the service heading and '
               'vmui link are present in its root handler. This identifies the HTTP interface; '
               'cluster frontends or customized/protected root pages may not match.',
    'signals': (('body', 'Single-node VictoriaMetrics'), ('body', 'vmui')),
    'sources': ('https://raw.githubusercontent.com/VictoriaMetrics/VictoriaMetrics/master/app/victoria-metrics/main.go',),
    'overrides': (),
}

# Primary schema confirms Milvus metadata exists, but it is not a native gRPC
# browser screenshot claim. The protocol candidate above depends on the
# selected provider's actual service classification/heuristics.
PROFILES['milvus']['sources'] += ('https://datapedia.shodan.io/property/milvus.html',)
