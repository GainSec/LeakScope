from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0010_solr_gitea_gogs'),
    ]

    operations = [
        migrations.CreateModel(
            name='AzureBlob',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(blank=True, default='', max_length=100)),
                ('scheme', models.CharField(blank=True, default='http', max_length=10)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('account', models.CharField(blank=True, default='', max_length=500)),
                ('containers', models.TextField(blank=True, null=True)),
                ('indicator', models.CharField(blank=True, default='', max_length=10000)),
                ('auth_required', models.BooleanField(default=False)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.search')),
            ],
        ),
        migrations.CreateModel(
            name='GcsBucket',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(blank=True, default='', max_length=100)),
                ('scheme', models.CharField(blank=True, default='http', max_length=10)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('bucket', models.CharField(blank=True, default='', max_length=500)),
                ('objects_list', models.TextField(blank=True, null=True)),
                ('indicator', models.CharField(blank=True, default='', max_length=10000)),
                ('auth_required', models.BooleanField(default=False)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.search')),
            ],
        ),
    ]
