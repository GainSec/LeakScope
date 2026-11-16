import uuid
from django.db import models


class TechnologySignature(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    name=models.CharField(max_length=160)
    technology=models.CharField(max_length=100,db_index=True)
    kind=models.CharField(max_length=20,choices=[(k,k.upper()) for k in ('ja4','ja4s','ja4t','ja4ts','ouroboros')])
    value=models.CharField(max_length=1000)
    match=models.CharField(max_length=10,default='exact',choices=[('exact','Exact'),('prefix','Prefix')])
    scope=models.JSONField(default=dict)
    description=models.TextField(blank=True)
    enabled=models.BooleanField(default=True,db_index=True)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['technology','name','id']
