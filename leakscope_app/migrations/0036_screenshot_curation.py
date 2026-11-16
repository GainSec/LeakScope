import uuid
from django.db import migrations,models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('leakscope_app','0035_workspaceview')]
    operations=[
        migrations.CreateModel(name='ScreenshotCollection',fields=[
            ('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),
            ('name',models.CharField(max_length=120)),('color',models.CharField(default='#70cbbf',max_length=7)),
            ('position',models.PositiveIntegerField(default=0)),('created_at',models.DateTimeField(auto_now_add=True)),
            ('updated_at',models.DateTimeField(auto_now=True))],options={'ordering':['position','name','id']}),
        migrations.CreateModel(name='ScreenshotMembership',fields=[
            ('id',models.AutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('created_at',models.DateTimeField(auto_now_add=True)),
            ('artifact',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='collection_memberships',to='leakscope_app.evidenceartifact')),
            ('collection',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='memberships',to='leakscope_app.screenshotcollection'))],
            options={'constraints':[models.UniqueConstraint(fields=('artifact','collection'),name='unique_screenshot_membership')]}),
        migrations.CreateModel(name='ScreenshotCuration',fields=[
            ('id',models.AutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('pinned',models.BooleanField(db_index=True,default=False)),('updated_at',models.DateTimeField(auto_now=True)),
            ('artifact',models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,related_name='curation',to='leakscope_app.evidenceartifact'))]),
    ]
