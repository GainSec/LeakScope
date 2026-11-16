"""Runtime settings for the private, login-free LeakScope workbench."""
from datetime import timedelta
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs, unquote, urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('LEAKSCOPE_DATA_DIR', BASE_DIR / '.runtime' / 'app'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR = Path(os.environ.get('LEAKSCOPE_EXPORT_DIR', DATA_DIR.parent / 'artifacts' / 'exports'))
LEAKSCOPE_EXPORT_DIR = EXPORT_DIR
LEAKSCOPE_CONFIG_FILE = os.environ.get('LEAKSCOPE_CONFIG_FILE', str(BASE_DIR / 'config.json'))
EXPORT_TTL_SECONDS = int(os.environ.get('LEAKSCOPE_EXPORT_TTL_SECONDS', '86400'))
LEAKSCOPE_EXPORT_TTL_SECONDS = EXPORT_TTL_SECONDS


def _private_secret():
    configured = os.environ.get('LEAKSCOPE_SECRET_KEY')
    if configured:
        return configured
    path = DATA_DIR / 'django-secret'
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    value = secrets.token_urlsafe(64)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(value)
    return value


SECRET_KEY = _private_secret()
DEBUG = os.environ.get('DJANGO_DEBUG', '').lower() in {'1', 'true', 'yes'}
ALLOWED_HOSTS = [host.strip() for host in os.environ.get(
    'DJANGO_ALLOWED_HOSTS',
    'localhost,127.0.0.1,[::1],host.docker.internal'
).split(',') if host.strip()]
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get(
    'CSRF_TRUSTED_ORIGINS',
    'http://localhost:2627,http://127.0.0.1:2627'
).split(',') if origin.strip()]

INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages',
    'leakscope_app', 'django.contrib.staticfiles',
]
MIDDLEWARE = [
    'leakscope.runtime_diagnostics.RuntimeDiagnosticsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
LEAKSCOPE_SLOW_REQUEST_MS = 1000
ROOT_URLCONF = 'leakscope.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.debug',
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
WSGI_APPLICATION = 'leakscope.wsgi.application'
def _database_config():
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'db.sqlite3', 'OPTIONS': {'timeout': 30}}
    try:
        parsed = urlsplit(url)
        name = unquote(parsed.path.lstrip('/'))
        if parsed.scheme not in {'postgres', 'postgresql'} or not parsed.hostname or not name or '/' in name or parsed.fragment:
            raise ValueError
        parameters = parse_qs(parsed.query, strict_parsing=True)
        if set(parameters) - {'sslmode', 'connect_timeout'} or any(len(values) != 1 for values in parameters.values()):
            raise ValueError
        options = {'connect_timeout': 10}
        if 'sslmode' in parameters:
            mode = parameters['sslmode'][0]
            if mode not in {'disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'}:
                raise ValueError
            options['sslmode'] = mode
        if 'connect_timeout' in parameters:
            timeout = int(parameters['connect_timeout'][0])
            if not 1 <= timeout <= 60:
                raise ValueError
            options['connect_timeout'] = timeout
        return {'ENGINE': 'django.db.backends.postgresql', 'NAME': name,
                'USER': unquote(parsed.username or ''), 'PASSWORD': unquote(parsed.password or ''),
                'HOST': parsed.hostname, 'PORT': parsed.port or 5432,
                'OPTIONS': options, 'CONN_MAX_AGE': 60, 'CONN_HEALTH_CHECKS': True}
    except (ValueError, TypeError):
        # Connection strings can contain secrets; never reflect them in errors.
        raise ValueError('DATABASE_URL must be a valid PostgreSQL URL with supported connection options') from None


