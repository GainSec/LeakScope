"""Server-side integration profiles; credentials are encrypted with the site secret."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction

PROFILE_KEYS = {
    'llm': ({'base_url': 'LLM_API_BASE', 'model': 'LLM_MODEL', 'thinking_mode':'LLM_THINKING_MODE'}, 'LLM_API_KEY'),
    'firecrawl': ({'base_url': 'FIRECRAWL_API_URL'}, 'FIRECRAWL_API_KEY'),
    'certspotter': ({}, 'CERTSPOTTER_API_KEY'), 'urlscan': ({}, 'URLSCAN_API_KEY'),
    'netlas': ({}, 'NETLAS_API_KEY'), 'shodan': ({}, 'SHODAN_API_KEY'), 'zoomeye': ({}, 'ZOOMEYE_API_KEY'),
}


def _cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()))


def overlay_profiles(config):
    from leakscope_app.models import IntegrationProfile
    errors = []
    for row in IntegrationProfile.objects.all():
        if row.name not in PROFILE_KEYS: continue
        fields, secret_key = PROFILE_KEYS[row.name]
        for field, config_key in fields.items():
            if field in row.values: config[config_key] = row.values[field]
        try:
            config[secret_key] = _cipher().decrypt(row.encrypted_secret.encode()).decode() if row.encrypted_secret else ''
        except (InvalidToken, UnicodeError, ValueError):
            config[secret_key] = ''
            errors.append(row.name + ' credentials cannot be decrypted with the current site secret; re-enter them or restore the matching app secret.')
    config['INTEGRATION_ERRORS'] = errors
    return config


@transaction.atomic
def save_profiles(data, current):
    from leakscope.intelligence.clients import _base, _thinking_mode
    from leakscope_app.models import IntegrationProfile
    if not isinstance(data, dict) or not data or set(data) - set(PROFILE_KEYS): raise ValueError('Unknown integration profile')
    pending = []
    for name in sorted(data):
        values = data[name]
        fields, secret_key = PROFILE_KEYS[name]
        if not isinstance(values, dict) or set(values) - set(fields) - {'api_key'}: raise ValueError('Unknown integration setting')
        row = IntegrationProfile.objects.select_for_update().filter(pk=name).first()
        existing = row is not None
        row = row or IntegrationProfile(name=name)
        row.values = dict(row.values or {})
        for field in fields:
            if field not in values: continue
            value = values[field]
            if not isinstance(value, str): raise ValueError('Invalid integration setting')
            value = value.strip()
            if field == 'base_url' and value: value = _base(value, name)
            elif field == 'model' and (len(value) > 200 or any(ord(c) < 32 for c in value)): raise ValueError('Invalid LLM model')
            elif field == 'thinking_mode':value=_thinking_mode(value)
            row.values[field] = value
        if 'api_key' in values or not existing:
            secret = values.get('api_key', current.get(secret_key, ''))
            if not isinstance(secret, str) or len(secret) > 4096 or any(ord(c) < 32 for c in secret): raise ValueError('Invalid API key')
            row.encrypted_secret = _cipher().encrypt(secret.encode()).decode() if secret else ''
        pending.append(row)
    for row in pending: row.save()
