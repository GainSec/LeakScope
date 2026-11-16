"""Storage and messaging query definitions; no searches or network effects.

Provider protocol classification is indexed candidate evidence. An SSH service
cannot establish an SFTP subsystem, and an administrative HTTP companion is not
the native storage/message listener. Every query is independent of default ports.
"""

SHODAN_API = 'https://developer.shodan.io/api'
ZOOMEYE_API = 'https://github.com/zoomeye-ai/ZoomEye-python/blob/master/api.md'
NETLAS_FIELDS = 'https://docs.netlas.io/knowledge-base/field-reference/responses/'

PROFILES = {
    'nfs': {
        'summary': (
            'Provider-classified NFS candidate; protocol index/alias coverage is not live-verified. '
            'The Shodan query instead selects an RPC portmapper advertising NFS program 100003: '
            'that mapper endpoint is not a verified native NFS listener.'
        ),
        'signals': (),
        'sources': (SHODAN_API, ZOOMEYE_API, NETLAS_FIELDS, 'https://www.rfc-editor.org/rfc/rfc1813'),
        'overrides': (
            ('shodan', 'shodan.module:portmap-tcp,portmap-udp "100003"'),
            ('zoomeye', 'service="nfs"'),
            ('netlas', 'prot7:nfs'),
        ),
    },
    'smb': {
        'summary': (
            'SMB protocol-classified response candidate on any indexed port; '
            'Netlas retains its decoded version-support predicate. Provider index/alias '
            'coverage is not live-verified; matching does not establish accessible shares.'
        ),
        'signals': (),
        'sources': (SHODAN_API, ZOOMEYE_API, NETLAS_FIELDS),
        'overrides': (
            ('shodan', 'shodan.module:smb'),
            ('zoomeye', 'service="smb"'),
            ('netlas', 'smb.smbv1_support:*'),
        ),
    },
    'sftp': {
        'summary': (
            'SSH protocol-family candidate for possible SFTP; the SFTP subsystem is not verified. '
            'All SSH implementations can match whether or not file transfer is enabled. '
            'Provider index/alias coverage is not live-verified.'
        ),
        'signals': (),
        'sources': (SHODAN_API, ZOOMEYE_API, NETLAS_FIELDS),
        'overrides': (
            ('shodan', 'shodan.module:ssh'),
            ('zoomeye', 'service="ssh"'),
            ('netlas', 'ssh.server_id.raw:*'),
        ),
    },
    'ceph': {
        'summary': (
            'Ceph messenger v1/v2 wire-banner candidate; not restricted to monitor daemons. '
            'Shodan uses the exact v1 banner and therefore misses v2-only services; '
            'ZoomEye/Netlas also match the documented v2 prefix when indexed.'
        ),
        'signals': (),
        'sources': ('https://raw.githubusercontent.com/ceph/ceph/main/src/include/msgr.h', NETLAS_FIELDS, ZOOMEYE_API),
        'overrides': (
            ('shodan', '"ceph v027"'),
            ('zoomeye', '(banner="ceph v027" || banner="ceph v2")'),
            ('netlas', '(raw.strings:"ceph v027" OR raw.strings:"ceph v2")'),
        ),
    },
    'hdfs': {
        'summary': (
            'HDFS NameNode HTTP companion: Hadoop Administration title plus dfshealth.js '
            'response content, not a native RPC listener or proof that files can be read. '
            'Only indexed web interfaces carrying these assets match.'
        ),
        'signals': (('title', 'Hadoop Administration'), ('body', 'dfshealth.js')),
        'sources': ('https://github.com/apache/hadoop/blob/trunk/hadoop-hdfs-project/hadoop-hdfs/src/main/webapps/hdfs/dfshealth.html',),
        'overrides': (),
    },
    'amqp': {
        'summary': (
            'AMQP protocol-classified response candidate; Netlas requires decoded version '
            'and platform server properties. Provider index/alias coverage is not '
            'live-verified; this does not identify a specific broker implementation.'
        ),
        'signals': (),
        'sources': (SHODAN_API, ZOOMEYE_API, NETLAS_FIELDS),
        'overrides': (
            ('shodan', 'shodan.module:amqp'),
            ('zoomeye', 'service="amqp"'),
            ('netlas', 'amqp.version:* AND amqp.platform:*'),
        ),
    },
    'kafka': {
        'summary': (
            'Provider-classified Kafka protocol candidate; Shodan has a documented Kafka '
            'scanner module. Other provider protocol index/alias coverage is not live-verified. '
            'A match does not establish topic permissions or current broker reachability.'
        ),
        'signals': (),
        'sources': (SHODAN_API, ZOOMEYE_API, NETLAS_FIELDS),
        'overrides': (
            ('shodan', 'shodan.module:kafka'),
            ('zoomeye', 'service="kafka"'),
            ('netlas', 'prot7:kafka'),
        ),
    },
    'pulsar': {
        'summary': (
            'Pulsar HTTP metrics companion containing pulsar_rate_in and '
            'pulsar_subscriptions_count; not the binary broker listener. Only indexed '
            'metrics responses match, and enabled metrics depend on broker configuration.'
        ),
        'signals': (('body', 'pulsar_rate_in'), ('body', 'pulsar_subscriptions_count')),
        'sources': ('https://pulsar.apache.org/docs/next/reference-metrics/',),
        'overrides': (),
    },
}
