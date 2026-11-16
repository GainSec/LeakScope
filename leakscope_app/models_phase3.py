"""Normalized observations and explicitly configured collection workflows."""
import uuid
from django.db import models
from django.utils import timezone


class ServiceEndpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=64, unique=True)
    address = models.CharField(max_length=253, db_index=True)
    port = models.PositiveIntegerField()
    transport = models.CharField(max_length=8, default='tcp')
    scheme = models.CharField(max_length=16, blank=True)
    service_type = models.CharField(max_length=100, blank=True, db_index=True)
    title = models.CharField(max_length=500, blank=True)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-last_seen']


class ServiceObservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(ServiceEndpoint, on_delete=models.CASCADE, related_name='observations')
    provider = models.CharField(max_length=32, db_index=True)
    hidden = models.BooleanField(default=False, db_index=True)
    source_id = models.CharField(max_length=256, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)
    last_observed_at = models.DateTimeField(null=True, blank=True)
    first_collected_at = models.DateTimeField(default=timezone.now)
    last_collected_at = models.DateTimeField(default=timezone.now, db_index=True)
    data = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=64)
    # Times this exact result was saved by a collection. Rows saved before the counter existed start at one.
    collection_count = models.PositiveIntegerField(default=1, db_default=1)

    class Meta:
        ordering = ['-last_collected_at']
        constraints = [models.UniqueConstraint(fields=['endpoint', 'provider', 'fingerprint'], name='unique_service_observation')]


class EvidenceArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(ServiceEndpoint, on_delete=models.CASCADE, related_name='artifacts')
    run = models.ForeignKey('leakscope_app.Run', null=True, blank=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=32)
    relative_path = models.CharField(max_length=300)
    mime = models.CharField(max_length=100, default='application/octet-stream')
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    tool = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class CloudResource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url = models.CharField(max_length=500, unique=True)
    provider = models.CharField(max_length=24, db_index=True)
    name = models.CharField(max_length=100)
    region = models.CharField(max_length=64, blank=True)
    kind = models.CharField(max_length=24)
    state = models.CharField(max_length=24, db_index=True)
    evidence = models.JSONField(default=dict)
    endpoint = models.ForeignKey(ServiceEndpoint, null=True, on_delete=models.SET_NULL)
    last_run = models.ForeignKey('leakscope_app.Run', null=True, on_delete=models.SET_NULL)
    first_seen = models.DateTimeField(default=timezone.now)
    last_checked = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-last_checked']


class Tag(models.Model):
    name = models.CharField(max_length=64, unique=True)


class TagAssignment(models.Model):
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    target_kind = models.CharField(max_length=20)
    target_key = models.CharField(max_length=120, db_index=True)
    origin = models.CharField(max_length=120, default='manual')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tag', 'target_kind', 'target_key', 'origin'], name='unique_tag_assignment')]


class SavedLookup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    provider = models.CharField(max_length=20, default='netlas')
    query = models.TextField()
    service_type = models.CharField(max_length=100, blank=True)
    scope = models.JSONField(default=dict)
    actions = models.JSONField(default=list)
    auto_fetch = models.BooleanField(default=False)
    collection_mode = models.CharField(max_length=16, blank=True, default='', db_default='', choices=[
        ('', 'Custom actions'), ('passive', 'Passive'), ('demo', 'Demo'), ('semi_passive', 'Semi-passive'),
        ('screenshot', 'Screenshot'), ('full', 'Full')])
    enabled = models.BooleanField(default=False, db_index=True)
    interval_seconds = models.PositiveIntegerField(null=True, blank=True)
    cron = models.CharField(max_length=100, blank=True)
    timezone = models.CharField(max_length=64, default='America/New_York')
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_state = models.CharField(max_length=20, blank=True)
    last_error = models.TextField(blank=True)
    page_count = models.PositiveIntegerField(default=1)
    start_page = models.PositiveIntegerField(default=1, db_default=1)
    page_size = models.PositiveIntegerField(default=100)
    max_results = models.PositiveIntegerField(default=100)
    max_daily_runs = models.PositiveIntegerField(default=24)
    cooldown_seconds = models.PositiveIntegerField(default=3600)
    custom_query = models.ForeignKey('leakscope_app.CustomQuery', null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']


class SearchProgress(models.Model):
    """How far provider searches for one exact query and page size have paged."""
    key = models.CharField(max_length=64, primary_key=True)
    provider = models.CharField(max_length=20)
    page_size = models.PositiveIntegerField()
    next_page = models.PositiveIntegerField(default=1)
    total = models.PositiveIntegerField(null=True, blank=True)
    exhausted = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class WatchRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    domains = models.JSONField(default=list)
    keywords = models.JSONField(default=list)
    ports = models.JSONField(default=list)
    actions = models.JSONField(default=list)
    auto_fetch = models.BooleanField(default=True)
    enabled = models.BooleanField(default=True, db_index=True)
    cooldown_seconds = models.PositiveIntegerField(default=3600)
    max_daily_fetches = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)


class StreamSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    mode = models.CharField(max_length=16, default='builtin')
    external_url = models.CharField(max_length=2000, blank=True)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class StreamHealth(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    state = models.CharField(max_length=30, default='starting')
    mode = models.CharField(max_length=16, default='builtin')
    last_event_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=1500, blank=True)
    received = models.PositiveBigIntegerField(default=0)
    matched = models.PositiveBigIntegerField(default=0)
    dropped = models.PositiveBigIntegerField(default=0)


class DispatchSlot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lookup = models.ForeignKey(SavedLookup, on_delete=models.CASCADE)
    scheduled_for = models.DateTimeField()
    run = models.ForeignKey('leakscope_app.Run', null=True, on_delete=models.SET_NULL)
    state = models.CharField(max_length=20, default='pending')
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error = models.CharField(max_length=2000, blank=True)
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['lookup', 'scheduled_for'], name='unique_lookup_slot')]


class ExecutionLease(models.Model):
    key = models.CharField(max_length=256, primary_key=True)
    expires_at = models.DateTimeField(db_index=True)
    data = models.JSONField(default=dict)


class ProviderEndpointState(models.Model):
    endpoint = models.ForeignKey(ServiceEndpoint, on_delete=models.CASCADE)
    provider = models.CharField(max_length=32)
    observation = models.ForeignKey(ServiceObservation, on_delete=models.CASCADE)
    provider_observed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['endpoint', 'provider'], name='unique_endpoint_provider_state')]


class ObservationChange(models.Model):
    endpoint = models.ForeignKey(ServiceEndpoint, on_delete=models.CASCADE, related_name='changes')
    provider = models.CharField(max_length=32, db_index=True)
    previous = models.ForeignKey(ServiceObservation, on_delete=models.SET_NULL, null=True, related_name='outgoing_changes')
    observation = models.ForeignKey(ServiceObservation, on_delete=models.CASCADE, related_name='incoming_changes')
    collected_at = models.DateTimeField(default=timezone.now, db_index=True)
    summary = models.JSONField(default=list)

    class Meta:
        ordering = ['-collected_at', '-pk']


class EvidenceCorrelation(models.Model):
    endpoint = models.ForeignKey(ServiceEndpoint, on_delete=models.CASCADE, related_name='correlations')
    kind = models.CharField(max_length=48)
    value = models.CharField(max_length=128)
    observation = models.ForeignKey(ServiceObservation, on_delete=models.SET_NULL, null=True, blank=True)
    observations = models.ManyToManyField(ServiceObservation, related_name='correlation_signals', blank=True)
    artifact = models.ForeignKey(EvidenceArtifact, on_delete=models.SET_NULL, null=True, blank=True)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['endpoint', 'kind', 'value'], name='unique_endpoint_correlation')]
        indexes = [models.Index(fields=['kind', 'value'], name='correlation_kind_value')]
