from django.db import migrations, models


def add_provider_default(apps, schema_editor):
    # Populate existing rows with default provider "shodan"
    model_names = [
        'Gitlab', 'Ftp', 'Elastic', 'Keys', 'Amazonbe', 'AmazonBuckets', 'Github', 'Javascript', 'Dirs', 'Jenkins',
        'Amazons3be', 'Mongo', 'Rsync', 'Sonarqube', 'Couchdb', 'Kibana', 'Cassandra', 'Rethink', 'Angular',
        'OpenSearch', 'Grafana', 'Prometheus', 'Minio', 'Swagger', 'MongoExpress', 'Nexus', 'Artifactory', 'Registry',
        'Solr', 'Gitea', 'Gogs', 'Etcd', 'ConsulKV', 'Rabbitmq', 'AzureBlob', 'GcsBucket'
    ]
    for name in model_names:
        Model = apps.get_model('leakscope_app', name)
        Model.objects.filter(provider='').update(provider='shodan')


class Migration(migrations.Migration):

    dependencies = [
        ('leakscope_app', '0009_provider_fields'),
    ]

    operations = []

    for model_name in [
        'gitlab', 'ftp', 'elastic', 'keys', 'amazonbe', 'amazonbuckets', 'github', 'javascript', 'dirs', 'jenkins',
        'amazons3be', 'mongo', 'rsync', 'sonarqube', 'couchdb', 'kibana', 'cassandra', 'rethink', 'angular',
        'opensearch', 'grafana', 'prometheus', 'minio', 'swagger', 'mongoexpress', 'nexus', 'artifactory', 'registry',
        'solr', 'gitea', 'gogs', 'etcd', 'consulkv', 'rabbitmq', 'azureblob', 'gcsbucket'
    ]:
        operations.append(
            migrations.AddField(
                model_name=model_name,
                name='provider',
                field=models.CharField(default='shodan', max_length=20),
            )
        )

    operations.append(migrations.RunPython(add_provider_default, migrations.RunPython.noop))
