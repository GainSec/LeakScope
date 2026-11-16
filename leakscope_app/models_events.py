"""Application activity scoped to one saved job."""
from django.db import models
from django.utils import timezone


class RunEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    run = models.ForeignKey('Run', on_delete=models.CASCADE, related_name='events')
    created_at = models.DateTimeField(default=timezone.now)
    level = models.CharField(max_length=16, default='info')
    event = models.CharField(max_length=64, default='activity')
    message = models.TextField()
    data = models.JSONField(default=dict)
    dedupe_key = models.CharField(max_length=160, default='', blank=True)

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['run', 'id'], name='run_event_cursor_idx')]
        constraints = [models.UniqueConstraint(fields=['run','dedupe_key'], condition=~models.Q(dedupe_key=''), name='unique_run_event_dedupe')]
