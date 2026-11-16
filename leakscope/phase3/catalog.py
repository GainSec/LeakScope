"""Discovery candidates, explicitly separate from verified native adapters.

Ports are conventional service endpoints, not identification evidence. Query
grammar follows Shodan's query guide, ZoomEye API v2 and Netlas Lucene fields.
IANA service-name assignments and vendor defaults are navigation aids; every
result must still be fingerprinted before a technology is considered verified.
"""
from copy import deepcopy
from .native import IDENTITY_TYPES, identity_capability
from .catalog_presentation import presentation_for
from .hmi_profiles import DEFINITIONS as HMI_DEFINITIONS
from .fingerprints import (FINGERPRINTS, HTTP_CAPTURE_TYPES, PROVIDERS, SYNTAX_SOURCES, fingerprint_query, nonstandard_query)

# key, human name, category, conventional candidate ports, transport
_ENTRIES = (
    ('cardano','Cardano / Ouroboros protocol','messaging',(3001,),'tcp'),
    ('amaru','Amaru (native response signature)','messaging',(3001,),'tcp'),
    ('cardanonode','Haskell cardano-node (native response signature)','messaging',(3001,),'tcp'),
    ('postgresql','PostgreSQL','database',(5432,),'tcp'),
    ('mysql','MySQL / MariaDB','database',(3306,33060),'tcp'),
    ('mssql','Microsoft SQL Server','database',(1433,),'tcp'),
    ('oracle','Oracle Database listener','database',(1521,),'tcp'),
    ('redis','Redis','database',(6379,),'tcp'),
    ('memcached','Memcached','database',(11211,),'tcp'),
    ('mongo','MongoDB','database',(27017,),'tcp'),
    ('cassandra','Apache Cassandra','database',(9042,),'tcp'),
    ('scylla','ScyllaDB','database',(9042,),'tcp'),
    ('couchdb','Apache CouchDB','database',(5984,6984),'tcp'),
    ('couchbase','Couchbase','database',(8091,11210),'tcp'),
    ('elastic','Elasticsearch','database',(9200,),'tcp'),
    ('opensearch','OpenSearch','database',(9200,),'tcp'),
    ('rethink','RethinkDB','database',(28015,8080),'tcp'),
    ('influxdb','InfluxDB','database',(8086,),'tcp'),
    ('clickhouse','ClickHouse','database',(8123,9000),'tcp'),
    ('neo4j','Neo4j','database',(7474,7687),'tcp'),
    ('arangodb','ArangoDB','database',(8529,),'tcp'),
    ('aerospike','Aerospike','database',(3000,),'tcp'),
    ('riak','Riak KV','database',(8087,8098),'tcp'),
    ('vertica','Vertica','database',(5433,),'tcp'),
    ('db2','IBM Db2','database',(50000,),'tcp'),
    ('hana','SAP HANA','database',(30015,30013),'tcp'),
    ('maxdb','SAP MaxDB','database',(7210,),'tcp'),
    ('informix','IBM Informix','database',(9088,),'tcp'),
    ('firebird','Firebird','database',(3050,),'tcp'),
    ('monetdb','MonetDB','database',(50000,),'tcp'),
    ('cratedb','CrateDB','database',(4200,5432),'tcp'),
    ('qdrant','Qdrant','database',(6333,6334),'tcp'),
    ('milvus','Milvus','database',(19530,9091),'tcp'),
    ('weaviate','Weaviate','database',(8080,50051),'tcp'),
    ('questdb','QuestDB','database',(9000,8812),'tcp'),
    ('victoriametrics','VictoriaMetrics','database',(8428,),'tcp'),
    ('druid','Apache Druid','database',(8888,8082),'tcp'),
    ('pinot','Apache Pinot','database',(9000,8099),'tcp'),
    ('trino','Trino','database',(8080,),'tcp'),
    ('presto','Presto','database',(8080,),'tcp'),
    ('opentsdb','OpenTSDB','database',(4242,),'tcp'),
    ('timescaledb','TimescaleDB','database',(5432,),'tcp'),
    ('hbase','Apache HBase','database',(16010,9090),'tcp'),
    ('nfs','NFS','storage',(2049,),'tcp'),
    ('smb','SMB / CIFS','storage',(445,),'tcp'),
    ('rsync','Rsync','storage',(873,),'tcp'),
    ('ftp','FTP','storage',(21,),'tcp'),
    ('sftp','SFTP over SSH','storage',(22,),'tcp'),
    ('webdav','WebDAV','storage',(80,443),'tcp'),
    ('minio','MinIO','storage',(9000,9001),'tcp'),
    ('ceph','Ceph monitor','storage',(3300,6789),'tcp'),
    ('cephrgw','Ceph Object Gateway','storage',(7480,),'tcp'),
    ('swift','OpenStack Swift','storage',(8080,),'tcp'),
    ('seaweedfs','SeaweedFS','storage',(9333,8888),'tcp'),
    ('webhdfs','WebHDFS','storage',(9870,50070),'tcp'),
    ('hdfs','Hadoop HDFS NameNode','storage',(8020,9000),'tcp'),
    ('yarn','Hadoop YARN','storage',(8088,),'tcp'),
    ('ipfs','IPFS Kubo API','storage',(5001,),'tcp'),
    ('synology','Synology DSM','storage',(5000,5001),'tcp'),
    ('qnap','QNAP QTS','storage',(8080,443),'tcp'),
    ('modbus','Modbus TCP','ot',(502,),'tcp'),
    ('bacnet','BACnet/IP','ot',(47808,),'udp'),
    ('s7','Siemens S7 over ISO-on-TCP','ot',(102,),'tcp'),
    ('opcua','OPC UA TCP','ot',(4840,),'tcp'),
    ('ethernetip','EtherNet/IP explicit messaging','ot',(44818,),'tcp'),
    ('cipio','EtherNet/IP I/O','ot',(2222,),'udp'),
    ('dnp3','DNP3','ot',(20000,),'tcp'),
    ('iec104','IEC 60870-5-104','ot',(2404,),'tcp'),
    ('iec61850','IEC 61850 MMS','ot',(102,),'tcp'),
    ('fins','Omron FINS','ot',(9600,),'udp'),
    ('melsec','Mitsubishi MELSEC / SLMP','ot',(5007,),'udp'),
    ('ads','Beckhoff ADS','ot',(48898,),'tcp'),
    ('fox','Niagara Fox','ot',(1911,4911),'tcp'),
    ('hartip','HART-IP','ot',(5094,),'udp'),
    ('codesys','CODESYS gateway','ot',(2455,1217),'tcp'),
    ('proconos','ProConOS','ot',(20547,),'tcp'),
    ('pcworx','Phoenix Contact PC Worx','ot',(1962,),'tcp'),
    ('srtp','GE SRTP','ot',(18245,),'tcp'),
    ('plc','PLC device classification','ot',(),'tcp'),
    ('lantronix','Lantronix TCP setup','ot',(9999,),'tcp'),
    ('lantronix-config','Lantronix UDP setup / discovery','ot',(30718,),'udp'),
    ('smux','SNMP SMUX','ot',(199,),'tcp'),
    ('vertx-edge','HID VertX / EDGE discovery','ot',(4070,),'udp'),
    ('grpc','gRPC over HTTP/2','messaging',(),'tcp'),
    ('mqtt','MQTT','messaging',(1883,8883),'tcp'),
    ('amqp','AMQP','messaging',(5672,5671),'tcp'),
    ('kafka','Apache Kafka','messaging',(9092,9093),'tcp'),
    ('nats','NATS','messaging',(4222,8222),'tcp'),
    ('stomp','STOMP','messaging',(61613,61614),'tcp'),
    ('activemq','Apache ActiveMQ','messaging',(61616,8161),'tcp'),
    ('pulsar','Apache Pulsar','messaging',(6650,8080),'tcp'),
    ('zookeeper','Apache ZooKeeper','messaging',(2181,),'tcp'),
    ('etcd','etcd','messaging',(2379,),'tcp'),
    ('consul','Consul','messaging',(8500,8501),'tcp'),
    ('angular', 'Angular application shell', 'web', (80, 443), 'tcp'),
    ('amazons3be', 'S3-compatible listing', 'storage', (80, 443), 'tcp'),
    ('amazonbe', 'S3 references', 'storage', (80, 443), 'tcp'),
    ('gitlab', 'GitLab', 'web', (80, 443), 'tcp'),
    ('dirs', 'Directory listings', 'storage', (80, 443), 'tcp'),
    ('jenkins', 'Jenkins', 'web', (8080,), 'tcp'),
    ('sonarqube', 'SonarQube', 'web', (9000,), 'tcp'),
    ('kibana', 'Kibana', 'web', (5601,), 'tcp'),
    ('grafana', 'Grafana', 'web', (3000,), 'tcp'),
    ('prometheus', 'Prometheus', 'web', (9090,), 'tcp'),
    ('swagger', 'Swagger UI', 'web', (80, 443), 'tcp'),
    ('mongoexpress', 'Mongo Express', 'web', (8081,), 'tcp'),
    ('keys', 'Key indicators', 'web', (80, 443), 'tcp'),
    ('nexus', 'Nexus Repository Manager', 'web', (8081,), 'tcp'),
    ('artifactory', 'Artifactory', 'web', (8081, 8082), 'tcp'),
    ('registry', 'Container registry', 'storage', (5000,), 'tcp'),
    ('rabbitmq', 'RabbitMQ Management', 'messaging', (15672,), 'tcp'),
    ('solr', 'Solr Admin', 'database', (8983,), 'tcp'),
    ('gitea', 'Gitea', 'web', (3000,), 'tcp'),
    ('gogs', 'Gogs', 'web', (3000,), 'tcp'),
    ('azureblob', 'Azure Blob listings', 'storage', (80, 443), 'tcp'),
    ('gcsbucket', 'Google Cloud Storage responses', 'storage', (80, 443), 'tcp'),
    ('rtsp','RTSP cameras / media','iot',(554,8554),'tcp'),
    ('onvif','ONVIF camera / device services','iot',(80,443,8000,8080),'tcp'),
    ('upnp','UPnP device descriptions','iot',(80,49152,49153),'tcp'),
    ('coap','CoAP constrained devices','iot',(5683,5684),'udp'),
    ('homeassistant','Home Assistant','iot',(8123,),'tcp'),
    ('openhab','openHAB','iot',(8080,8443),'tcp'),
    ('nodered','Node-RED','iot',(1880,),'tcp'),
    ('thingsboard','ThingsBoard','iot',(8080,),'tcp'),
    ('tasmota','Tasmota','iot',(80,),'tcp'),
    ('esphome','ESPHome web devices','iot',(80,6052),'tcp'),
    ('shelly','Shelly devices','iot',(80,),'tcp'),
    ('octoprint','OctoPrint','iot',(5000,),'tcp'),
    ('domoticz','Domoticz','iot',(8080,),'tcp'),
    ('zigbee2mqtt','Zigbee2MQTT frontend','iot',(8080,),'tcp'),
)
_ALIASES={'g-rpc':'grpc','grpcs':'grpc','ethernetip-1':'ethernetip','niagara-fox':'fox','ge-srtp':'srtp','hart-ip':'hartip','iec 60870-5-104':'iec104','lantronix_config':'lantronix-config','postgres':'postgresql','pgsql':'postgresql','mongodb':'mongo','elasticsearch':'elastic','sqlserver':'mssql','rethinkdb':'rethink','modbus-tcp':'modbus','opc-ua':'opcua','bacnet-ip':'bacnet','siemens-s7':'s7','ethernet-ip':'ethernetip','iec-104':'iec104','nfs4':'nfs','cifs':'smb','home-assistant':'homeassistant','node-red':'nodered','zigbee-2-mqtt':'zigbee2mqtt'}


