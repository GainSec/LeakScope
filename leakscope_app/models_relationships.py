"""Certificate discoveries and typed, sourced asset relationships."""
import uuid
from django.db import models
from django.utils import timezone


class AssetNode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=24, db_index=True)
    value = models.CharField(max_length=500, db_index=True)
    label = models.CharField(max_length=500, blank=True)
    attributes = models.JSONField(default=dict)
    organization_status = models.CharField(max_length=12, default='active', db_default='active', db_index=True, choices=[(s,s) for s in ('active','archived','deleted','merged')])
    organization_status_changed_at = models.DateTimeField(null=True, blank=True)
    merged_into = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='merged_sources')
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['kind', 'value']
        constraints = [models.CheckConstraint(condition=(models.Q(kind='organization') | models.Q(organization_status='active')) & ((models.Q(organization_status='merged') & models.Q(merged_into__isnull=False)) | (~models.Q(organization_status='merged') & models.Q(merged_into__isnull=True))), name='valid_organization_lifecycle'), models.CheckConstraint(condition=~models.Q(pk=models.F('merged_into')),name='organization_not_merged_into_self')]


class AssetEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=64, unique=True)
    subject = models.ForeignKey(AssetNode, on_delete=models.CASCADE, related_name='evidence')
    source = models.CharField(max_length=48)
    source_url = models.CharField(max_length=2000, blank=True)
    data = models.JSONField(default=dict)
    observed_at = models.DateTimeField(default=timezone.now)
    first_collected_at = models.DateTimeField(default=timezone.now)
    last_collected_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    run = models.ForeignKey('leakscope_app.Run', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-last_collected_at']
        indexes = [models.Index(fields=['subject', 'source'])]


class AssetRelationship(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(AssetNode, on_delete=models.CASCADE, related_name='outgoing')
    target = models.ForeignKey(AssetNode, on_delete=models.CASCADE, related_name='incoming')
    relation = models.CharField(max_length=48)
    evidence = models.ForeignKey(AssetEvidence, on_delete=models.CASCADE, related_name='relationships')
    confidence = models.PositiveSmallIntegerField(default=50)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source', 'target', 'relation', 'evidence'], name='unique_asset_relationship_evidence')]


class CertificateRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=64, unique=True)
    fingerprint = models.CharField(max_length=128, blank=True, db_index=True)
    serial = models.CharField(max_length=200, blank=True)
    issuer = models.JSONField(default=dict)
    domains = models.JSONField(default=list)
    data = models.JSONField(default=dict)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    observed_at = models.DateTimeField(null=True, blank=True)
    not_before = models.DateTimeField(null=True, blank=True)
    not_after = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-last_seen']


class CertificateName(models.Model):
    certificate = models.ForeignKey(CertificateRecord, on_delete=models.CASCADE, related_name='names')
    name = models.CharField(max_length=253, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['certificate', 'name'], name='unique_certificate_name')]


class CertificateWatchMatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    certificate = models.ForeignKey(CertificateRecord, on_delete=models.CASCADE, related_name='watch_matches')
    watch = models.ForeignKey('leakscope_app.WatchRule', null=True, blank=True, on_delete=models.SET_NULL, related_name='certificate_matches')
    watch_key = models.CharField(max_length=36)
    watch_name = models.CharField(max_length=200)
    matched_names = models.JSONField(default=list)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-last_seen']
        constraints = [models.UniqueConstraint(fields=['certificate', 'watch_key'], name='unique_certificate_watch_match')]
