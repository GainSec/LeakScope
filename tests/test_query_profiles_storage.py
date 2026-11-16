"""Storage/messaging discovery predicates and honest protocol-family limits."""
import re
from django.test import SimpleTestCase
from leakscope.phase3.catalog import profile_query


class StorageQueryProfileTests(SimpleTestCase):
    def test_new_profiles_have_port_independent_queries_with_primary_basis(self):
        for key in ('nfs', 'sftp', 'ceph', 'hdfs', 'kafka', 'pulsar'):
            for provider in ('shodan', 'zoomeye', 'netlas'):
                with self.subTest(key=key, provider=provider):
                    row = profile_query(key, provider)
                    self.assertTrue(row['supported'], row['reason'])
                    self.assertTrue(row['query'])
                    self.assertNotRegex(row['query'], r'\bport\s*[:=]')
                    self.assertNotRegex(row['query'], r'\bproduct\s*[:=]')
                    self.assertTrue(row['basis'])

    def test_native_provider_modules_and_decoded_fields_are_preserved(self):
        for key in ('smb', 'amqp', 'kafka'):
            self.assertEqual(profile_query(key, 'shodan')['query'], 'shodan.module:' + key)
            self.assertEqual(profile_query(key, 'zoomeye')['query'], 'service="' + key + '"')
        self.assertEqual(profile_query('smb', 'netlas')['query'], 'smb.smbv1_support:*')
        self.assertEqual(profile_query('amqp', 'netlas')['query'], 'amqp.version:* AND amqp.platform:*')

    def test_sftp_query_is_explicitly_an_ssh_candidate_not_subsystem_verification(self):
        for provider in ('shodan', 'zoomeye', 'netlas'):
            row = profile_query('sftp', provider)
            self.assertTrue(row['supported'])
            self.assertIn('SSH', row['reason'])
            self.assertIn('SFTP subsystem is not verified', row['reason'])
            self.assertNotIn('sftp:', row['query'])

    def test_hdfs_and_pulsar_are_specific_http_companion_responses(self):
        for provider in ('shodan', 'zoomeye', 'netlas'):
            hdfs = profile_query('hdfs', provider)
            pulsar = profile_query('pulsar', provider)
            self.assertIn('dfshealth.js', hdfs['query'])
            self.assertIn('HTTP companion', hdfs['reason'])
            self.assertIn('not a native RPC', hdfs['reason'])
            self.assertIn('pulsar_rate_in', pulsar['query'])
            self.assertIn('pulsar_subscriptions_count', pulsar['query'])
            self.assertIn('HTTP metrics', pulsar['reason'])

    def test_ceph_matches_actual_v1_wire_banner_with_explicit_recall_limit(self):
        for provider in ('shodan', 'zoomeye', 'netlas'):
            row = profile_query('ceph', provider)
            self.assertIn('ceph v027', row['query'])
            self.assertIn('messenger v1', row['reason'])
            self.assertIn('v2-only', row['reason'])
            self.assertNotIn('http.', row['query'])

    def test_nonstandard_mode_only_excludes_defaults_after_the_response_predicate(self):
        for key in ('sftp', 'ceph', 'hdfs', 'kafka', 'pulsar', 'smb', 'amqp'):
            for provider in ('shodan', 'zoomeye', 'netlas'):
                base = profile_query(key, provider)
                nonstandard = profile_query(key, provider, 'nonstandard')
                self.assertTrue(nonstandard['supported'])
                self.assertIn(base['query'], nonstandard['query'])
                self.assertNotEqual(base['query'], nonstandard['query'])

    def test_nfs_mapper_is_advertisement_evidence_and_other_protocols_are_candidates(self):
        row = profile_query('nfs', 'shodan')
        self.assertIn('100003', row['query'])
        self.assertIn('shodan.module:portmap-tcp,portmap-udp', row['query'])
        self.assertIn('mapper endpoint is not a verified native NFS listener', row['reason'])
        self.assertEqual(profile_query('nfs', 'zoomeye')['query'], 'service="nfs"')
        self.assertEqual(profile_query('nfs', 'netlas')['query'], 'prot7:nfs')
        self.assertIn('not live-verified', profile_query('kafka', 'netlas')['reason'])
