from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver


# Create your models here.
class Search(models.Model):
    id = models.AutoField(primary_key=True)

    type = models.CharField(max_length=100)
    keyword = models.CharField(max_length=100)
    network = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    provider = models.CharField(max_length=20, default="shodan")
    created_on = models.DateTimeField(auto_now_add=True)


class CustomQuery(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    query_template = models.TextField()
    default_type = models.CharField(max_length=100, blank=True, default="")
    active_probe_default = models.BooleanField(default=False)
    provider = models.CharField(max_length=20, default="shodan")
    created_on = models.DateTimeField(auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=50, blank=True, default="")
    last_error = models.TextField(blank=True, default="")

    def __str__(self):
        return self.name

class Gitlab(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    url = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)

class Ftp(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    url = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    files = models.CharField(max_length=10000)
    indicator = models.CharField(max_length=10000)


class Elastic(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    name = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    indices =models.CharField(max_length=10000)
    size = models.CharField(max_length=100)
    indicator = models.CharField(max_length=10000)


class Keys(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    title = models.CharField(max_length=1000, null=True)

class Amazonbe(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    buckets = models.CharField(max_length=10000)

class AmazonBuckets(models.Model):
    bucket = models.CharField(max_length=1000)
    provider = models.CharField(max_length=20, default="shodan")
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)

class Github(models.Model):
    commit = models.CharField(max_length=1000)
    provider = models.CharField(max_length=20, default="shodan")
    path = models.CharField(max_length=1000)
    secret = models.CharField(max_length=10000)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    keyword = models.CharField(max_length=10000)

class Javascript(models.Model):
    secrets = models.CharField(max_length=1000)
    provider = models.CharField(max_length=20, default="shodan")
    path = models.CharField(max_length=10000)
    context = models.CharField(max_length=10000)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    keyword = models.CharField(max_length=10000)

class Dirs(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    dirs = models.CharField(max_length=10000)
    url = models.CharField(max_length=100)
    indicator = models.CharField(max_length=10000)

class Jenkins(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    jobs = models.CharField(max_length=10000)
    url = models.CharField(max_length=100)
    indicator = models.CharField(max_length=10000)

class Amazons3be(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    files = models.CharField(max_length=10000)
    url = models.CharField(max_length=100)
    indicator = models.CharField(max_length=10000)

class Mongo(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    databases = models.CharField(max_length=10000)
    size = models.CharField(max_length=100)
    indicator = models.CharField(max_length=10000)


class Rsync(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    shares = models.CharField(max_length=10000)

class Sonarqube(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=10000)

class Couchdb(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)

class Kibana(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)

class Cassandra(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    keyspaces = models.CharField(max_length=10000)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)

class Rethink(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    databases = models.CharField(max_length=10000)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)

class Monitor(models.Model):
    keyword = models.CharField(max_length=100)
    network = models.CharField(max_length=100)
    types = models.CharField(max_length=1000)
    created_on = models.DateTimeField(auto_now_add=True)

class Angular(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    title = models.CharField(max_length=1000, null=True)
    path =models.CharField(max_length=1000, null=True)


class OpenSearch(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    name = models.CharField(max_length=1000, null=True)
    indices = models.CharField(max_length=10000, null=True)
    size = models.CharField(max_length=100, null=True)


class Grafana(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True)
    title = models.CharField(max_length=1000, null=True)


class Prometheus(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True)
    title = models.CharField(max_length=1000, null=True)


class Minio(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True)
    title = models.CharField(max_length=1000, null=True)
    buckets = models.CharField(max_length=10000, null=True)


class Swagger(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True)
    title = models.CharField(max_length=1000, null=True)


class MongoExpress(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True)
    title = models.CharField(max_length=1000, null=True)


class Nexus(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True)
    title = models.CharField(max_length=1000, null=True)
    version = models.CharField(max_length=200, null=True)
    product = models.CharField(max_length=50, default="nexus")
    repo_count = models.IntegerField(null=True, blank=True)
    repos = models.TextField(null=True, blank=True)


class Artifactory(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True)
    title = models.CharField(max_length=1000, null=True)
    version = models.CharField(max_length=200, null=True)
    product = models.CharField(max_length=50, default="artifactory")
    repo_count = models.IntegerField(null=True, blank=True)
    repos = models.TextField(null=True, blank=True)


class Registry(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True)
    title = models.CharField(max_length=1000, null=True)
    version = models.CharField(max_length=200, null=True)
    product = models.CharField(max_length=50, default="registry")
    is_harbor = models.BooleanField(default=False)
    auth_required = models.BooleanField(default=False)
    repo_count = models.IntegerField(null=True, blank=True)
    repos = models.TextField(null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)


class Solr(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100, default="", blank=True)
    scheme = models.CharField(max_length=10, default="http", blank=True)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    core_count = models.IntegerField(null=True, blank=True)
    cores = models.TextField(null=True, blank=True)
    version = models.CharField(max_length=200, null=True, blank=True)
    indicator = models.CharField(max_length=10000, default="", blank=True)
    auth_required = models.BooleanField(default=False)


class Gitea(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100, default="", blank=True)
    scheme = models.CharField(max_length=10, default="http", blank=True)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True, blank=True)
    version = models.CharField(max_length=200, null=True, blank=True)
    repos = models.TextField(null=True, blank=True)
    users = models.TextField(null=True, blank=True)
    indicator = models.CharField(max_length=10000, default="", blank=True)
    auth_required = models.BooleanField(default=False)


class Gogs(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100, default="", blank=True)
    scheme = models.CharField(max_length=10, default="http", blank=True)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    url = models.CharField(max_length=1000, null=True, blank=True)
    version = models.CharField(max_length=200, null=True, blank=True)
    repos = models.TextField(null=True, blank=True)
    users = models.TextField(null=True, blank=True)
    indicator = models.CharField(max_length=10000, default="", blank=True)
    auth_required = models.BooleanField(default=False)


class Etcd(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100, default="", blank=True)
    scheme = models.CharField(max_length=10, default="http", blank=True)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    version = models.CharField(max_length=255, default="", blank=True)
    status = models.CharField(max_length=255, default="", blank=True)
    keys_sample = models.CharField(max_length=20000, default="", blank=True)
    indicator = models.CharField(max_length=10000, default="", blank=True)
    auth_required = models.BooleanField(default=False)


class ConsulKV(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100, default="", blank=True)
    scheme = models.CharField(max_length=10, default="http", blank=True)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    datacenters = models.CharField(max_length=5000, default="", blank=True)
    services = models.CharField(max_length=5000, default="", blank=True)
    kv_roots = models.CharField(max_length=5000, default="", blank=True)
    indicator = models.CharField(max_length=10000, default="", blank=True)
    auth_required = models.BooleanField(default=False)


class Rabbitmq(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100, default="", blank=True)
    scheme = models.CharField(max_length=10, default="http", blank=True)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    version = models.CharField(max_length=255, default="", blank=True)
    vhosts = models.CharField(max_length=5000, default="", blank=True)
    queues = models.CharField(max_length=5000, default="", blank=True)
    indicator = models.CharField(max_length=10000, default="", blank=True)
    auth_required = models.BooleanField(default=False)


class AzureBlob(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100, default="", blank=True)
    scheme = models.CharField(max_length=10, default="http", blank=True)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    account = models.CharField(max_length=500, default="", blank=True)
    containers = models.TextField(blank=True, null=True)
    indicator = models.CharField(max_length=10000, default="", blank=True)
    auth_required = models.BooleanField(default=False)


class GcsBucket(models.Model):
    search = models.ForeignKey(Search, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, default="shodan")
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=100, default="", blank=True)
    scheme = models.CharField(max_length=10, default="http", blank=True)
    confirmed = models.BooleanField(default=False)
    for_later = models.BooleanField(default=False)
    bucket = models.CharField(max_length=500, default="", blank=True)
    objects_list = models.TextField(blank=True, null=True)
    indicator = models.CharField(max_length=10000, default="", blank=True)
    auth_required = models.BooleanField(default=False)


class BlacklistEntry(models.Model):
    value = models.CharField(max_length=1000, unique=True)
    created_on = models.DateTimeField(auto_now_add=True)


class FingerprintLog(models.Model):
    fingerprint = models.CharField(max_length=128, unique=True)
    type = models.CharField(max_length=100)
    ip = models.CharField(max_length=100)
    port = models.CharField(max_length=50, blank=True, null=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    seen_count = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, default="new")


@receiver(pre_save)
def ensure_provider(sender, instance, **kwargs):
    """
    Best-effort: if a model has both `provider` and `search`, default provider from the search when missing.
    """
    try:
        if hasattr(instance, "provider") and hasattr(instance, "search"):
            if not getattr(instance, "provider", "") and getattr(instance, "search", None):
                instance.provider = getattr(instance.search, "provider", "shodan") or "shodan"
    except Exception:
        # Do not block saves on errors here.
        pass
