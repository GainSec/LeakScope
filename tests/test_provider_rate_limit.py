from unittest.mock import patch
from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from leakscope.phase3.providers import ProviderRateLimit
from leakscope.phase3.rate_limit import throttle
from tests.test_phase3_providers import Response


@override_settings(LEAKSCOPE_PROVIDER_THROTTLE=True)
class ProviderRateLimitTests(TestCase):
    @patch('leakscope.phase3.providers.requests.request')
    def test_provider_retry_after_defers_other_workers_and_collection_buckets(self, request):
        from leakscope.phase3.providers import search_provider
        request.return_value=Response(status=429,headers={'Retry-After':'45'})
        with self.assertRaises(ProviderRateLimit):
            search_provider('netlas','port:443',{'NETLAS_API_KEY':'fixture-key'})
        with self.assertRaises(ProviderRateLimit) as error:
            throttle('netlas:certificates',max_wait=0)
        self.assertGreaterEqual(error.exception.retry_after,44)
        self.assertEqual(request.call_count,1)
        # Other provider accounts retain their independent buckets.
        throttle('shodan',max_wait=0)

    def test_shared_bucket_refuses_immediate_second_request_without_wait(self):
        throttle('netlas', max_wait=0)
        with self.assertRaises(ProviderRateLimit) as error:
            throttle('netlas', max_wait=0)
        self.assertGreaterEqual(error.exception.retry_after, 1)
        # Certificate searches have their own documented bucket.
        throttle('netlas:certificates', max_wait=0)
        with self.assertRaises(ProviderRateLimit) as error:
            throttle('netlas:certificates', max_wait=0)
        self.assertEqual(error.exception.retry_after, 20)

    def test_shorter_rate_limit_cannot_erase_existing_shared_cooldown(self):
        from leakscope.phase3.rate_limit import defer
        from leakscope_app.models import ExecutionLease
        defer('netlas',60)
        expiry=ExecutionLease.objects.get(pk='provider-defer:netlas').expires_at
        defer('netlas',1)
        self.assertEqual(ExecutionLease.objects.get(pk='provider-defer:netlas').expires_at,expiry)
        with self.assertRaises(ProviderRateLimit) as error:
            throttle('netlas:certificates',max_wait=0)
        self.assertGreaterEqual(error.exception.retry_after,59)
        ExecutionLease.objects.filter(pk='provider-defer:netlas').update(expires_at=timezone.now()-timedelta(seconds=1))
        throttle('netlas:certificates',max_wait=0)
