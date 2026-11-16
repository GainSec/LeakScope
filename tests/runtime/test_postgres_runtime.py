import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PostgresSettingsTests(unittest.TestCase):
    def database(self, url):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, LEAKSCOPE_DATA_DIR=directory, LEAKSCOPE_SECRET_KEY='fixture-secret', DATABASE_URL=url)
            return subprocess.run([sys.executable, '-c', 'import json;from leakscope.settings import DATABASES;print(json.dumps(DATABASES["default"],default=str))'], cwd=ROOT, env=env, capture_output=True, text=True)

    def test_postgres_url_decodes_credentials_and_ipv6(self):
        result = self.database('postgresql://user%2Bname:secret%40word@[::1]:5433/leakscope?sslmode=require')
        self.assertEqual(result.returncode, 0, result.stderr)
        db = json.loads(result.stdout)
        self.assertEqual(db['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(db['USER'], 'user+name')
        self.assertEqual(db['PASSWORD'], 'secret@word')
        self.assertEqual(db['HOST'], '::1')
        self.assertEqual(db['PORT'], 5433)
        self.assertEqual(db['OPTIONS']['sslmode'], 'require')

    def test_unknown_schemes_and_options_fail_without_echoing_url(self):
        for value in ('mysql://user:private-password@host/db', 'postgresql://user:private-password@host/db?options=bad'):
            result = self.database(value)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('private-password', result.stderr)

    def test_unset_url_retains_sqlite(self):
        result = self.database('')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['ENGINE'], 'django.db.backends.sqlite3')

    def test_postgres_override_is_internal_with_required_mount(self):
        text = (ROOT/'deploy/compose.postgres.yml').read_text()
        self.assertIn('postgres:18.6', text)
        self.assertIn('@sha256:', text)
        self.assertIn('create_host_path: false', text)
        self.assertIn('service_healthy', text)
        self.assertNotIn('ports:', text)


class MigrationToolTests(unittest.TestCase):
    def tool(self):
        path = ROOT/'deploy/postgres/migrate_sqlite.py'
        spec = importlib.util.spec_from_file_location('leakscope_pg_import', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_artifact_file_is_private_and_refuses_overwrite(self):
        tool = self.tool()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'fixture.json'
            tool.write_private(path, b'[]')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                tool.write_private(path, b'replace')
            self.assertEqual(path.read_bytes(), b'[]')

    def test_fixture_excludes_generated_auth_tables_but_keeps_app_ids(self):
        args = self.tool().dump_arguments()
        self.assertIn('--natural-foreign', args)
        self.assertNotIn('--natural-primary', args)
        self.assertIn('contenttypes', args)
        self.assertIn('auth.permission', args)
        self.assertIn('sessions', args)

    def test_import_refuses_nonempty_application_target(self):
        self.assertTrue(self.tool().target_has_data({'leakscope_app.search': 1}))
        self.assertFalse(self.tool().target_has_data({'auth.permission': 120, 'leakscope_app.search': 0}))

    def test_canonical_state_sorts_only_unordered_m2m_memberships(self):
        from types import SimpleNamespace
        model=SimpleNamespace(_meta=SimpleNamespace(many_to_many=[SimpleNamespace(name='observations')]))
        row={'model':'fixture','pk':1,'fields':{'observations':['b','a'],'data':{'ordered':[2,1]}}}
        result=self.tool().canonical_row(model,row)
        self.assertEqual(result['fields']['observations'],['a','b'])
        self.assertEqual(result['fields']['data']['ordered'],[2,1])
