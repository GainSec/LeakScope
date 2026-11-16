"""CSRF-protected controls for ephemeral, target-bound browser verification."""
from datetime import timedelta
from uuid import UUID, uuid4

from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from leakscope.phase3 import browser_client as client
from leakscope.phase3.state import visible_endpoints
from leakscope.phase3.jobs import endpoint_snapshot, queue_capture, capture_root
from leakscope.services.config import get_config
from leakscope.services.runs import serialize_run
from leakscope_app.models import ExecutionLease
from .workbench import endpoint, _body


def _id(value):
    try: return str(UUID(value))
    except (ValueError,TypeError,AttributeError): raise ValueError('Invalid browser session ID') from None


def _lease(session_id):
    row=ExecutionLease.objects.get(pk='browser-session:'+_id(session_id),expires_at__gt=timezone.now())
    endpoint_row=visible_endpoints().get(pk=row.data['endpoint_id'])
    return row,endpoint_row


def _session(data, session_id):
    return {**{key:data[key] for key in ('expires_at','viewport','profile','status','url','title') if key in data},
            'id':session_id,'frame_url':'/api/browser/sessions/'+session_id+'/frame'}


@endpoint()
def capabilities(request):
    try: return JsonResponse(client.request('GET','/health',timeout=5))
    except ValueError as exc: return JsonResponse({'available':False,'backend':'playwright','error':str(exc)})


@endpoint(('POST',))
def create(request):
    from leakscope.phase3.capture import supported_actions, _enforce_blacklist, _endpoint
    data=_body(request)
    endpoint_row=visible_endpoints().get(pk=_id(data.get('endpoint_id')))
    snapshot=endpoint_snapshot(endpoint_row)
    if 'screenshot' not in supported_actions(snapshot) or endpoint_row.service_type=='vnc': raise ValueError('Select a web endpoint for browser verification')
    address,port,scheme,service,transport=_endpoint(snapshot)
    _enforce_blacklist(address,port,transport,get_config()['config'])
    options=client.validate_options(data.get('options'))
    if options['backend']!='playwright' or 'session_id' in options or 'snapshot_id' in options: raise ValueError('Start a new Playwright verification session')
    result=client.request('POST','/sessions',client.target_payload(snapshot,options),timeout=options['timeout_seconds']+10)
    session_id=_id(result.get('id'))
    try: expiry=parse_datetime(result.get('expires_at',''))
    except (ValueError,TypeError): expiry=None
    if not expiry or timezone.is_naive(expiry) or expiry<=timezone.now():
        client.request('POST','/sessions/'+session_id+'/close',{},timeout=10)
        raise ValueError('Browser returned an invalid session expiry')
    expiry=min(expiry,timezone.now()+timedelta(minutes=10))
    ExecutionLease.objects.update_or_create(key='browser-session:'+session_id,defaults={
        'expires_at':expiry,'data':{'endpoint_id':str(endpoint_row.pk),'snapshot':snapshot,'options':options}})
    return JsonResponse({'session':_session(dict(result,expires_at=expiry.isoformat()),session_id)})


@endpoint()
def status(request,session_id):
    _lease(session_id)
    return JsonResponse({'session':_session(client.request('GET','/sessions/'+_id(session_id)),_id(session_id))})


@endpoint()
def frame(request,session_id):
    _lease(session_id)
    response=HttpResponse(client.request('GET','/sessions/'+_id(session_id)+'/frame.png',raw=True,timeout=15),content_type='image/png')
    response['Cache-Control']='no-store';response['X-Content-Type-Options']='nosniff'
    return response


def validate_input(data):
    kind=data.get('type')
    fields={'click':{'x','y'},'text':{'text'},'key':{'key'},'scroll':{'delta_x','delta_y'}}
    if kind not in fields or set(data)-fields[kind]-{'type'}: raise ValueError('Unsupported browser input')
    if kind=='click':
        for key,maximum in [('x',1280),('y',800)]:
            if isinstance(data.get(key),bool) or not isinstance(data.get(key),int) or not 0<=data[key]<maximum: raise ValueError('Invalid pointer coordinate')
    elif kind=='text':
        if not isinstance(data.get('text'),str) or not 1<=len(data['text'])<=1000 or any(ord(c)<32 for c in data['text']): raise ValueError('Invalid browser text')
    elif kind=='key':
        if data.get('key') not in {'Enter','Tab','Shift+Tab','Backspace','Delete','Escape','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End','PageUp','PageDown','Space'}: raise ValueError('Unsupported browser key')
    else:
        for key in ('delta_x','delta_y'):
            if isinstance(data.get(key,0),bool) or not isinstance(data.get(key,0),(int,float)) or not -1800<=data.get(key,0)<=1800: raise ValueError('Invalid browser scroll')
    return data


@endpoint(('POST',))
def input(request,session_id):
    _lease(session_id)
    return JsonResponse(client.request('POST','/sessions/'+_id(session_id)+'/input',validate_input(_body(request)),timeout=10))


@endpoint(('POST',))
def capture(request,session_id):
    lease,endpoint_row=_lease(session_id)
    root=capture_root();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    client.cleanup_snapshots(root)
    result=client.request('POST','/sessions/'+_id(session_id)+'/capture',{},timeout=15)
    frozen=client.publish_capture(result,root)
    snapshot_id=str(uuid4())
    try:
        row=ExecutionLease.objects.create(key='browser-snapshot:'+snapshot_id,expires_at=timezone.now()+timedelta(minutes=10),
            data={'endpoint_id':str(endpoint_row.pk),'capture':frozen})
    except Exception:
        client.discard_capture(frozen,root)
        raise
    options=dict(lease.data.get('options') or {},backend='playwright',snapshot_id=snapshot_id)
    try: run=queue_capture(endpoint_row.pk,['screenshot'],options={'screenshot':options},cooldown_seconds=0)
    except Exception:
        client.discard_snapshot(row,root)
        raise
    return JsonResponse({'run':serialize_run(run)})


@endpoint(('POST',))
def close(request,session_id):
    lease=ExecutionLease.objects.filter(pk='browser-session:'+_id(session_id)).first()
    try: result=client.request('POST','/sessions/'+_id(session_id)+'/close',{},timeout=10)
    finally:
        if lease: lease.delete()
    return JsonResponse(result)
