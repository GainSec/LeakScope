import django.db.models.deletion
import django.utils.timezone
from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('leakscope_app','0033_durable_expansion')]
    operations=[migrations.CreateModel(
        name='RunEvent',
        fields=[('id',models.BigAutoField(primary_key=True,serialize=False)),
            ('created_at',models.DateTimeField(default=django.utils.timezone.now)),
            ('level',models.CharField(default='info',max_length=16)),
            ('event',models.CharField(default='activity',max_length=64)),
            ('message',models.TextField()),('data',models.JSONField(default=dict)),
            ('dedupe_key',models.CharField(blank=True,default='',max_length=160)),
            ('run',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='events',to='leakscope_app.run'))],
        options={'ordering':['id'],'indexes':[models.Index(fields=['run','id'],name='run_event_cursor_idx')],
            'constraints':[models.UniqueConstraint(condition=~models.Q(dedupe_key=''),fields=('run','dedupe_key'),name='unique_run_event_dedupe')]})]
