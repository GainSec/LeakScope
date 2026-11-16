import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PostgresStorageTests(unittest.TestCase):
    def postgres_mount(self, override=None, native=False):
        with tempfile.TemporaryDirectory() as directory:
            envfile = Path(directory) / 'fixture.env'
            envfile.write_text('LEAKSCOPE_DB_PASSWORD=fixture\nLEAKSCOPE_FIRECRAWL_PASSWORD=fixture\n')
            env = dict(os.environ, LEAKSCOPE_DB_PASSWORD='fixture',
                       LEAKSCOPE_FIRECRAWL_PASSWORD='fixture', LEAKSCOPE_SEARXNG_SECRET='fixture', LEAKSCOPE_POSTGRES_NATIVE_VOLUME='fixture-native', LEAKSCOPE_DATA_ROOT='/tmp/fixture-app-data')
            env.pop('LEAKSCOPE_POSTGRES_DATA_ROOT', None)
            if override is not None:
                env['LEAKSCOPE_POSTGRES_DATA_ROOT'] = override
            args = ['docker', 'compose', '--env-file', str(envfile),
                '-f', str(ROOT / 'docker-compose.yml'), '-f', str(ROOT / 'deploy/compose.postgres.yml'),
                'config', '--format', 'json']
            if native:
                args[args.index('config'):args.index('config')] = ['-f', str(ROOT / 'deploy/compose.postgres-native.yml')]
            result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, 'Fixture Compose configuration must resolve')
            config = json.loads(result.stdout)
            return next(m for m in config['services']['postgres']['volumes'] if m['target'] == '/var/lib/postgresql')

    def test_default_stays_under_application_data_root(self):
        mount = self.postgres_mount()
        self.assertEqual(mount['source'], '/tmp/fixture-app-data/postgres')
        self.assertFalse(mount['bind']['create_host_path'])

    def test_database_can_use_independent_internal_path_with_spaces(self):
        path = '/Users/fixture/Library/Application Support/LeakScope/postgres'
        mount = self.postgres_mount(path)
        self.assertEqual(mount['source'], path)
        self.assertEqual(mount['type'], 'bind')
        self.assertFalse(mount['bind']['create_host_path'])

    def test_optional_native_volume_replaces_bind_and_retains_parent_mount(self):
        mount = self.postgres_mount(native=True)
        self.assertEqual(mount['type'], 'volume')
        self.assertEqual(mount['source'], 'postgres_native')
        self.assertNotIn('bind', mount)
