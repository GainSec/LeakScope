from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0003_blacklistentry'),
    ]

    operations = [
        migrations.CreateModel(
            name='FingerprintLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fingerprint', models.CharField(max_length=128, unique=True)),
                ('type', models.CharField(max_length=100)),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(blank=True, max_length=50, null=True)),
                ('first_seen', models.DateTimeField(auto_now_add=True)),
                ('last_seen', models.DateTimeField(auto_now=True)),
                ('seen_count', models.PositiveIntegerField(default=1)),
                ('status', models.CharField(default='new', max_length=20)),
            ],
        ),
    ]
