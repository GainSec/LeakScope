from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0008_registry'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConsulKV',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(blank=True, default='', max_length=100)),
                ('scheme', models.CharField(blank=True, default='http', max_length=10)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('datacenters', models.CharField(blank=True, default='', max_length=5000)),
                ('services', models.CharField(blank=True, default='', max_length=5000)),
                ('kv_roots', models.CharField(blank=True, default='', max_length=5000)),
                ('indicator', models.CharField(blank=True, default='', max_length=10000)),
                ('auth_required', models.BooleanField(default=False)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.search')),
            ],
        ),
        migrations.CreateModel(
            name='Etcd',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(blank=True, default='', max_length=100)),
                ('scheme', models.CharField(blank=True, default='http', max_length=10)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('version', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(blank=True, default='', max_length=255)),
                ('keys_sample', models.CharField(blank=True, default='', max_length=20000)),
                ('indicator', models.CharField(blank=True, default='', max_length=10000)),
                ('auth_required', models.BooleanField(default=False)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.search')),
            ],
        ),
        migrations.CreateModel(
            name='Rabbitmq',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(blank=True, default='', max_length=100)),
                ('scheme', models.CharField(blank=True, default='http', max_length=10)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('version', models.CharField(blank=True, default='', max_length=255)),
                ('vhosts', models.CharField(blank=True, default='', max_length=5000)),
                ('queues', models.CharField(blank=True, default='', max_length=5000)),
                ('indicator', models.CharField(blank=True, default='', max_length=10000)),
                ('auth_required', models.BooleanField(default=False)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.search')),
            ],
        ),
    ]
