"""Private controller transport and bounded browser capture artifacts."""
import base64
import binascii
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import requests

MAX_RESPONSE = 24 * 1024 * 1024
MAX_ARTIFACTS = 16 * 1024 * 1024

# Keep controller diagnostics bounded and independent of its response text.
BROWSER_FAILURES = {
    'timeout': 'Browser operation timed out',
    'tls_certificate': 'TLS certificate verification failed',
    'connection_refused': 'Target connection was refused',
    'connection_reset': 'Target connection was reset or closed',
    'dns_resolution': 'Target name could not be resolved',
    'navigation_blocked': 'Main navigation outside the selected endpoint was blocked',
    'resource_blocked': 'Requests were blocked by browser policy',
    'http_error': 'Main document returned an HTTP error',
    'auth_required': 'Main document requires authentication; no credentials were sent',
    'network_error': 'Target network request failed',
    'browser_unavailable': 'Browser executable is unavailable',
    'capture_expired': 'Browser capture deadline expired',
    'browser_operation_failed': 'Browser operation failed',
    'controller_timeout': 'Browser controller timed out',
    'controller_unreachable': 'Browser controller is unreachable',
    'invalid_request': 'Invalid browser request',
    'session_unavailable': 'Browser session not found or expired',
    'browser_busy': 'Browser operation already active or busy',
}


class BrowserControllerError(ValueError):
    """A safe, stable reason for capture job diagnostics."""
    def __init__(self, code, status=None):
        self.code = code if isinstance(code, str) and code in BROWSER_FAILURES else 'browser_operation_failed'
        self.status = status
        super().__init__(f'Browser capture failed [{self.code}]: {BROWSER_FAILURES[self.code]}')


def validate_options(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'backend','profile','wait_seconds','timeout_seconds','full_page','session_id','snapshot_id'}:
        raise ValueError('Unsupported screenshot options')
    result = {'backend':'playwright','profile':'strict','wait_seconds':2,'timeout_seconds':40,'full_page':False}
    result.update(value)
    if result['backend'] not in {'playwright','gowitness'} or result['profile'] not in {'strict','web_page'}:
        raise ValueError('Choose a supported screenshot backend and profile')
    for field, low, high in [('wait_seconds',0,10),('timeout_seconds',10,60)]:
        if isinstance(result[field],bool) or not isinstance(result[field],int) or not low <= result[field] <= high:
            raise ValueError('Invalid screenshot '+field)
    if not isinstance(result['full_page'],bool): raise ValueError('Invalid screenshot full_page')
    for field in ('session_id','snapshot_id'):
        if field in result:
            try: result[field] = str(UUID(result[field]))
            except (ValueError,TypeError,AttributeError): raise ValueError('Invalid browser session or snapshot ID') from None
    if 'session_id' in result and 'snapshot_id' in result: raise ValueError('Choose a browser session or frozen snapshot')
    if result['backend']=='gowitness':
        if result['profile']!='strict' or any(key in value for key in ('wait_seconds','timeout_seconds','full_page','session_id','snapshot_id')):
            raise ValueError('Gowitness supports its fixed Strict endpoint profile; use Playwright for these options')
        return {'backend':'gowitness','profile':'strict'}
    return result


def request(method, path, payload=None, *, raw=False, timeout=70):
    """Only talk to the configured internal controller; never follow redirects."""
    base = os.environ.get('BROWSER_CONTROL_URL','http://fetch:9227').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Invalid internal browser controller configuration')
    token_file = os.environ.get('BROWSER_CONTROL_TOKEN_FILE','/run/secrets/browser_control_token')
    try:
        token = Path(token_file).read_text().strip()
        if not 32 <= len(token) <= 256 or any(ord(c)<33 or ord(c)>126 for c in token): raise ValueError
    except (OSError,ValueError): raise ValueError('Browser controller credential is unavailable') from None
    if not path.startswith('/') or '://' in path or '..' in path: raise ValueError('Invalid browser controller route')
    session=requests.Session();session.trust_env=False
    try:
        with session.request(method,base+path,json=payload,headers={'Authorization':'Bearer '+token},timeout=(3,timeout),allow_redirects=False,stream=True) as response:
            body=bytearray()
            limit=8*1024*1024 if raw else MAX_RESPONSE
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body)>limit: raise ValueError('Browser controller response exceeds the size limit')
            if response.status_code not in (200,201):
                # Never forward raw controller text, even on malformed/legacy errors.
                try: code=json.loads(body).get('code')
                except (ValueError,AttributeError,TypeError): code=None
                raise BrowserControllerError(code, response.status_code)
            if raw:
                if not body.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('Browser frame is not a PNG')
                return bytes(body)
            result=json.loads(body)
            if not isinstance(result,dict): raise ValueError('Malformed browser controller response')
            return result
    except requests.Timeout: raise BrowserControllerError('controller_timeout') from None
    except requests.RequestException: raise BrowserControllerError('controller_unreachable') from None
    finally: session.close()


def target_payload(endpoint, options=None):
    from .capture import _endpoint, _resource_path
    from .asset_names import registrable_domain
    address,port,scheme,service,transport=_endpoint(endpoint)
    if transport!='tcp' or service=='vnc': raise ValueError('A web browser requires an HTTP endpoint')
    scheme=scheme if scheme in {'http','https'} else 'https' if port==443 else 'http'
    host='['+address+']' if ':' in address else address
    try: site=registrable_domain(address)
    except ValueError: site=address
    options=validate_options(options)
    return {'url':f'{scheme}://{host}:{port}'+_resource_path(endpoint.get('path','/')), 'site_domain':site,
            **{key:val for key,val in options.items() if key not in {'backend','session_id','snapshot_id'}}}


