# Database response and protocol candidates

`leakscope/phase3/profiles_database.py` adds22 profile definitions. Every default query is independent of conventional ports and provider product taxonomy. These are indexed candidates, not verified engine identities or proof that a provider currently has matching records.

| Evidence | Profiles / limits |
| --- | --- |
| Exact Shodan scanner modules from the current published protocol map | MongoDB, MS-SQL, Oracle TNS, Cassandra, Firebird, DB2 DRDA, Riak. Classification describes the protocol/module that collected evidence, not a fresh connection. |
| Compatible protocol family | ScyllaDB searches CQL; TimescaleDB, Vertica and CrateDB search PostgreSQL-compatible responses. Other engines match; these do not identify the requested engine or an installed extension. |
| ZoomEye service / Netlas protocol or raw heuristic | Native database protocol candidates, including rarer proprietary protocols. Exact indexed names and scanner coverage vary. A syntactically implemented query can return zero, particularly where the provider lacks that scanner. |
| Retained stronger Netlas decoding | MongoDB build-info, SQL Server prelogin version/encryption and Oracle TNS accepted/refused handshake fields. |
| HTTP title companion | Pinot, Trino, Presto and OpenTSDB search their name in indexed HTTP titles. Matching branding/management pages do not prove a reachable native database; unrelated pages with the same title can match. |
| VictoriaMetrics root response | Actual root handler emits `Single-node VictoriaMetrics` and a `vmui` link. Both body terms are required; cluster frontends/custom roots may not match. |

Six Shodan gaps remain explicit: Aerospike, SAP HANA, MaxDB, Informix, MonetDB and Milvus have no exact native module in the fetched protocol map, and no independent Shodan response query was established in this bounded pass. They are not substituted with guessed product labels or generic HTTP transports.

Research reused the shared Shodan filter/protocol maps, Netlas response fields and ZoomEye API documentation. Two additional primary URLs were consulted: the [Shodan Milvus schema](https://datapedia.shodan.io/property/milvus.html) and [VictoriaMetrics root handler](https://raw.githubusercontent.com/VictoriaMetrics/VictoriaMetrics/master/app/victoria-metrics/main.go). No provider result/API calls, model/Firecrawl calls or target probes were made by this database task.

Tests in `tests/test_query_profiles_database.py` cover exact module mapping, all-port defaults, protocol-family disclosure, preservation of stronger existing decoded queries, HTTP root evidence and honest unsupported Shodan cases. The root integration registers the data definitions in the shared Fingerprint engine.
