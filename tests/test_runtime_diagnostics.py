"""Runtime timing must be useful without retaining request/query secrets."""
import json
from unittest.mock import patch

from django.db import OperationalError
from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase


class RuntimeDiagnosticsTests(SimpleTestCase):
    def middleware(self, view):
        from leakscope.runtime_diagnostics import RuntimeDiagnosticsMiddleware
        return RuntimeDiagnosticsMiddleware(view)

    def test_request_id_is_generated_and_client_values_are_never_logged(self):
        request = RequestFactory().get('/api/private/secret-value?token=private-query', HTTP_X_REQUEST_ID='secret-client-header')
        with patch('leakscope.runtime_diagnostics.log.warning') as warning:
            response = self.middleware(lambda req: JsonResponse({'error': 'existing'}, status=500))(request)
        self.assertRegex(response['X-Request-ID'], r'^[a-f0-9]{32}$')
        record = json.loads(warning.call_args.args[1])
        self.assertEqual(record['request_id'], response['X-Request-ID'])
        self.assertEqual(record['status'], 500)
        self.assertEqual(record['route'], 'unresolved')
        self.assertNotIn('secret', str(warning.call_args))
        self.assertIn('app;dur=', response['Server-Timing'])
        self.assertIn('db;dur=', response['Server-Timing'])
        self.assertIn('db_connect;dur=', response['Server-Timing'])

    def test_sql_failure_caught_by_existing_endpoint_is_still_safe_and_retryable(self):
        from leakscope.runtime_diagnostics import RequestTiming
        from leakscope_app.workbench import endpoint
        secret = 'postgresql://private:password@database query-private'
        @endpoint()
        def view(request):
            timing = request.leakscope_timing
            def execute(*args):
                raise OperationalError(secret)
            return timing.execute(execute, 'SELECT private_value', ['secret-param'], False, {})
        request = RequestFactory().get('/api/example?password=private')
        with patch('leakscope.runtime_diagnostics.log.warning') as warning:
            response = self.middleware(view)(request)
        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.content)
        self.assertEqual(payload['request_id'], response['X-Request-ID'])
        self.assertIn('temporarily unavailable', payload['error'])
        self.assertNotIn(secret, response.content.decode())
        self.assertNotIn('private', str(warning.call_args))
        self.assertEqual(request.leakscope_timing.query_count, 1)

    def test_database_failure_never_logs_raw_exception_or_returns_dsn(self):
        from leakscope_app.workbench import endpoint
        @endpoint()
        def view(request):
            raise OperationalError('postgresql://private:password@database secret-query')
        request = RequestFactory().get('/api/example')
        with patch('leakscope_app.workbench.log') as endpoint_log, patch('leakscope.runtime_diagnostics.log') as runtime_log, \
                patch('leakscope_app.workbench._config') as config:
            response = self.middleware(view)(request)
        self.assertEqual(response.status_code, 503)
        output = str(endpoint_log.mock_calls) + str(runtime_log.mock_calls) + response.content.decode()
        for secret in ['postgresql://', 'password', 'private', 'secret-query']:
            self.assertNotIn(secret, output)
        self.assertEqual(json.loads(response.content)['request_id'], response['X-Request-ID'])
        config.assert_not_called()  # Error formatting must not reconnect to load the blacklist/config.

    def test_connect_is_measured_separately_and_wrapper_is_restored(self):
        from leakscope.runtime_diagnostics import RequestTiming, instrument_connection
        class Connection:
            def get_new_connection(self, parameters):
                return 'connection'
        connection = Connection()
        original = connection.get_new_connection
        timing = RequestTiming()
        with instrument_connection(connection, timing):
            self.assertEqual(connection.get_new_connection({'password': 'private'}), 'connection')
        self.assertEqual(connection.get_new_connection, original)
        self.assertEqual(timing.connect_count, 1)
        self.assertGreaterEqual(timing.connect_ms, 0)
        self.assertEqual(timing.query_count, 0)

    def test_failed_connection_restores_wrapper_and_keeps_only_exception_type(self):
        from leakscope.runtime_diagnostics import RequestTiming, instrument_connection
        class Connection:
            def get_new_connection(self, parameters):
                raise OperationalError('password=private-dsn')
        connection = Connection()
        original = connection.get_new_connection
        timing = RequestTiming()
        with self.assertRaises(OperationalError), instrument_connection(connection, timing):
            connection.get_new_connection({})
        self.assertEqual(connection.get_new_connection, original)
        self.assertEqual(timing.db_error, 'OperationalError')
        self.assertNotIn('private', str(timing.__dict__))

    def test_real_sql_is_counted_without_storing_sql_or_parameters(self):
        from leakscope.runtime_diagnostics import RequestTiming
        timing = RequestTiming()
        self.assertEqual(timing.execute(lambda *args: 4, 'SELECT private', ['private-param'], False, {}), 4)
        self.assertEqual(timing.query_count, 1)
        self.assertGreaterEqual(timing.query_ms, 0)
        self.assertNotIn('private', str(timing.__dict__))

    def test_non_database_error_response_and_existing_headers_are_preserved(self):
        def view(request):
            response = JsonResponse({'error': 'validation'}, status=400)
            response['Server-Timing'] = 'existing;dur=1'
            return response
        response = self.middleware(view)(RequestFactory().get('/api/example'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content), {'error': 'validation'})
        self.assertTrue(response['Server-Timing'].startswith('existing;dur=1,'))

    def test_actual_in_memory_connection_and_execute_are_measured(self):
        from copy import deepcopy
        from django.conf import settings
        from django.db.backends.sqlite3.base import DatabaseWrapper
        from leakscope.runtime_diagnostics import RequestTiming, instrument_connection
        config = deepcopy(settings.DATABASES['default'])
        config['NAME'] = ':memory:'
        config['ENGINE'] = 'django.db.backends.sqlite3'
        config['OPTIONS'] = {}
        connection = DatabaseWrapper(config, alias='timing_fixture')
        timing = RequestTiming()
        try:
            with instrument_connection(connection, timing), connection.execute_wrapper(timing.execute):
                with connection.cursor() as cursor:
                    cursor.execute('SELECT 1')
                    self.assertEqual(cursor.fetchone(), (1,))
            self.assertEqual(timing.connect_count, 1)
            self.assertEqual(timing.query_count, 1)
        finally:
            connection.close()

    def test_mutation_failure_does_not_suggest_blindly_replaying_an_action(self):
        def view(request):
            request.leakscope_timing.db_error = 'OperationalError'
            return JsonResponse({'error': 'private'}, status=500)
        with patch('leakscope.runtime_diagnostics.log.warning'):
            response = self.middleware(view)(RequestFactory().post('/api/example'))
        self.assertEqual(response.status_code, 503)
        self.assertIn('Check saved results', json.loads(response.content)['error'])
