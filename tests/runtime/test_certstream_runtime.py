from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]


class CertstreamRuntimeTests(unittest.TestCase):
    def test_builtin_and_ingestor_are_default_internal_services(self):
        text=(ROOT/'docker-compose.yml').read_text()
        self.assertIn('  certstream:',text)
        self.assertIn('  certstream-ingestor:',text)
        section=text.split('  certstream:',1)[1].split('  certstream-ingestor:',1)[0]
        self.assertNotIn('profiles:',section)
        self.assertNotIn('ports:',section)
        self.assertIn('mem_limit:',section)
        self.assertIn('cpus:',section)
        self.assertIn('create_host_path: false',section)

    def test_server_build_is_pinned_and_tests_recovery_patch(self):
        text=(ROOT/'deploy/certstream/Dockerfile').read_text()
        self.assertIn('@sha256:',text)
        self.assertIn('--checksum=sha256:',text)
        self.assertIn('v1.10.1',text)
        self.assertIn('go test',text)
        self.assertIn('recovery',text)

    def test_recovery_config_persists_checkpoints(self):
        text=(ROOT/'deploy/certstream/config.yaml').read_text()
        self.assertIn('enabled: true',text)
        self.assertIn('/data/certstream/ct_index.json',text)
        self.assertIn('stream_processing: []',text)
