"""Port-independent search evidence and provider grammar.

These are discovery candidates, not proofs of a live service. Fingerprints match
specific indexed response forms; authentication, changed UI assets and scanners'
protocol/port coverage limit recall. Source URLs accompany every enabled query.
"""
from dataclasses import dataclass

PROVIDERS = ('shodan', 'zoomeye', 'netlas')
SYNTAX_SOURCES = {
    'shodan': 'https://book.shodan.io/getting-started/query-syntax/',
    'zoomeye': 'https://github.com/zoomeye-ai/ZoomEye-python/blob/master/api.md',
    'netlas': 'https://docs.netlas.io/knowledge-base/query-language/',
}
NETLAS_FIELDS = 'https://docs.netlas.io/knowledge-base/field-reference/responses/'


@dataclass(frozen=True)
class Fingerprint:
    summary: str
    signals: tuple = ()
    sources: tuple = ()
    # Explicit queries are used only for documented provider-specific fields.
    overrides: tuple = ()
    unavailable: tuple = ()
    provider_notes: tuple = ()


def http(summary, source, *signals):
    return Fingerprint(summary, signals, (source,))


def banner(summary, source, *terms, netlas=None):
    overrides = (('netlas', netlas),) if netlas else ()
    return Fingerprint(summary, tuple(('banner', term) for term in terms), (source,), overrides)


def metadata(summary, query):
    return Fingerprint(summary, (), (NETLAS_FIELDS,), (('netlas', query),))


