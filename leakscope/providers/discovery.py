"""Provider presets, client construction and account metadata."""
from leakscope.shared import (
    Dict,
    ShodanClient,
    ShodanClientError,
    ZoomEyeClient,
    ZoomEyeClientError,
    get_config,
    logger,
)

queries_shodan = {
    "gitlab": 'http.title:"GitLab"',
    "elastic": 'product:"Elastic" port:9200',
    "dirs": 'http.title:"Index of /" http.status:200',
    "jenkins": 'http.title:"Dashboard [Jenkins]"',
    "mongo": 'product:"MongoDB"',
    "rsync": 'port:873 "@RSYNCD"',
    "sonarqube": 'http.title:"SonarQube"',
    "couchdb": 'product:"CouchDB"',
    "kibana": 'product:"Kibana"',
    "cassandra": 'product:"Cassandra" port:9160',
    "rethink": 'http.title:"RethinkDB Administration Console"',
    "ftp": 'port:21 "230" "anonymous"',
    "asia": 'hostname:"s3.ap-southeast-1.amazonaws.com"',
    "europe": 'hostname:"s3-eu-west-1.amazonaws.com"',
    "north america": 'hostname:"s3-us-west-2.amazonaws.com"',
    "api_key": 'http.html:"api_key" -http.title:"swagger"',
    "stripe": 'http.html:"STRIPE_KEY"',
    "secret_key": 'http.html:"secret_key" -http.title:"swagger"',
    "google_api_key": 'http.html:"google_api_key"',
    "amazons3be": 'http.html:"ListBucketResult" "amazonaws.com"',
    "angular": 'http.html:"polyfills" http.html:"main." http.html:"runtime"',
    "opensearch": 'product:"OpenSearch" port:9200',
    "grafana": 'http.title:"Grafana"',
    "prometheus": 'http.title:"Prometheus Time Series Collection and Processing Server"',
    "minio": 'http.title:"MinIO Browser" OR product:"MinIO"',
    "swagger": 'http.title:"swagger" OR http.title:"Swagger UI" OR http.title:"API Documentation"',
    "mongoexpress": 'http.title:"Mongo Express"',
    "nexus": 'http.title:"Nexus Repository Manager"',
    "artifactory": 'http.title:"Artifactory"',
    # Keep registry query simple to avoid Shodan parse issues; users can extend via custom queries.
    "registry": 'http.title:"Docker Registry"',
    "harbor": 'http.title:"Harbor"',
    "dockerapi": '"docker-distribution-api"',
    "etcd": 'product:"etcd" port:2379',
    "consul": 'product:"Consul" port:8500',
    "rabbitmq": 'http.title:"RabbitMQ Management" port:15672',
    # Single primary query; variants are handled in code fallbacks (still one page/credit).
    "solr": 'product:"Solr"',
    "gitea": 'http.title:"Gitea" port:3000',
    "gogs": 'http.title:"Gogs" port:3000',
    "azureblob": 'http.title:"Azure Blob Storage" OR product:"Azure Blob"',
    "gcsbucket": 'http.title:"Index of /storage.googleapis.com" OR product:"Google Cloud Storage"',
}