def _queries(ports):
    if not ports:
        return {}
    return {
        'shodan':'port:'+','.join(str(p) for p in ports),
        'zoomeye':('('+' || '.join(f'port="{p}"' for p in ports)+')') if len(ports)>1 else f'port="{ports[0]}"',
        'netlas':'port:('+' OR '.join(str(p) for p in ports)+')' if len(ports)>1 else f'port:{ports[0]}',
    }


CATEGORY_LABELS = {
    'database': 'Databases', 'storage': 'Data storage', 'web': 'Web / applications',
    'ot': 'ICS / SCADA / OT', 'iot': 'IoT / connected devices',
    'messaging': 'Messaging / coordination',
}


_ENTRIES += tuple((key,label,'ot',(80,443),'tcp') for key,(label,terms,source) in HMI_DEFINITIONS.items()) + (('vnc','VNC / RFB remote display','ot',(5900,),'tcp'),)


def _catalog_record(key, label, category, ports, transport):
    identity=identity_capability(key)
    queries = _queries(ports)
    fingerprint = {provider: fingerprint_query(key, provider) for provider in PROVIDERS}
    variants = {
        'fingerprint': fingerprint,
        'nonstandard': {provider: nonstandard_query(dto, provider, ports) for provider, dto in fingerprint.items()},
        'ports': {provider: {'query': queries.get(provider, ''), 'supported': bool(ports),
            'reason': ('Port-only candidate: a conventional listening port does not identify the service.' if ports else
                       'This profile has no universal reference port; use its all-indexed-ports query.'),
            'basis': [SYNTAX_SOURCES[provider]]} for provider in PROVIDERS},
    }
    categories = [category]
    if key == 'mqtt':
        categories += ['iot', 'ot']
    elif key in ('nodered', 'thingsboard'):
        categories.append('ot')
    fp = FINGERPRINTS.get(key)
    return {
        'key': key, 'label': label, 'category': category,
        'category_label': CATEGORY_LABELS[category], 'categories': categories,
        'ports': list(ports), 'transport': transport, 'queries': queries,
        'query_variants': variants,
        'fingerprint_summary': fp.summary if fp else fingerprint['shodan']['reason'],
        'http_capture': key in HTTP_CAPTURE_TYPES,
        'identity': key in IDENTITY_TYPES, 'inspection': False, 'export': False,
        'identity_operation':identity['operation'],'identity_protocol':identity['protocol'],
        'identity_transports':identity['transports'],'identity_limitation':identity['limitation'],
        'identity_sources':identity['sources'],
        'detection_confidence': 'candidate',
        'detection_notes': 'Port-only presets are candidates. Fingerprint presets match indexed response evidence and still require live confirmation; provider scan coverage, authentication and compatible implementations limit identification.',
    }