# Each tuple is an AND of response features, never a required product taxonomy.
FINGERPRINTS = {
    **{key:Fingerprint(
        'Native Ouroboros protocol capture and experimental response signatures; a shared port or JA4TS value does not identify the implementation.',
        sources=('https://ouroboros-network.cardano.intersectmbo.org/cardano-diffusion/Cardano-Network-NodeToNode.html',),
        unavailable=tuple((provider,'No provider response query has been validated for this profile. Use bounded native Ouroboros capture; port matches and JA4TS do not identify an implementation.') for provider in PROVIDERS))
       for key in ('cardano','amaru','cardanonode')},
    'grpc': Fingerprint(
        'gRPC candidate from indexed application/grpc response text or protocol classifiers on any indexed port. '
        'Classifier aliases are not live-validated. Header matches can include gRPC-Web, gateways or error responses; '
        'use native identity to confirm HTTP/2 gRPC. HTTP/2 or TLS alone does not identify gRPC.',
        sources=('https://grpc.github.io/grpc/core/md_doc__p_r_o_t_o_c_o_l-_h_t_t_p2.html',),
        overrides=(('shodan', '"application/grpc"'),
                   ('zoomeye', '(service="grpc" || http.header="application/grpc" || banner="application/grpc")'),
                   ('netlas', '(prot7:"grpc" OR raw.suggested_protocol:"grpc" OR http.headers.content_type:"application/grpc")'))),
    'plc': Fingerprint(
        'PLC device-classification candidate on any indexed port; this does not establish a protocol, vendor or current identity. '
        'Provider device fields are documented, but the plc classifier value and index coverage are not live-validated. '
        'Protocol presets remain separate and can find devices omitted by this taxonomy.',
        sources=(SYNTAX_SOURCES['zoomeye'], 'https://www.shodan.io/search/filters'),
        overrides=(('shodan', 'device:plc'), ('zoomeye', 'device="plc"')),
        unavailable=(('netlas', 'No verified generic device-class field exists in the checked Netlas response schema. '
                      'Software tags are not equivalent to a PLC device classification; use a protocol preset instead.'),)),
    'postgresql': banner('PostgreSQL startup rejection text; Netlas decoded PostgreSQL handshake fields.',
        'https://www.postgresql.org/docs/current/protocol-flow.html',
        'unsupported frontend protocol', 'server supports',
        netlas='postgres.is_ssl:* AND (postgres.supported_versions:* OR postgres.startup_error:* OR postgres.protocol_error:*)'),
    'mysql': banner('Native authentication plugin name in a MySQL-compatible greeting; Netlas decoded greeting version.',
        'https://dev.mysql.com/doc/dev/mysql-server/latest/page_protocol_connection_phase_packets_protocol_handshake_v10.html',
        'mysql_native_password', netlas='mysql.server_version:*'),
    'mssql': metadata('Netlas decoded TDS prelogin version and encryption negotiation.',
        'mssql.version:* AND mssql.encrypt_mode:*'),
    'oracle': metadata('Netlas accepted TNS handshake or structured Oracle refusal response.',
        '(oracle.handshake.accept_version:* OR oracle.handshake.refuse_version:*)'),
    'redis': banner('INFO response includes Redis version and operational mode; compatible implementations may match.',
        'https://redis.io/docs/latest/commands/info/', 'redis_version', 'redis_mode',
        netlas='redis.server_info.version:* AND redis.server_info.redis_mode:*'),
    'memcached': banner('ASCII stats response includes version and current connection counters.',
        'https://github.com/memcached/memcached/blob/master/doc/protocol.txt',
        'STAT version', 'STAT curr_connections', netlas='memcached.version:* AND memcached.curr_connections:*'),
    'mongo': metadata('Netlas MongoDB build-info response fields; requires a decoded server response.',
        'mongodb.build_info.version:*'),
    'couchdb': http('Root JSON welcome response and CouchDB/vendor keys.',
        'https://docs.couchdb.org/en/stable/api/server/common.html',
        ('body', 'couchdb'), ('body', 'Welcome'), ('body', 'vendor')),
    'couchbase': http('Couchbase administrative console title; web interface only.',
        'https://github.com/couchbase/ns_server/tree/master/priv/public/ui',
        ('title', 'Couchbase Console')),
    'elastic': http('Root response cluster UUID and Elasticsearch tagline.',
        'https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-info',
        ('body', 'cluster_uuid'), ('body', 'You Know, for Search')),
    'opensearch': http('Root response cluster UUID and OpenSearch tagline.',
        'https://docs.opensearch.org/latest/api-reference/cluster-api/info/',
        ('body', 'cluster_uuid'), ('body', 'The OpenSearch Project')),
    'rethink': http('RethinkDB administration console title; not the native driver port.',
        'https://rethinkdb.com/docs/administration-tools/',
        ('title', 'RethinkDB Administration Console')),
    'influxdb': http('InfluxDB version response header; matches only indexed responses carrying that header.',
        'https://docs.influxdata.com/platform/identify-version/', ('header', 'X-Influxdb-Version')),
    'clickhouse': http('ClickHouse HTTP server response header; not the generic Ok. response body.',
        'https://clickhouse.com/docs/interfaces/http', ('header', 'X-ClickHouse-Server-Display-Name')),
    'neo4j': http('Discovery API JSON includes Neo4j version and direct Bolt URI keys.',
        'https://neo4j.com/docs/http-api/current/endpoints/',
        ('body', 'neo4j_version'), ('body', 'bolt_direct')),
    'arangodb': http('ArangoDB Server response header.',
        'https://docs.arangodb.com/3.12/develop/http-api/general-request-handling/', ('server', 'ArangoDB')),
    'qdrant': http('Qdrant root instance-information JSON title and version keys.',
        'https://api.qdrant.tech/api-reference/service/root',
        ('body', 'qdrant'), ('body', 'title'), ('body', 'version')),
    'weaviate': http('Weaviate root discovery links to REST documentation.',
        'https://github.com/weaviate/weaviate/blob/main/adapters/handlers/rest/handlers_misc.go',
        ('body', 'documentationHref'), ('body', 'weaviate')),
    'questdb': http('QuestDB console title; HTTP UI only.',
        'https://github.com/questdb/ui', ('title', 'QuestDB')),
    'druid': http('Druid unified-console title; HTTP UI only.',
        'https://github.com/apache/druid/tree/master/web-console', ('title', 'Apache Druid')),
    'hbase': http('HBase master status page title; HTTP UI only.',
        'https://github.com/apache/hbase/blob/master/hbase-server/src/main/jamon/org/apache/hadoop/hbase/tmpl/master/MasterStatusTmpl.jamon',
        ('title', 'Master'), ('body', 'HBase'), ('body', 'Zookeeper')),
    'smb': metadata('Netlas decoded SMB version-support negotiation; true and false values both qualify.',
        'smb.smbv1_support:*'),
    'rsync': banner('Native rsync daemon greeting, independent of its listening port.',
        'https://download.samba.org/pub/rsync/rsync.1', '@RSYNCD',
        netlas='raw.strings:"RSYNCD"'),
    'ftp': Fingerprint('FTP greeting with service-specific 220 response text; Netlas decoded FTP protocol banner.',
        (('banner', '220'), ('banner', 'FTP')), ('https://www.rfc-editor.org/rfc/rfc959',),
        (('netlas', 'prot7:ftp AND ftp.banner:*'),)),
    'webdav': http('DAV capability header; identifies WebDAV capability rather than an arbitrary HTTP server.',
        'https://www.rfc-editor.org/rfc/rfc4918', ('header', 'DAV:')),
    'minio': http('MinIO Console title; web console only, not proof that an S3 bucket is readable.',
        'https://github.com/minio/console', ('title', 'MinIO Console')),
    'cephrgw': http('Ceph/RGW Server response header; generic Amazon S3 XML is deliberately insufficient.',
        'https://github.com/ceph/ceph/tree/main/src/rgw', ('server', 'Ceph')),
    'swift': http('OpenStack Swift response headers disclose its transaction and timestamp fields.',
        'https://docs.openstack.org/api-ref/object-store/', ('header', 'X-Trans-Id'), ('header', 'X-Openstack-Request-Id')),
    'seaweedfs': http('SeaweedFS master console title; HTTP interface only.',
        'https://github.com/seaweedfs/seaweedfs/tree/master/weed/server', ('title', 'SeaweedFS')),
    'webhdfs': http('Hadoop NameNode browser page and HDFS client script asset.',
        'https://github.com/apache/hadoop/blob/trunk/hadoop-hdfs-project/hadoop-hdfs/src/main/webapps/hdfs/dfshealth.html',
        ('title', 'Hadoop Administration'), ('body', 'dfshealth.js')),
    'yarn': http('YARN ResourceManager console title; HTTP interface only.',
        'https://hadoop.apache.org/docs/stable/hadoop-yarn/hadoop-yarn-site/ResourceManagerRest.html',
        ('title', 'All Applications'), ('body', 'ResourceManager')),
    'ipfs': http('IPFS HTTP response header; may identify a gateway as well as Kubo API.',
        'https://docs.ipfs.tech/reference/kubo/rpc/', ('header', 'X-Ipfs-Path')),
    'synology': http('Synology DSM web frontend title; other NAS interfaces may not match.',
        'https://datapedia.shodan.io/property/synology_dsm.html', ('title', 'Synology DiskStation')),
    'qnap': http('QNAP NAS login title.',
        'https://datapedia.shodan.io/property/qnap.html', ('title', 'QNAP Turbo NAS')),
    'modbus': metadata('Netlas decoded Modbus device-identification response with nonempty wire length; parser may have vendor-field errors.',
        'modbus.function_code:43 AND modbus.length:>0'),
    'bacnet': metadata('Netlas explicitly recognized and decoded a BACnet Device-object response.',
        'bacnet.is_bacnet:true'),
    's7': banner('S7 PLC/module labels from decoded identification responses; Netlas decoded SZL module fields.',
        'https://book.shodan.io/getting-started/query-syntax/', 'PLC name', 'Module type',
        netlas='s7.module:* AND s7.module_type:*'),
    'fox': banner('Niagara Fox response protocol and station-property keys.',
        'https://nmap.org/nsedoc/scripts/fox-info.html', 'fox.version', 'app.name'),
    'mqtt': banner('MQTT broker system topic in indexed broker messages; brokers without that topic are missed.',
        'https://datapedia.shodan.io/property/mqtt.html', '$SYS/broker/version',
        netlas='mqtt.topics.sys_broker_version:*'),
    'amqp': metadata('Netlas decoded AMQP server properties; binary protocol identification is not inferred from a port.',
        'amqp.version:* AND amqp.platform:*'),
    'nats': banner('NATS server INFO greeting includes server ID and protocol capability fields.',
        'https://docs.nats.io/reference/reference-protocols/nats-protocol', 'INFO', 'server_id', 'max_payload'),
    'stomp': banner('STOMP CONNECTED response and version/session headers; requires a scanner-recorded handshake.',
        'https://stomp.github.io/stomp-specification-1.2.html', 'CONNECTED', 'version:', 'session:'),
    'activemq': http('ActiveMQ administration console title.',
        'https://github.com/apache/activemq/tree/main/assembly/src/release/webapps/admin', ('title', 'ActiveMQ Console')),
    'zookeeper': banner('ZooKeeper stat/srvr response version and latency counters; disabled four-letter commands will not match.',
        'https://zookeeper.apache.org/doc/current/zookeeperAdmin.html', 'Zookeeper version', 'Latency min/avg/max'),
    'etcd': http('etcd cluster response header from an indexed HTTP API response.',
        'https://etcd.io/docs/v2.3/api/', ('header', 'X-Etcd-Cluster-Id')),
    'consul': http('Consul UI title; web UI only.',
        'https://github.com/hashicorp/consul/tree/main/ui/packages/consul-ui', ('title', 'Consul by HashiCorp')),
    'rtsp': banner('RTSP 1.0 response status line; RTSP 2.0-only replies are outside this preset.',
        'https://www.rfc-editor.org/rfc/rfc7826', 'RTSP/1.0'),
    'onvif': http('ONVIF SOAP namespace and device-information response; requires an indexed SOAP response, not just a camera title.',
        'https://www.onvif.org/ver10/device/wsdl/devicemgmt.wsdl',
        ('body', 'www.onvif.org'), ('body', 'GetDeviceInformationResponse')),
    'upnp': http('UPnP device-description XML namespace and device type.',
        'https://openconnectivity.org/developer/specifications/upnp-resources/upnp/',
        ('body', 'schemas-upnp-org'), ('body', 'deviceType')),
    'homeassistant': http('Home Assistant HTML title and frontend custom element.',
        'https://github.com/home-assistant/frontend/blob/dev/src/html/index.html.template',
        ('title', 'Home Assistant'), ('body', 'home-assistant')),
    'openhab': http('openHAB HTML title and main UI icon path.',
        'https://github.com/openhab/openhab-webui/blob/main/bundles/org.openhab.ui/web/index.html',
        ('title', 'openHAB'), ('body', '/res/icons/')),
    'nodered': http('Node-RED editor root element and stylesheet path; independent of customizable page title.',
        'https://github.com/node-red/node-red/blob/main/packages/node_modules/@node-red/editor-client/templates/index.mst',
        ('body', 'red-ui-editor'), ('body', 'red/style.min.css')),
    'thingsboard': http('ThingsBoard Angular root element and loading-spinner class, independent of configurable title.',
        'https://github.com/thingsboard/thingsboard/blob/master/ui-ngx/src/index.html',
        ('body', 'tb-root'), ('body', 'tb-loading-spinner')),
    'tasmota': http('Tasmota browser title and firmware UI menu.',
        'https://github.com/arendst/Tasmota/blob/development/tasmota/tasmota_xdrv_driver/xdrv_01_9_webserver.ino',
        ('title', 'Tasmota'), ('body', 'Main Menu')),
    'esphome': http('ESPHome web component and hosted frontend asset namespace; local/custom assets may not match.',
        'https://github.com/esphome/esphome/blob/2024.12.0/esphome/components/web_server/__init__.py',
        ('body', 'oi.esphome.io'), ('body', 'esp-app')),
    'shelly': http('Shelly device UI title and embedded RPC interface reference; older firmware may differ.',
        'https://shelly-api-docs.shelly.cloud/gen2/', ('title', 'Shelly'), ('body', 'rpc')),
    'octoprint': http('OctoPrint title and server-rendered version attribute.',
        'https://github.com/OctoPrint/OctoPrint/blob/master/src/octoprint/templates/index.jinja2',
        ('title', 'OctoPrint'), ('body', 'data-octoprint-version')),
    'domoticz': http('Domoticz UI title and device-control JavaScript asset.',
        'https://github.com/domoticz/domoticz/blob/master/www/index.html',
        ('title', 'Domoticz'), ('body', 'domoticz.js')),
    'zigbee2mqtt': http('Zigbee2MQTT frontend title and touch-icon asset; alternative/custom frontends may differ.',
        'https://github.com/nurikk/z2m-frontend/blob/master/src/index.html',
        ('title', 'Zigbee2MQTT'), ('body', 'apple-touch-icon.png')),
}

