import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class RuntimeSettingsTests(unittest.TestCase):
    def settings(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, LEAKSCOPE_DATA_DIR=directory, LEAKSCOPE_EXPORT_DIR=directory+'/exports', LEAKSCOPE_SECRET_KEY='test-private-secret', DJANGO_DEBUG='0')
            code = '''import json; from leakscope import settings as s; print(json.dumps({'debug':s.DEBUG,'db':str(s.DATABASES['default']['NAME']),'exports':str(s.EXPORT_DIR),'csrf':'django.middleware.csrf.CsrfViewMiddleware' in s.MIDDLEWARE,'static':'whitenoise.middleware.WhiteNoiseMiddleware' in s.MIDDLEWARE,'hosts':s.ALLOWED_HOSTS,'default_auto':s.DEFAULT_AUTO_FIELD,'broker':s.CELERY_BROKER_URL}))'''
            result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
            return json.loads(result.stdout), directory

    def test_private_runtime_defaults(self):
        settings, directory = self.settings()
        self.assertFalse(settings['debug'])
        self.assertEqual(settings['db'], directory+'/db.sqlite3')
        self.assertEqual(settings['exports'], directory+'/exports')
        self.assertTrue(settings['csrf'])
        self.assertTrue(settings['static'])
        self.assertNotIn('*', settings['hosts'])
        self.assertIn('localhost', settings['hosts'])
        self.assertEqual(settings['default_auto'], 'django.db.models.AutoField')

    def test_generated_secret_is_private_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, LEAKSCOPE_DATA_DIR=directory)
            env.pop('LEAKSCOPE_SECRET_KEY', None)
            code = 'import hashlib;from leakscope.settings import SECRET_KEY;print(hashlib.sha256(SECRET_KEY.encode()).hexdigest())'
            first = subprocess.check_output([sys.executable, '-c', code], cwd=ROOT, env=env, text=True)
            second = subprocess.check_output([sys.executable, '-c', code], cwd=ROOT, env=env, text=True)
            self.assertEqual(first, second)
            self.assertEqual((Path(directory)/'django-secret').stat().st_mode & 0o777, 0o600)

    def test_wsgi_bootstraps_the_app_registry(self):
        env = dict(os.environ, DJANGO_SETTINGS_MODULE='leakscope.test_settings')
        result = subprocess.run([sys.executable, '-c', 'from leakscope.wsgi import application; assert callable(application)'], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_export_housekeeping_has_an_hourly_schedule(self):
        env = dict(os.environ, LEAKSCOPE_SECRET_KEY='test-private-secret')
        code = "from leakscope.settings import CELERY_BEAT_SCHEDULE as s;entry=s['export-housekeeping'];assert entry['task']=='leakscope.tasks.cleanup_exports';assert entry['schedule'].total_seconds()==3600"
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_test_static_directory_is_outside_the_readonly_app(self):
        code = "from leakscope.test_settings import STATIC_ROOT,BASE_DIR;assert not STATIC_ROOT.is_relative_to(BASE_DIR);assert STATIC_ROOT.is_dir()"
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_compose_does_not_publish_redis_or_bake_secrets(self):
        text = (ROOT/'docker-compose.yml').read_text()
        self.assertIn('service_completed_successfully', text)
        self.assertIn('create_host_path: false', text)
        self.assertIn('/run/secrets/provider_config', text)
        self.assertIn('profiles: [scheduler]', text)
        dockerignore=(ROOT/'.dockerignore').read_text()
        self.assertIn('config.json', dockerignore)
        self.assertIn('.runtime', dockerignore)

if __name__ == '__main__':
    unittest.main()