def catalog():
    """Return fresh candidate records and truthful per-provider query support."""
    records = [_catalog_record(*entry) for entry in _ENTRIES]
    from .device_profiles import DEVICE_PROFILES
    base = {item['key']: item for item in records}
    for key, profile in DEVICE_PROFILES.items():
        record = deepcopy(base[profile['service_type']])
        fingerprints = deepcopy(profile['queries'])
        unavailable = {p: {'query':'','supported':False,'reason':'A port alone cannot identify this vendor or model. Use response fingerprints.','basis':[]} for p in PROVIDERS}
        record.update(key=key, label=profile['label'], base_type=profile['service_type'],
            query_variants={'fingerprint':fingerprints,
                'nonstandard':{p:nonstandard_query(dto,p,record['ports']) for p,dto in fingerprints.items()},
                'ports':unavailable}, queries={p:dto['query'] for p,dto in fingerprints.items() if dto['supported']}, fingerprint_summary=profile['summary'], preset_kind='builtin')
        records.append(record)
    for record in records:
        record['presentation'] = presentation_for(record['key'])
    return records


def catalog_entry(key):
    key=str(key or '').strip().lower()
    key=_ALIASES.get(key,key)
    for entry in catalog():
        if entry['key']==key:return deepcopy(entry)
    raise ValueError('Unknown protocol or technology catalog entry')


def profile_query(key, provider, mode='fingerprint'):
    """Select a fresh query DTO without silently falling back to port presets."""
    provider = str(provider or '').strip().lower()
    mode = str(mode or '').strip().lower()
    if provider not in PROVIDERS:
        raise ValueError('Unknown search provider')
    if mode not in ('fingerprint', 'nonstandard', 'ports'):
        raise ValueError('Unknown discovery query mode')
    return deepcopy(catalog_entry(key)['query_variants'][mode][provider])
