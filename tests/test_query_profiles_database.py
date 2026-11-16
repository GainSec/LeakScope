"""Database query additions use protocol/response evidence independently of ports."""
from django.test import SimpleTestCase
from leakscope.phase3.catalog import profile_query


class DatabaseQueryProfileTests(SimpleTestCase):
    def test_native_scanner_module_queries_cover_existing_provider_gaps(self):
        expected = {'mssql':'ms-sql','oracle':'oracle-tns','mongo':'mongodb',
                    'cassandra':'cassandra','firebird':'firebird',
                    'db2':'ibm-db2-drda','riak':'riak'}
        for key, module in expected.items():
            with self.subTest(key=key):
                dto = profile_query(key, 'shodan')
                self.assertTrue(dto['supported'])
                self.assertEqual(dto['query'], 'shodan.module:' + module)
                self.assertIn('candidate', dto['reason'].lower())

    def test_every_new_database_has_explicit_protocol_candidates_where_fields_exist(self):
        keys = ('mssql','oracle','mongo','cassandra','scylla','aerospike','riak',
                'vertica','db2','hana','maxdb','informix','firebird','monetdb',
                'cratedb','milvus','victoriametrics','pinot','trino','presto',
                'opentsdb','timescaledb')
        for key in keys:
            for provider in ('zoomeye', 'netlas'):
                with self.subTest(key=key, provider=provider):
                    dto = profile_query(key, provider)
                    self.assertTrue(dto['supported'])
                    self.assertTrue(dto['basis'])
                    self.assertNotRegex(dto['query'], r'\b(?:port|product|app|tag\.name)\s*[:=]')
                    self.assertTrue(any(word in dto['reason'].lower()
                                        for word in ('candidate','response','handshake')))

    def test_compatible_protocols_never_claim_engine_or_extension_identification(self):
        for key, family in (('scylla', 'CQL'), ('timescaledb', 'PostgreSQL'),
                            ('vertica', 'PostgreSQL'), ('cratedb', 'PostgreSQL')):
            for provider in ('shodan', 'zoomeye', 'netlas'):
                with self.subTest(key=key, provider=provider):
                    dto = profile_query(key, provider)
                    self.assertTrue(dto['supported'])
                    self.assertIn(family, dto['reason'])
                    self.assertIn('does not identify', dto['reason'])

    def test_existing_netlas_decoded_evidence_is_retained(self):
        for key, marker in [('mongo','mongodb.build_info.version:'),
                            ('mssql','mssql.encrypt_mode:'),
                            ('oracle','oracle.handshake.accept_version:')]:
            dto = profile_query(key,'netlas')
            self.assertIn(marker, dto['query'])

    def test_victoriametrics_matches_actual_root_response_not_default_port(self):
        for provider in ('shodan','zoomeye','netlas'):
            dto = profile_query('victoriametrics', provider)
            self.assertTrue(dto['supported'])
            self.assertIn('Single-node VictoriaMetrics', dto['query'])
            self.assertIn('vmui', dto['query'])
            self.assertNotIn('8428', dto['query'])

    def test_missing_shodan_native_scanners_are_not_faked_with_product_labels(self):
        for key in ('hana','maxdb','informix','monetdb','aerospike'):
            dto = profile_query(key, 'shodan')
            self.assertTrue(dto['supported'])
            self.assertNotIn('product:', dto['query'])
            self.assertNotIn('shodan.module:', dto['query'])
            self.assertNotRegex(dto['query'], r'\bport[:=]')