# Domain modules contain data only; the compiler remains shared across providers.
from .profiles_ot import PROFILES as OT_PROFILES
from .profiles_database import PROFILES as DATABASE_PROFILES
from .profiles_storage import PROFILES as STORAGE_PROFILES
from .profiles_legacy import PROFILES as LEGACY_PROFILES
from .profiles_gap_evidence import PROFILES as GAP_PROFILES
from .hmi_profiles import PROFILES as HMI_PROFILES, HMI_TYPES

for _definitions in (OT_PROFILES, DATABASE_PROFILES, STORAGE_PROFILES, LEGACY_PROFILES, GAP_PROFILES, HMI_PROFILES):
    for _key, _definition in _definitions.items():
        FINGERPRINTS[_key] = Fingerprint(**_definition)

UNSUPPORTED_REASONS = {
    'sftp': 'An SSH greeting does not prove that the SFTP subsystem is enabled; no independent indexed SFTP response signature is defined.',
    'timescaledb': 'A PostgreSQL handshake cannot distinguish the TimescaleDB extension; no independent indexed extension evidence is defined.',
    'scylla': 'The Cassandra-compatible wire handshake cannot reliably distinguish ScyllaDB; no independent indexed ScyllaDB signature is defined.',
    'cassandra': 'CQL capabilities are shared by Cassandra-compatible implementations; no independent Cassandra-specific indexed response signature is defined.',
    'cipio': 'Binary EtherNet/IP I/O traffic has no verified searchable response signature in the supported provider fields.',
    'coap': 'A CoAP acknowledgement alone is not a technology fingerprint; no verified provider-specific decoded response query is defined.',
    'iec61850': 'ISO-on-TCP is shared with S7; no verified MMS-specific indexed response query is defined.',
}

