"""Explicit, bounded TCP connection inventory for one numeric destination.

This module sends no application data and performs no DNS lookup. Callers own
scope consent and blacklist policy. A deadline/output limit may leave a partial
inventory; remaining_ports includes every unfinished or unreported open port.
"""
import errno
import ipaddress
import math
import re
import socket
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

MAX_CONCURRENCY=32
MAX_OPEN_PORTS=256
MAX_ERRORS=50


def _compact(ports):
    if not ports:return ''
    parts=[];start=previous=ports[0]
    for port in ports[1:]:
        if port==previous+1:previous=port;continue
        parts.append(str(start) if start==previous else f'{start}-{previous}')
        start=previous=port
    parts.append(str(start) if start==previous else f'{start}-{previous}')
    return ','.join(parts)


def _ports(expression):
    if not isinstance(expression,str) or not expression.strip() or len(expression)>1000:
        raise ValueError('Enter a TCP port expression of at most1000 characters')
    expression=expression.strip()
    if expression.lower()=='all':return list(range(1,65536))
    ports=set()
    for item in expression.split(','):
        item=item.strip()
        match=re.fullmatch(r'([0-9]{1,5})(?:\s*-\s*([0-9]{1,5}))?',item)
        if not match:raise ValueError('Use comma-separated TCP ports or ascending ranges')
        first=int(match.group(1));last=int(match.group(2) or first)
        if not 1<=first<=last<=65535:raise ValueError('TCP ports must be within1–65535 and ranges must ascend')
        ports.update(range(first,last+1))
    return sorted(ports)


def validate_scan_options(options=None):
    if options is None:options={}
    if not isinstance(options,dict) or set(options)-{'ports','rate_per_second','max_seconds','timeout_seconds'}:
        raise ValueError('Invalid TCP inventory options')
    ports=_ports(options.get('ports','1-65535'))
    rate=options.get('rate_per_second',20);duration=options.get('max_seconds',120);timeout=options.get('timeout_seconds',.3)
    if isinstance(rate,bool) or not isinstance(rate,int) or not 1<=rate<=100:
        raise ValueError('TCP inventory rate must be an integer within1–100 per second')
    if isinstance(duration,bool) or not isinstance(duration,int) or not 15<=duration<=900:
        raise ValueError('TCP inventory time budget must be an integer within15–900 seconds')
    if isinstance(timeout,bool) or not isinstance(timeout,(float,int)) or not math.isfinite(timeout) or not .01<=timeout<=1:
        raise ValueError('TCP connect timeout must be within0.01–1 second')
    return {'ports':_compact(ports),'rate_per_second':rate,'max_seconds':duration,'timeout_seconds':float(timeout)}


def _address(value):
    if not isinstance(value,str) or len(value)>64 or '%' in value:
        raise ValueError('TCP inventory requires one literal IPv4 or IPv6 address')
    try:address=ipaddress.ip_address(value)
    except ValueError:raise ValueError('TCP inventory requires one literal IPv4 or IPv6 address; no DNS lookup is made') from None
    effective=address.ipv4_mapped if isinstance(address,ipaddress.IPv6Address) and address.ipv4_mapped else address
    if effective.is_unspecified or effective.is_multicast or str(effective)=='255.255.255.255':
        raise ValueError('TCP inventory requires a unicast address')
    return address


