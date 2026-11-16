"""Request/DB boundary timings without request, SQL, credential or target data.

Connection setup and query execution are timed independently. This exposes
slow handshakes versus slow SQL without enabling Django's SQL logging.
"""
from contextlib import ExitStack, contextmanager
import json
import logging
from time import perf_counter
from uuid import uuid4

from django.conf import settings
from django.db import InterfaceError, OperationalError, connections
from django.http import JsonResponse

log = logging.getLogger('leakscope.runtime')
UNAVAILABLE = (OperationalError, InterfaceError)


def database_unavailable(request, error):
    """Safe API failure even when a view's existing decorator catches the DB error."""
    request_id = getattr(request, 'leakscope_request_id', None) or uuid4().hex
    timing = getattr(request, 'leakscope_timing', None)
    if timing is not None:
        timing.db_error = type(error).__name__
    # Never log exception text: libpq can include connection details/credentials.
    log.warning('runtime_database_unavailable %s', json.dumps({
        'request_id': request_id, 'error_type': type(error).__name__}, separators=(',', ':')))
    response = unavailable_response(request, request_id)
    response['X-Request-ID'] = request_id
    return response


def unavailable_response(request, request_id):
    message = ('Database temporarily unavailable. Please retry this request.' if request.method in {'GET', 'HEAD'}
               else 'Database temporarily unavailable. Check saved results before retrying this action.')
    return JsonResponse({'error': message, 'request_id': request_id}, status=503)


class RequestTiming:
    def __init__(self):
        self.query_count = 0
        self.query_ms = 0.0
        self.connect_count = 0
        self.connect_ms = 0.0
        self.db_error = None

    def execute(self, execute, sql, params, many, context):
        start = perf_counter()
        self.query_count += 1
        try:
            return execute(sql, params, many, context)
        except UNAVAILABLE as error:
            self.db_error = type(error).__name__
            raise
        finally:
            self.query_ms += (perf_counter() - start) * 1000


@contextmanager
def instrument_connection(connection, timing):
    # Django database wrappers are thread-local; restore even on failure so a
    # persistent connection never retains another request's timing object.
    original = connection.get_new_connection
    previous = connection.__dict__.get('get_new_connection')
    had_instance_attribute = 'get_new_connection' in connection.__dict__

    def connect(parameters):
        start = perf_counter()
        timing.connect_count += 1
        try:
            return original(parameters)
        except UNAVAILABLE as error:
            timing.db_error = type(error).__name__
            raise
        finally:
            timing.connect_ms += (perf_counter() - start) * 1000

    connection.get_new_connection = connect
    try:
        yield
    finally:
        if had_instance_attribute:
            connection.get_new_connection = previous
        else:
            del connection.get_new_connection


class RuntimeDiagnosticsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = uuid4().hex
        request.leakscope_request_id = request_id
        timing = request.leakscope_timing = RequestTiming()
        start = perf_counter()
        with ExitStack() as stack:
            for connection in connections.all():
                stack.enter_context(instrument_connection(connection, timing))
                stack.enter_context(connection.execute_wrapper(timing.execute))
            response = self.get_response(request)
        # Existing endpoint decorators catch DB errors. The timing hook detects
        # those too, without inspecting or reflecting their exception strings.
        if timing.db_error and response.status_code >= 500 and request.path.startswith('/api/'):
            response = unavailable_response(request, request_id)
        elapsed = (perf_counter() - start) * 1000
        response['X-Request-ID'] = request_id
        metrics = f'app;dur={elapsed:.2f}, db;dur={timing.query_ms:.2f}, db_connect;dur={timing.connect_ms:.2f}'
        response['Server-Timing'] = ', '.join(filter(None, [response.get('Server-Timing'), metrics]))
        # Resolver route templates retain parameter names, never their values.
        match = getattr(request, 'resolver_match', None)
        route = getattr(match, 'view_name', None) or 'unresolved'
        method = request.method if request.method in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT'} else 'OTHER'
        record = {'request_id': request_id, 'method': method, 'route': route,
                  'status': response.status_code, 'app_ms': round(elapsed, 2),
                  'db_ms': round(timing.query_ms, 2), 'db_queries': timing.query_count,
                  'db_connect_ms': round(timing.connect_ms, 2), 'db_connects': timing.connect_count,
                  'db_error': timing.db_error}
        threshold = getattr(settings, 'LEAKSCOPE_SLOW_REQUEST_MS', 1000)
        if elapsed >= threshold or response.status_code >= 500:
            log.warning('runtime_request %s', json.dumps(record, separators=(',', ':')))
        return response