def assert_session(session_id, endpoint_id):
    from django.utils import timezone
    from leakscope_app.models import ExecutionLease
    from .state import visible_endpoints
    key='browser-session:'+str(UUID(session_id))
    row=ExecutionLease.objects.filter(pk=key,expires_at__gt=timezone.now()).first()
    if not row or row.data.get('endpoint_id')!=str(endpoint_id) or not visible_endpoints().filter(pk=endpoint_id).exists():
        raise ValueError('Browser session is expired or belongs to another endpoint')
    return row


def assert_snapshot(snapshot_id,endpoint_id):
    from django.utils import timezone
    from leakscope_app.models import ExecutionLease
    from .state import visible_endpoints
    row=ExecutionLease.objects.filter(pk='browser-snapshot:'+str(UUID(snapshot_id)),expires_at__gt=timezone.now()).first()
    if not row or row.data.get('endpoint_id')!=str(endpoint_id) or not visible_endpoints().filter(pk=endpoint_id).exists():
        raise ValueError('Browser snapshot is expired or belongs to another endpoint')
    return row


def discard_capture(result,root):
    from leakscope_app.models import EvidenceArtifact
    root=root.resolve()
    for item in result.get('artifacts',[]):
        try:
            path=Path(item['path']).resolve()
            if path.is_relative_to(root) and path.is_file() and not EvidenceArtifact.objects.filter(relative_path=str(path.relative_to(root))).exists(): path.unlink(missing_ok=True)
        except (KeyError,TypeError,OSError): pass


def discard_snapshot(row,root):
    discard_capture(row.data.get('capture') or {},root)
    row.delete()


def cleanup_snapshots(root):
    from django.utils import timezone
    from leakscope_app.models import ExecutionLease
    from django.db import transaction
    keys=list(ExecutionLease.objects.filter(key__startswith='browser-snapshot:',expires_at__lt=timezone.now()).values_list('pk',flat=True))
    for key in keys:
        with transaction.atomic():
            row=ExecutionLease.objects.select_for_update().filter(pk=key,expires_at__lt=timezone.now()).first()
            if row: discard_snapshot(row,root)


def publish_capture(result, root):
    from .capture import _persist
    if not isinstance(result,dict) or not isinstance(result.get('metadata'),dict): raise ValueError('Malformed browser capture')
    items=result.get('artifacts')
    if not isinstance(items,list) or not 1 <= len(items) <= 4: raise ValueError('Malformed browser artifacts')
    metadata=dict(result['metadata'],backend='playwright')
    # A PNG does not prove a complete page or resolved browser challenge.
    metadata.setdefault('capture_quality',{'state':'unassessed','visual_completeness':'unassessed','reasons':[]})
    total=0;prepared=[]
    types={('screenshot','image/png','.png'),('browser-metadata','application/json','.json')}
    for item in items:
        if not isinstance(item,dict) or (item.get('kind'),item.get('mime'),item.get('extension')) not in types:
            raise ValueError('Unsupported browser artifact type')
        try: data=base64.b64decode(item['base64'],validate=True)
        except (KeyError,TypeError,ValueError,binascii.Error): raise ValueError('Malformed browser artifact encoding') from None
        total+=len(data)
        if not data or total>MAX_ARTIFACTS: raise ValueError('Browser artifacts exceed the size limit')
        if item['kind']=='screenshot' and not data.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('Browser screenshot is not a PNG')
        prepared.append((data,item))
    if not any(item['kind']=='screenshot' for _,item in prepared): raise ValueError('Browser returned no screenshot')
    artifacts=[]
    try:
        for data,item in prepared:
            artifacts.append(_persist(root,data,item['mime'],item['kind'],'playwright',metadata,item['extension']))
    except Exception:
        for item in artifacts: Path(item['path']).unlink(missing_ok=True)
        raise
    return {'metadata':metadata,'partial':bool(result.get('partial') or metadata.get('partial')),'artifacts':artifacts}


def capture(endpoint, config, root):
    options=validate_options(config.get('SCREENSHOT_OPTIONS'))
    if options.get('snapshot_id'):
        from django.db import transaction
        with transaction.atomic():
            lease=assert_snapshot(options['snapshot_id'],endpoint.get('id'))
            from django.utils import timezone
            from datetime import timedelta
            lease=type(lease).objects.select_for_update().get(pk=lease.pk,expires_at__gt=timezone.now())
            lease.expires_at=timezone.now()+timedelta(minutes=10)
            lease.save(update_fields=['expires_at'])
            result=lease.data['capture']
        # Keep the lease until expiry so housekeeping can remove unregistered files after worker failure.
        # Paths originate only from publish_capture; normal jobs revalidate size and root.
        return result
    if options.get('session_id'):
        assert_session(options['session_id'],endpoint.get('id'))
        result=request('POST','/sessions/'+options['session_id']+'/capture',{})
    else: result=request('POST','/capture',target_payload(endpoint,options),timeout=options['timeout_seconds']+10)
    return publish_capture(result,root)
