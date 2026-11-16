"""One bounded TCP connection check; no application request or identity claim."""
import math
import ssl
import time

from .capture_diagnostics import CaptureDiagnosticError
from .wire import connect_tcp


def check_tcp(address, port, *, tls=False, timeout=3):
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or not 0.05<=timeout<=10:
        raise ValueError('Reachability timeout must be a finite number between 0.05 and 10 seconds')
    if not isinstance(tls,bool):raise ValueError('Reachability TLS selection must be a boolean')
    started=time.monotonic();deadline=started+timeout
    try:
        with connect_tcp(address,port,deadline) as raw:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Reachability deadline exceeded')
            raw.settimeout(remaining)
            if tls:
                context=ssl.create_default_context()
                with context.wrap_socket(raw,server_hostname=address):
                    if time.monotonic()>deadline:raise TimeoutError('Reachability deadline exceeded')
            elif time.monotonic()>deadline:raise TimeoutError('Reachability deadline exceeded')
    except ssl.SSLCertVerificationError:
        raise CaptureDiagnosticError('tls_certificate') from None
    except TimeoutError:
        raise CaptureDiagnosticError('timeout') from None
    except ConnectionRefusedError:
        raise CaptureDiagnosticError('connection_refused') from None
    except ConnectionResetError:
        raise CaptureDiagnosticError('connection_reset') from None
    except OSError:
        raise CaptureDiagnosticError('connection_failed') from None
    return {'operation':'TCP connection only; no application payload',
            'reachable':True,'address':address,'port':port,'transport':'tcp','tls':tls,'tls_verified':tls,
            'identity_verified':False,'protocol_verified':False,
            'duration_ms':round((time.monotonic()-started)*1000,3)}