def _probe(address,port,timeout,deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:return {'attempted':False,'status':'deadline'}
    family=socket.AF_INET6 if address.version==6 else socket.AF_INET
    destination=(str(address),port,0,0) if family==socket.AF_INET6 else (str(address),port)
    try:
        with socket.socket(family,socket.SOCK_STREAM) as connection:
            connection.settimeout(min(timeout,remaining))
            try:code=connection.connect_ex(destination)
            except (socket.timeout,TimeoutError):return {'attempted':True,'status':'timeout'}
            except OSError as exc:return {'attempted':True,'status':'error','error':errno.errorcode.get(exc.errno,'CONNECT_ERROR')}
        if code==0:return {'attempted':True,'status':'open'}
        if code in {errno.ECONNREFUSED,errno.ECONNRESET}:return {'attempted':True,'status':'closed'}
        if code in {errno.ETIMEDOUT,errno.EAGAIN,errno.EWOULDBLOCK}:return {'attempted':True,'status':'timeout'}
        return {'attempted':True,'status':'error','error':errno.errorcode.get(code,'CONNECT_ERROR')}
    except OSError as exc:return {'attempted':False,'status':'error','error':errno.errorcode.get(exc.errno,'SOCKET_ERROR')}


def scan_tcp(address,options=None,*,should_stop=None):
    """Probe a paced subset/range of TCP ports without application requests.

    Up to32 connections are pending. Cancellation stops scheduling new probes;
    already-started connections finish within their at-most-one-second timeout.
    A sorted completed prefix and exact remaining expression avoid skipping
    out-of-order work when a caller resumes a partial inventory.
    """
    address=_address(address);options=validate_scan_options(options)
    if should_stop is not None and not callable(should_stop):raise ValueError('Invalid cancellation callback')
    ports=_ports(options['ports']);started=time.monotonic();deadline=started+options['max_seconds']
    interval=1/options['rate_per_second'];next_start=started;next_index=0
    pending={};completed=set();opens=[];omitted=set();attempted=0;error_count=0
    errors=[];outcomes={'open':0,'closed':0,'timeout':0,'error':0};stop_reason='';output_limited=False

    def error(port,reason):
        nonlocal error_count
        error_count+=1
        if len(errors)<MAX_ERRORS:errors.append({'port':port,'error':str(reason)[:100]})

    executor=ThreadPoolExecutor(max_workers=MAX_CONCURRENCY,thread_name_prefix='leakscope-tcp-inventory')
    try:
        while next_index<len(ports) or pending:
            # Account for every completed future before considering cancellation
            # or an output cap. A cancelled queued future was never a probe.
            for future in [item for item in pending if item.done()]:
                index=pending.pop(future);port=ports[index]
                if future.cancelled():continue
                try:result=future.result()
                except Exception:
                    error(port,'Probe worker failed');stop_reason=stop_reason or 'probe_error';continue
                if result.get('attempted') is True:
                    completed.add(index);attempted+=1
                    status=result.get('status')
                    if status not in outcomes:status='error'
                    outcomes[status]+=1
                    if status=='open':
                        if len(opens)<MAX_OPEN_PORTS:opens.append(port)
                        else:omitted.add(index);output_limited=True
                    elif status=='error':error(port,result.get('error') or 'CONNECT_ERROR')
                elif result.get('status')=='error':
                    error(port,result.get('error') or 'SOCKET_ERROR');stop_reason=stop_reason or 'probe_error'
            now=time.monotonic()
            if not stop_reason:
                if should_stop is not None:
                    try:cancelled=bool(should_stop())
                    except Exception:
                        error(None,'Cancellation callback failed');cancelled=True
                    if cancelled:stop_reason='cancelled'
                if not stop_reason and now>=deadline:stop_reason='deadline'
                if not stop_reason and len(opens)>=MAX_OPEN_PORTS and (next_index<len(ports) or pending):
                    stop_reason='output_limit';output_limited=True
            if stop_reason:
                for future in pending:future.cancel()
                if not pending:break
            elif next_index<len(ports) and len(pending)<MAX_CONCURRENCY and now>=next_start:
                future=executor.submit(_probe,address,ports[next_index],options['timeout_seconds'],deadline)
                pending[future]=next_index;next_index+=1
                # Delayed scheduling does not accumulate burst credits.
                next_start=now+interval
                continue
            if pending:
                delay=.05 if stop_reason or len(pending)>=MAX_CONCURRENCY or next_index>=len(ports) else min(.05,max(0,next_start-now),max(0,deadline-now))
                wait(pending,timeout=delay,return_when=FIRST_COMPLETED)
            elif next_index<len(ports) and not stop_reason:
                time.sleep(max(0,min(.05,next_start-now,deadline-now)))
    finally:
        executor.shutdown(wait=True,cancel_futures=True)
    remaining=[port for index,port in enumerate(ports) if index not in completed or index in omitted]
    prefix=0
    while prefix<len(ports) and prefix in completed and prefix not in omitted:prefix+=1
    return {'target':str(address),'operation':'TCP connect inventory; no application requests',
        'open_ports':sorted(opens),'attempted':attempted,'requested':len(ports),'scheduled':next_index,
        'partial':bool(remaining or error_count or output_limited),'next_port':remaining[0] if remaining else None,
        'remaining_ports':_compact(remaining),'resume_includes_unreported_open_ports':bool(omitted),
        'completed_prefix':prefix,'errors':errors,'error_count':error_count,'outcomes':outcomes,
        'output_limited':output_limited,'open_ports_omitted':len(omitted),'stop_reason':stop_reason,
        'elapsed_seconds':round(max(0,time.monotonic()-started),3),'rate_per_second':options['rate_per_second'],
        'max_seconds':options['max_seconds'],'timeout_seconds':options['timeout_seconds'],
        'max_concurrency':MAX_CONCURRENCY,'timeouts_do_not_establish_closed_ports':True}
