"""Cross-worker request pacing, separate from daily lookup job budgets."""
import time
import math
from datetime import timedelta
from django.conf import settings


def defer(provider, retry_after):
    """Persist the longest provider-wide Retry-After across worker processes."""
    if not settings.configured or not getattr(settings, 'LEAKSCOPE_PROVIDER_THROTTLE', False): return
    if provider not in ('shodan', 'zoomeye', 'netlas'): raise ValueError('Unknown provider rate bucket')
    if isinstance(retry_after, bool) or not isinstance(retry_after, int) or not 1 <= retry_after <= 86400:
        raise ValueError('Invalid provider retry delay')
    from django.db import transaction
    from django.utils import timezone
    from leakscope_app.models import ExecutionLease
    with transaction.atomic():
        now = timezone.now()
        key = 'provider-defer:' + provider
        ExecutionLease.objects.get_or_create(key=key, defaults={'expires_at': now, 'data': {}})
        lease = ExecutionLease.objects.select_for_update().get(pk=key)
        expiry = now + timedelta(seconds=retry_after)
        if lease.expires_at < expiry:
            lease.expires_at = expiry
            lease.save(update_fields=['expires_at'])


def _remaining(provider):
    from django.utils import timezone
    from leakscope_app.models import ExecutionLease
    expiry = ExecutionLease.objects.filter(pk='provider-defer:' + provider).values_list('expires_at', flat=True).first()
    return max(0, math.ceil((expiry - timezone.now()).total_seconds())) if expiry else 0


def throttle(bucket, *, max_wait=25):
    if not settings.configured or not getattr(settings, 'LEAKSCOPE_PROVIDER_THROTTLE', False): return
    from .state import acquire_lease
    intervals = {'shodan': 1, 'zoomeye': 1, 'netlas': 1, 'netlas:certificates': 20}
    if bucket not in intervals: raise ValueError('Unknown provider rate bucket')
    from .providers import ProviderRateLimit
    interval = intervals[bucket]
    started = time.monotonic()
    while True:
        remaining = _remaining(bucket.split(':', 1)[0])
        if remaining:
            raise ProviderRateLimit(bucket.split(':', 1)[0], remaining)
        if acquire_lease('provider-rate:' + bucket, interval): return
        if time.monotonic() - started >= max_wait:
            raise ProviderRateLimit(bucket.split(':', 1)[0], interval)
        time.sleep(min(0.25, max(0.01, max_wait - (time.monotonic() - started))))
