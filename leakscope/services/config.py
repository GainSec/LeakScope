"""One configuration reader for workers and HTTP helpers."""
import json
import os
from pathlib import Path
from django.db import DatabaseError


def get_config():
    section={key:os.environ.get(key,'') for key in (
        'SHODAN_API_KEY','ZOOMEYE_API_KEY','ZOOMEYE_TIMEOUT','NETLAS_API_KEY',
        'LLM_API_BASE','LLM_MODEL','LLM_API_KEY','LLM_TIMEOUT','LLM_MAX_TOKENS','LLM_THINKING_MODE',
        'FIRECRAWL_API_URL','FIRECRAWL_API_KEY','CERTSPOTTER_API_KEY','URLSCAN_API_KEY',
    )}
    section.update(ZOOMEYE_SUBTYPE=os.environ.get('ZOOMEYE_SUBTYPE','all'),ZOOMEYE_PAGESIZE=os.environ.get('ZOOMEYE_PAGESIZE','1'),blacklist=[x.strip() for x in os.environ.get('BLACKLIST','').split(',') if x.strip()],monitoring={'gmail_email':os.environ.get('GMAIL_EMAIL',''),'gmail_password':os.environ.get('GMAIL_PASSWORD','')})
    path=Path(os.environ.get('LEAKSCOPE_CONFIG_FILE','config.json'))
    if path.is_file():
        try:
            private=json.loads(path.read_text()).get('config',{})
            for key in section:
                if key in ('blacklist','monitoring'):continue
                if private.get(key):section[key]=private[key]
            if isinstance(private.get('blacklist'),list):section['blacklist'].extend(private['blacklist'])
            if isinstance(private.get('monitoring'),dict):section['monitoring'].update(private['monitoring'])
        except (ValueError,OSError,TypeError,AttributeError):
            # Never put private file content or credentials into logs/exceptions.
            pass
    try:
        from leakscope_app.models import BlacklistEntry
        section['blacklist'].extend(BlacklistEntry.objects.values_list('value',flat=True))
        from .integrations import overlay_profiles
        section = overlay_profiles(section)
    except DatabaseError:
        # Configuration is also loaded during initial schema creation.
        pass
    section['blacklist']=sorted(set(str(x).strip() for x in section['blacklist'] if str(x).strip()))
    return {'config':section}


def safe_text(value):
    """Make remote text valid UTF-8 and safe for PostgreSQL text/JSON storage."""
    return str(value).replace('\x00', '\ufffd').encode('utf-8', 'replace').decode('utf-8')


def redact(value):
    text=safe_text(value)
    section=get_config()['config']
    for key in ('SHODAN_API_KEY','ZOOMEYE_API_KEY','NETLAS_API_KEY','LLM_API_KEY','FIRECRAWL_API_KEY','CERTSPOTTER_API_KEY','URLSCAN_API_KEY'):
        secret=section.get(key)
        if secret:text=text.replace(secret,'[REDACTED]')
    return text[:2000]