# Actual HTTP-capable interfaces, independent of conventional ports. This is a
# capability hint, not an assertion that every endpoint for a technology is HTTP.
FINGERPRINTS['vnc']=Fingerprint('VNC/RFB greeting or protocol-classifier candidate on any indexed port; current identity and access require capture. Indexed greetings and classifier coverage may vary.',
    sources=('https://www.rfc-editor.org/rfc/rfc6143',),overrides=(('shodan','"RFB 003"'),('zoomeye','service="vnc"'),('netlas','prot7:"vnc"')))

HTTP_CAPTURE_TYPES = HMI_TYPES | frozenset({
    'angular', 'amazons3be', 'amazonbe', 'gitlab', 'dirs', 'jenkins', 'sonarqube', 'kibana',
    'grafana', 'prometheus', 'swagger', 'mongoexpress', 'keys', 'nexus', 'artifactory',
    'registry', 'rabbitmq', 'solr', 'gitea', 'gogs', 'azureblob', 'gcsbucket', 'hdfs',
    'couchdb', 'couchbase', 'elastic', 'opensearch', 'rethink', 'influxdb',
    'clickhouse', 'neo4j', 'arangodb', 'riak', 'cratedb', 'qdrant',
    'weaviate', 'questdb', 'victoriametrics', 'druid', 'pinot', 'trino', 'presto',
    'opentsdb', 'hbase', 'webdav', 'minio', 'cephrgw', 'swift', 'seaweedfs',
    'webhdfs', 'yarn', 'ipfs', 'synology', 'qnap', 'nats', 'activemq', 'pulsar',
    'etcd', 'consul', 'onvif', 'upnp', 'homeassistant', 'openhab', 'nodered',
    'thingsboard', 'tasmota', 'esphome', 'shelly', 'octoprint', 'domoticz', 'zigbee2mqtt',
})