queries_zoomeye = {
    "gitlab": 'title:"GitLab" app:"GitLab"',
    "elastic": 'app:"Elasticsearch" port:9200',
    "dirs": 'title:"Index of /" status:200',
    "jenkins": 'title:"Dashboard [Jenkins]"',
    "mongo": 'app:"MongoDB"',
    "rsync": 'port:873 "@RSYNCD"',
    "sonarqube": 'title:"SonarQube"',
    "couchdb": 'app:"CouchDB"',
    "kibana": 'app:"Kibana"',
    "cassandra": 'app:"Cassandra" port:9160',
    "rethink": 'title:"RethinkDB Administration Console"',
    "ftp": 'port:21 "230" "anonymous"',
    "asia": 'hostname:"s3.ap-southeast-1.amazonaws.com"',
    "europe": 'hostname:"s3-eu-west-1.amazonaws.com"',
    "north america": 'hostname:"s3-us-west-2.amazonaws.com"',
    "api_key": 'body:"api_key"',
    "stripe": 'body:"STRIPE_KEY"',
    "secret_key": 'body:"secret_key"',
    "google_api_key": 'body:"google_api_key"',
    "amazons3be": 'body:"ListBucketResult" "amazonaws.com"',
    "angular": 'body:"polyfills" body:"main." body:"runtime"',
    "opensearch": 'app:"OpenSearch" port:9200',
    "grafana": 'title:"Grafana"',
    "prometheus": 'title:"Prometheus"',
    "minio": 'title:"MinIO Browser" OR app:"MinIO"',
    "swagger": 'title:"swagger" OR title:"Swagger UI" OR title:"API Documentation"',
    "mongoexpress": 'title:"Mongo Express"',
    "nexus": 'title:"Nexus Repository Manager"',
    "artifactory": 'title:"Artifactory"',
    "registry": 'title:"Docker Registry"',
    "harbor": 'title:"Harbor"',
    "dockerapi": '"docker-distribution-api"',
    "etcd": 'app:"etcd" port:2379',
    "consul": 'app:"Consul" port:8500',
    "rabbitmq": 'title:"RabbitMQ Management" port:15672',
    "solr": 'app:"Solr"',
    "gitea": 'title:"Gitea" port:3000',
    "gogs": 'title:"Gogs" port:3000',
    "azureblob": 'title:"Azure Blob Storage" OR app:"Azure Blob"',
    "gcsbucket": 'title:"Index of /storage.googleapis.com" OR app:"Google Cloud Storage"',
}


def get_shodan_client() -> ShodanClient:
    config = get_config()
    api_key = config['config'].get('SHODAN_API_KEY', '')
    return ShodanClient(api_key)


def get_zoomeye_client() -> ZoomEyeClient:
    config = get_config()
    api_key = config['config'].get('ZOOMEYE_API_KEY', '')
    timeout = int(config['config'].get('ZOOMEYE_TIMEOUT', 30)) if str(config['config'].get('ZOOMEYE_TIMEOUT', '')).isdigit() else 30
    return ZoomEyeClient(api_key, timeout=timeout)


def provider_queries(provider: str) -> Dict[str, str]:
    if (provider or '').lower() == 'zoomeye':
        return queries_zoomeye
    return queries_shodan


def check_credits(provider: str = "shodan"):
    provider = (provider or "shodan").lower()
    if provider == "zoomeye":
        try:
            client = get_zoomeye_client()
            info = client.userinfo() or {}
            data = info.get("data") or {}
            subscription = data.get("subscription") or info.get("subscription") or {}
            # Prefer zoomeye_points if present, else points.
            return subscription.get("zoomeye_points") or subscription.get("points") or data.get("zoomeye_points") or data.get("points") or 0
        except ZoomEyeClientError as exc:
            logger.error("Unable to fetch ZoomEye points: %s", exc)
        except Exception as exc:  # pragma: no cover - defensive catch
            logger.exception("Unexpected error while fetching ZoomEye points")
        return 0

    try:
        client = get_shodan_client()
        info = client.info()
        return info.get('query_credits', 0)
    except ShodanClientError as exc:
        logger.error("Unable to fetch Shodan credits: %s", exc)
    except Exception as exc:  # pragma: no cover - defensive catch
        logger.exception("Unexpected error while fetching Shodan credits")
    return 0


def stats(type=None, provider: str = "shodan"):
    if not type:
        return 0

    qmap = provider_queries(provider)
    query = qmap.get(type)
    if not query:
        return 0

    # For ZoomEye, live count may not be available; callers should rely on DB counts.
    if (provider or "").lower() == "zoomeye":
        return 0

    try:
        client = get_shodan_client()
        response = client.host_count(query)
        return response.get('total', 0)
    except ShodanClientError as exc:
        logger.error("Unable to fetch Shodan stats for %s: %s", type, exc)
    except Exception as exc:  # pragma: no cover - defensive catch
        logger.exception("Unexpected error while fetching Shodan stats for %s", type)
    return 0
