import os
import tempfile
os.environ.setdefault('LEAKSCOPE_SECRET_KEY', 'leakscope-unit-test-secret-not-for-deployment')
os.environ['LEAKSCOPE_CONFIG_FILE'] = '/nonexistent/leakscope-test-config.json'
os.environ['SHODAN_API_KEY'] = ''
os.environ['ZOOMEYE_API_KEY'] = ''
from .settings import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {}
STATIC_ROOT = Path(tempfile.gettempdir()) / 'leakscope-test-static'
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

TEST_RUNNER = "leakscope.testing.SafeDiscoverRunner"
LEAKSCOPE_NORMALIZE_LEGACY = False
LEAKSCOPE_PROVIDER_THROTTLE = False