DATABASES = {'default': _database_config()}
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = Path(os.environ.get('LEAKSCOPE_STATIC_ROOT', BASE_DIR / 'staticfiles'))
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}
WHITENOISE_USE_FINDERS = DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', CELERY_BROKER_URL)
CELERY_BROKER_TRANSPORT_OPTIONS = {'visibility_timeout': 3600}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_SOFT_TIME_LIMIT = 1200
CELERY_TASK_TIME_LIMIT = 1260
CELERY_RESULT_EXPIRES = 86400
CELERY_IMPORTS = ('leakscope.tasks',)
CELERY_IMPORTS += ('leakscope.phase3.jobs',)
CELERY_IMPORTS += ('leakscope.phase3.cloud_jobs',)
CELERY_IMPORTS += ('leakscope.phase3.asset_jobs',)
CELERY_IMPORTS += ('leakscope.phase3.domain_expansion',)
CELERY_IMPORTS += ('leakscope.phase3.ics_jobs',)
CELERY_IMPORTS += ('leakscope.phase3.local_correlations',)
CELERY_IMPORTS += ('leakscope.phase3.gallery_jobs',)
CELERY_IMPORTS += ('leakscope.phase3.evidence_jobs',)
CELERY_IMPORTS += ('leakscope.build.engine',)
CELERY_TASK_ROUTES = {
    'leakscope.ics.related':{'queue':'discovery'},
    'leakscope.ics.inspect':{'queue':'fetch'},
    'leakscope.ics.scan':{'queue':'fetch'},
    'leakscope.ics.web_probe':{'queue':'fetch'},
    'leakscope.phase3.run_asset_enrichment': {'queue':'discovery'},
    'leakscope.phase3.capture_watch_certificate': {'queue':'fetch'},
    'leakscope.phase3.run_cloud': {'queue': 'discovery'},
    'leakscope.phase3.run_lookup': {'queue': 'discovery'},
    'leakscope.build.advance': {'queue': 'discovery'},
    'leakscope.build.tick': {'queue': 'discovery'},
    'leakscope.phase3.enrich_endpoint': {'queue': 'discovery'},
    'leakscope.phase3.fetch_endpoint': {'queue': 'fetch'},
    'leakscope.phase3.capture_health': {'queue': 'fetch'},
}
LEAKSCOPE_NORMALIZE_LEGACY = os.environ.get('LEAKSCOPE_NORMALIZE_LEGACY', '1').lower() in {'1', 'true', 'yes'}
LEAKSCOPE_PROVIDER_THROTTLE = os.environ.get('LEAKSCOPE_PROVIDER_THROTTLE', '1').lower() in {'1', 'true', 'yes'}
CELERY_BEAT_SCHEDULE = {
    'capture-housekeeping': {'task': 'leakscope.phase3.cleanup_captures', 'schedule': timedelta(hours=1)},
    'scheduled-lookups': {'task': 'leakscope.phase3.dispatch_due', 'schedule': timedelta(seconds=60)},
    'workflow-starts': {'task': 'leakscope.build.tick', 'schedule': timedelta(seconds=60)},
    'capture-worker-health': {'task': 'leakscope.phase3.capture_health', 'schedule': timedelta(seconds=60)},
    'export-housekeeping': {
        'task': 'leakscope.tasks.cleanup_exports',
        'schedule': timedelta(hours=1), 'args': [],
    },
}
if os.environ.get('LEAKSCOPE_LEGACY_MONITORING', '0').lower() in {'1', 'true', 'yes'}:
    CELERY_BEAT_SCHEDULE['monitoring'] = {'task': 'leakscope.tasks.monitor_periodic', 'schedule': timedelta(hours=24), 'args': []}
LEAKSCOPE_CAPTURE_RETENTION_DAYS = max(1, int(os.environ.get('LEAKSCOPE_CAPTURE_RETENTION_DAYS', '30')))

# Local Atlas assets and derived capture maintenance.
LEAKSCOPE_GEOIP_FILE = os.environ.get('LEAKSCOPE_GEOIP_FILE', str(EXPORT_DIR.parent / 'atlas' / 'geoip' / 'city.mmdb'))
LEAKSCOPE_GEOIP_SOURCE = 'DB-IP City Lite'
LEAKSCOPE_PANORAMAX_API_URL = os.environ.get('LEAKSCOPE_PANORAMAX_API_URL', 'http://panoramax:5000/api')
LEAKSCOPE_GALLERY_AUTO_THUMBNAILS = os.environ.get('LEAKSCOPE_GALLERY_AUTO_THUMBNAILS', '0').lower() in {'1','true','yes'}
