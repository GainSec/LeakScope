from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0005_fingerprintlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomQuery',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, default='')),
                ('query_template', models.TextField()),
                ('default_type', models.CharField(blank=True, default='', max_length=100)),
                ('active_probe_default', models.BooleanField(default=False)),
                ('created_on', models.DateTimeField(auto_now_add=True)),
                ('updated_on', models.DateTimeField(auto_now=True)),
                ('last_run_at', models.DateTimeField(blank=True, null=True)),
                ('last_status', models.CharField(blank=True, default='', max_length=50)),
                ('last_error', models.TextField(blank=True, default='')),
            ],
        ),
    ]