def _quoted(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _signal(provider, kind, value):
    quoted = _quoted(value)
    if kind == 'body':
        return {'shodan': 'http.html:', 'zoomeye': 'http.body=', 'netlas': 'http.body:'}[provider] + quoted
    if kind == 'title':
        return {'shodan': 'http.title:', 'zoomeye': 'title=', 'netlas': 'http.title:'}[provider] + quoted
    if kind == 'server':
        return {'shodan': '"Server: ' + str(value) + '"', 'zoomeye': 'http.header.server=' + quoted,
                'netlas': 'http.headers.server:' + quoted}[provider]
    if kind == 'header':
        if provider == 'netlas':
            name = value.rstrip(':').lower().replace('-', '_')
            return f'(http.headers.{name}:* OR http.unknown_headers.key:{name})'
        return ('' if provider == 'shodan' else 'http.header=') + _quoted(value.rstrip(':') + ':')
    if kind == 'banner':
        if provider == 'shodan':
            return quoted
        if provider == 'zoomeye':
            return 'banner=' + quoted
        # Unlike an unqualified term, these fields actually index response text.
        return '(\\*.banner:' + quoted + ' OR raw.strings:' + quoted + ')'
    raise ValueError('Unknown fingerprint signal kind')


def fingerprint_query(key, provider):
    if provider not in PROVIDERS:
        raise ValueError('Unknown search provider')
    fp = FINGERPRINTS.get(key)
    if fp is None:
        return {'query': '', 'supported': False, 'reason': UNSUPPORTED_REASONS.get(key,
            'No all-port query preset is implemented for this technology on this provider.'), 'basis': []}
    overrides = dict(fp.overrides)
    query = overrides.get(provider)
    if query is None and fp.signals:
        join = {'shodan': ' ', 'zoomeye': ' && ', 'netlas': ' AND '}[provider]
        query = join.join(_signal(provider, *signal) for signal in fp.signals)
    if not query:
        reason = 'No all-port query preset is implemented for this provider. Other providers may have a protocol or response query.'
        if provider == 'shodan':
            reason = 'No Shodan preset is implemented: the checked scanner list has no corresponding module, and an independent response query is still needed.'
        reason = dict(fp.unavailable).get(provider, reason)
        return {'query': '', 'supported': False,
                'reason': reason,
                'basis': list(fp.sources)}
    sources = [*fp.sources, SYNTAX_SOURCES[provider]]
    if provider == 'netlas':
        sources.append(NETLAS_FIELDS)
    return {'query': query, 'supported': True, 'reason': dict(fp.provider_notes).get(provider, fp.summary),
            'basis': list(dict.fromkeys(sources))}


def nonstandard_query(dto, provider, ports):
    """Exclude defaults only after a usable independent signature exists."""
    result = {**dto, 'basis': list(dto['basis'])}
    if not ports:
        return {**result, 'query': '', 'supported': False,
                'reason': 'This profile has no universal reference port; use its all-indexed-ports query.'}
    if not dto['supported']:
        return result
    query = dto['query']
    if provider == 'shodan':
        result['query'] = query + ' -port:' + ','.join(str(port) for port in ports)
    elif provider == 'zoomeye':
        result['query'] = '(' + query + ') && ' + ' && '.join(f'port!="{port}"' for port in ports)
    elif provider == 'netlas':
        result['query'] = '(' + query + ') AND NOT port:(' + ' OR '.join(str(port) for port in ports) + ')'
    else:
        raise ValueError('Unknown search provider')
    result['reason'] += ' Conventional ports excluded; provider scan coverage still limits discovery.'
    return result
