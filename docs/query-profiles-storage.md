# Storage and messaging query definitions

Source: `leakscope/phase3/profiles_storage.py`; compiled by the shared fingerprint engine. Eight profiles supply24 provider definitions. These are indexed candidates, not24 verified native protocol adapters. Default queries contain no port inclusion/exclusion and no required provider product taxonomy.

| Profile | Shodan | ZoomEye | Netlas | Evidence limit |
| --- | --- | --- | --- | --- |
| NFS | Documented portmapper modules plus NFS RPC program100003 | `service="nfs"` | `prot7:nfs` | Shodan returns a mapper advertisement, not a verified native NFS listener. Protocol index/alias coverage is not live-verified. |
| SMB | `shodan.module:smb` | `service="smb"` | Existing `smb.smbv1_support:*` | Does not establish share permissions; both true/false support values remain included. |
| SFTP | SSH scanner module | SSH service classification | Decoded SSH server ID | SSH family candidate only. SFTP subsystem is not verified. |
| Ceph | Exact `ceph v027` wire banner | Messenger v1 or v2 prefix | Raw printable messenger v1 or v2 prefix | Shodan preset misses v2-only records; not restricted to monitor processes. |
| HDFS | NameNode HTTP title and `dfshealth.js` response | Same response form | Same response form | HTTP companion, not a native RPC listener or readable-files claim. |
| AMQP | AMQP scanner module | AMQP service classification | Existing decoded version AND platform fields | Does not verify a specific broker implementation. |
| Kafka | Kafka scanner module | Kafka service classification | `prot7:kafka` | Native protocol candidate; provider index/alias coverage is not live-verified. |
| Pulsar | Two Pulsar Prometheus metric names in HTTP body | Same response form | Same response form | HTTP metrics companion, not the binary broker listener. Metrics depend on configuration and indexed paths. |

The Shodan scanner IDs and accepted `shodan.module` filter come from the root's shared free metadata snapshots. Netlas fields and ZoomEye service syntax come from shared official references. Ceph's authoritative `src/include/msgr.h` defines `CEPH_BANNER` and `CEPH_BANNER_V2_PREFIX`; the root fetched this once and shared it. The Pulsar reference documents `pulsar_rate_in` and `pulsar_subscriptions_count`. Existing HDFS companion evidence is reused from the already defined WebHDFS/NameNode profile.

Primary source URLs are retained in each definition. Additional distinct URLs fetched by this agent: the Pulsar metrics reference and a Ceph documentation path that returned404 (one same-URL retry also failed). No provider API, result search, Firecrawl/LLM endpoint or target-probe calls were made by this agent. No broad web search queries were run.

`tests/test_query_profiles_storage.py` checks support, absence of default port/product constraints, exact preserved decoded fields, protocol-family caveats, HTTP-companion caveats and explicit nonstandard exclusions. The original central tests failed before the new data definitions. Seven tests then passed with the definitions registered in an isolated process; integration must rerun them after the root registers the module.
