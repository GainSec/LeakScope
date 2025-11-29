from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0007_nexus_artifactory'),
    ]

    operations = [
        migrations.CreateModel(
            name='Registry',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.CharField(max_length=100)),
                ('port', models.CharField(max_length=100)),
                ('confirmed', models.BooleanField(default=False)),
                ('for_later', models.BooleanField(default=False)),
                ('url', models.CharField(max_length=1000, null=True)),
                ('title', models.CharField(max_length=1000, null=True)),
                ('version', models.CharField(max_length=200, null=True)),
                ('product', models.CharField(default='registry', max_length=50)),
                ('is_harbor', models.BooleanField(default=False)),
                ('auth_required', models.BooleanField(default=False)),
                ('repo_count', models.IntegerField(blank=True, null=True)),
                ('repos', models.TextField(blank=True, null=True)),
                ('last_seen', models.DateTimeField(auto_now=True)),
                ('search', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='leakscope_app.search')),
            ],
        ),
    ]
