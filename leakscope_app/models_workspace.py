"""Explicitly saved shared investigation layouts; no credentials or navigation."""
import uuid
from django.db import models


class WorkspaceView(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    page = models.CharField(max_length=32)
    state = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        indexes = [models.Index(fields=['page', 'updated_at'], name='workspace_page_updated')]
