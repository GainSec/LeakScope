"""Build: workflows assembled from blocks, and their runs."""
import uuid
from django.db import models


class Workflow(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    # {"steps": [{"id", "block", "settings"}], "limits": {"pages", "contacts", "items"}}; order is execution order.
    definition = models.JSONField(default=dict)
    # Automatic starts. `confirmed_token` is the estimate the person approved when turning it on;
    # a definition that no longer matches it is switched off rather than run unseen.
    enabled = models.BooleanField(default=False, db_default=False, db_index=True)
    schedule_minutes = models.PositiveIntegerField(null=True, blank=True)
    confirmed_token = models.CharField(max_length=64, blank=True, default='', db_default='')
    next_run_at = models.DateTimeField(null=True, blank=True)
    cursor = models.DateTimeField(null=True, blank=True)
    last_note = models.CharField(max_length=300, blank=True, default='', db_default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']


class WorkflowRun(models.Model):
    """One execution. `definition` is a frozen copy, so later edits never change a run in progress."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(Workflow, on_delete=models.PROTECT, related_name='runs')
    run = models.OneToOneField('leakscope_app.Run', on_delete=models.CASCADE, related_name='workflow_run')
    definition = models.JSONField(default=dict)
    position = models.PositiveIntegerField(default=0)
    items = models.JSONField(default=list)
    waiting = models.JSONField(default=dict)
    spent = models.JSONField(default=dict)
    steps = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)
