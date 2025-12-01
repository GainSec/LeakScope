from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0009_etcd_consulkv_rabbitmq'),
    ]

    operations = [
        migrations.CreateModel(
            name='Solr',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(blank=True, default='', max_length=100)),
                ('scheme', models.CharField(blank=True, default='http', max_length=10)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('core_count', models.IntegerField(blank=True, null=True)),
                ('cores', models.TextField(blank=True, null=True)),
                ('version', models.CharField(blank=True, max_length=200, null=True)),
                ('indicator', models.CharField(blank=True, default='', max_length=10000)),
                ('auth_required', models.BooleanField(default=False)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.search')),
            ],
        ),
        migrations.CreateModel(
            name='Gitea',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(blank=True, default='', max_length=100)),
                ('scheme', models.CharField(blank=True, default='http', max_length=10)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('url', models.CharField(blank=True, max_length=1000, null=True)),
                ('version', models.CharField(blank=True, max_length=200, null=True)),
                ('repos', models.TextField(blank=True, null=True)),
                ('users', models.TextField(blank=True, null=True)),
                ('indicator', models.CharField(blank=True, default='', max_length=10000)),
                ('auth_required', models.BooleanField(default=False)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.search')),
            ],
        ),
        migrations.CreateModel(
            name='Gogs',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(blank=True, default='', max_length=100)),
                ('scheme', models.CharField(blank=True, default='http', max_length=10)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('url', models.CharField(blank=True, max_length=1000, null=True)),
                ('version', models.CharField(blank=True, max_length=200, null=True)),
                ('repos', models.TextField(blank=True, null=True)),
                ('users', models.TextField(blank=True, null=True)),
                ('indicator', models.CharField(blank=True, default='', max_length=10000)),
                ('auth_required', models.BooleanField(default=False)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.search')),
            ],
        ),
    ]
