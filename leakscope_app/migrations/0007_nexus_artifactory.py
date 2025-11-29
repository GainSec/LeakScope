from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0006_customquery'),
    ]

    operations = [
        migrations.CreateModel(
            name='Artifactory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(max_length=100)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('url', models.CharField(max_length=1000, null=True)),
                ('title', models.CharField(max_length=1000, null=True)),
                ('version', models.CharField(max_length=200, null=True)),
                ('product', models.CharField(default='artifactory', max_length=50)),
                ('repo_count', models.IntegerField(blank=True, null=True)),
                ('repos', models.TextField(blank=True, null=True)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.Search')),
            ],
        ),
        migrations.CreateModel(
            name='Nexus',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(max_length=100)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('url', models.CharField(max_length=1000, null=True)),
                ('title', models.CharField(max_length=1000, null=True)),
                ('version', models.CharField(max_length=200, null=True)),
                ('product', models.CharField(default='nexus', max_length=50)),
                ('repo_count', models.IntegerField(blank=True, null=True)),
                ('repos', models.TextField(blank=True, null=True)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.Search')),
            ],
        ),
    ]
