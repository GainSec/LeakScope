"""Durable domain research and explicit review decisions."""
import uuid
from django.db import models
from django.utils import timezone


class DomainExpansion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('AssetNode', null=True, blank=True, on_delete=models.SET_NULL, related_name='domain_expansions')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL)
    run = models.OneToOneField('Run', on_delete=models.PROTECT)
    scope_key = models.CharField(max_length=64, db_index=True)
    spec = models.JSONField(default=dict)
    state = models.CharField(max_length=20, default='queued', db_index=True)
    usage = models.JSONField(default=dict)
    notes = models.JSONField(default=list)
    leads = models.JSONField(default=list)
    checkpoint = models.JSONField(default=dict, db_default={})
    coverage = models.JSONField(default=dict, db_default={})
    execution_token = models.CharField(max_length=64, default='', db_default='')
    partial = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class DomainCandidate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expansion = models.ForeignKey(DomainExpansion, on_delete=models.CASCADE, related_name='candidates')
    domain = models.CharField(max_length=253)
    root = models.CharField(max_length=253)
    relation = models.CharField(max_length=32, default='related')
    confidence = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=16, default='pending')
    evidence = models.JSONField(default=list)
    paths = models.JSONField(default=list)
    tags = models.JSONField(default=list)
    identifiers = models.JSONField(default=list)
    dns_status = models.CharField(max_length=32, default='unchecked')
    category = models.CharField(max_length=16, default='', db_default='', db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['domain']
        constraints = [models.UniqueConstraint(fields=['expansion', 'domain'], name='unique_expansion_domain')]


class DomainDecision(models.Model):
    scope_key = models.CharField(max_length=64)
    organization = models.ForeignKey('AssetNode', null=True, on_delete=models.SET_NULL)
    domain = models.CharField(max_length=253)
    status = models.CharField(max_length=16)
    tags = models.JSONField(default=list)
    candidate = models.ForeignKey(DomainCandidate, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['scope_key', 'domain'], name='unique_domain_review_scope')]


class DomainSourceCache(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    data = models.JSONField(default=dict)
    expires_at = models.DateTimeField(db_index=True)


class ExpansionSourcePage(models.Model):
    """Retain collected names before review materialization, including paused work."""
    expansion = models.ForeignKey(DomainExpansion, on_delete=models.CASCADE, related_name='source_pages')
    key = models.CharField(max_length=64)
    source = models.CharField(max_length=32)
    seed = models.JSONField(default=dict)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['expansion', 'key'], name='unique_expansion_source_page')]
