from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0002_grafana_minio_mongoexpress_opensearch_prometheus_swagger'),
    ]

    operations = [
        migrations.CreateModel(
            name='BlacklistEntry',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('value', models.CharField(max_length=1000, unique=True)),
                ('created_on', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
