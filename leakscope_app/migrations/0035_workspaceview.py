import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('leakscope_app', '0034_run_activity')]
    operations = [migrations.CreateModel(name='WorkspaceView', fields=[
        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
        ('name', models.CharField(max_length=120)), ('page', models.CharField(max_length=32)),
        ('state', models.JSONField(default=dict)), ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ], options={'ordering': ['name', 'id'],
                'indexes': [models.Index(fields=['page', 'updated_at'], name='workspace_page_updated')]})]
