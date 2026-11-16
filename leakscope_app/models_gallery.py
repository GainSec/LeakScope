"""Persistent screenshot curation; capture bytes remain owned by EvidenceArtifact."""
import uuid
from django.db import models


class ScreenshotCollection(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    name=models.CharField(max_length=120)
    color=models.CharField(max_length=7,default='#70cbbf')
    position=models.PositiveIntegerField(default=0)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['position','name','id']


class ScreenshotMembership(models.Model):
    artifact=models.ForeignKey('leakscope_app.EvidenceArtifact',on_delete=models.CASCADE,related_name='collection_memberships')
    collection=models.ForeignKey(ScreenshotCollection,on_delete=models.CASCADE,related_name='memberships')
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['artifact','collection'],name='unique_screenshot_membership')]


class ScreenshotCuration(models.Model):
    artifact=models.OneToOneField('leakscope_app.EvidenceArtifact',on_delete=models.CASCADE,related_name='curation')
    pinned=models.BooleanField(default=False,db_index=True)
    updated_at=models.DateTimeField(auto_now=True)
