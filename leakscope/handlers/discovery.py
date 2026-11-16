"""Service-specific provider-banner extraction and explicit active probes."""
from leakscope.shared import (
    json,
    requests,
    size,
    BeautifulSoup,
    re,
    jsbeautifier,
    GzipFile,
    ssl,
    List,
    Request,
    urlopen,
    urlparse,
    base64,
    jxmlease,
    Rethink,
    Cassandra,
    Gitlab,
    Elastic,
    Dirs,
    Jenkins,
    Mongo,
    Rsync,
    Sonarqube,
    Couchdb,
    Kibana,
    Ftp,
    Amazonbe,
    Keys,
    Amazons3be,
    Angular,
    OpenSearch,
    Grafana,
    Prometheus,
    Minio,
    Swagger,
    MongoExpress,
    Nexus,
    Artifactory,
    Registry,
    Etcd,
    ConsulKV,
    Rabbitmq,
    Solr,
    Gitea,
    Gogs,
    AzureBlob,
    GcsBucket,
    app,
    logger,
    is_blacklisted,
    regex_str,
    context_delimiter_str,
    build_service_url,
    get_http_html,
    parse_json_payload,
    extract_ip_port,
    _http_base,
    _infer_host,
    parse_bucket,
)

def check_amazons3be(c, match, search, keyword, config):
    return_dict = {}
    parser = jxmlease.Parser()

    ip, port = extract_ip_port(match)
    files: list = []
    indicators: list = []

    if Amazons3be.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    body = get_http_html(match) or match.get('data', '')
    if not body or 'ListBucketResult' not in body:
        return return_dict

    try:
        root = parser(body)
        contents = root.get('ListBucketResult', {}).get('Contents', [])
        if isinstance(contents, dict):
            contents = [contents]

        for counter, item in enumerate(contents):
            if isinstance(item, dict):
                key_value = str(item.get('Key', ''))
            else:
                key_value = str(item)

            if keyword and key_value and keyword.lower() in key_value.lower():
                indicators.append(key_value)

            if counter < 50 and key_value:
                files.append(key_value)

        if files:
            device = Amazons3be(search=search, ip=ip, port=port, files=files, indicator=indicators)
            device.save()
            return_dict[c] = {"ip": ip, "port": port, 'files': files}
    except Exception as exc:
        logger.debug("Failed to parse Amazon S3 bucket listing for %s: %s", ip, exc)

    return return_dict


def check_rethink(c, match, search, config):
    return_dict = {}

    ip, port = extract_ip_port(match)
    databases = []

    if Rethink.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    payload = match.get('rethinkdb') or parse_json_payload(match.get('data')) or {}
    if isinstance(payload, str):
        payload = parse_json_payload(payload) or {}

    if isinstance(payload, dict):
        for database in payload.get('databases', []):
            if isinstance(database, dict):
                name = database.get('name') or database.get('db')
                if name:
                    databases.append(name)
            elif database:
                databases.append(str(database))

    # Save even when databases are empty to surface the finding in UI
    device = Rethink(search=search, ip=ip, port=port, databases=databases)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, 'databases': databases}

    return return_dict


def check_cassandra(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    keyspaces = []

    if Cassandra.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    payload = match.get('cassandra') or parse_json_payload(match.get('data')) or {}
    if isinstance(payload, str):
        payload = parse_json_payload(payload) or {}

    if isinstance(payload, dict):
        for keyspace in payload.get('keyspaces', []):
            if isinstance(keyspace, dict):
                name = keyspace.get('name')
                if name:
                    keyspaces.append(name)
            elif keyspace:
                keyspaces.append(str(keyspace))

    # Persist even when keyspaces are unknown so preview/export can still be attempted.
    device = Cassandra(search=search, ip=ip, port=port, keyspaces=keyspaces)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, 'keyspaces': keyspaces}

    return return_dict


def check_etcd(c, match, search, config, active_probe: bool = False):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if Etcd.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    scheme_used = "http"
    version = ""
    status = ""
    auth_required = False
    keys_sample: List[str] = []

    if not active_probe:
        device = Etcd(search=search, ip=ip, port=port, scheme=scheme_used, version=version, status=status,
                      keys_sample=json.dumps(keys_sample), auth_required=auth_required)
        device.save()
        return_dict[c] = {"ip": ip, "port": port, "auth_required": auth_required}
        return return_dict

    def _collect_keys(node) -> None:
        if isinstance(node, dict):
            key = node.get("key")
            if key:
                keys_sample.append(key)
            for child in node.get("nodes", []) or []:
                _collect_keys(child)

    def _base(scheme: str) -> str:
        return _http_base(ip, port, scheme=scheme)

    # try http then https
    for scheme in ("http", "https"):
        try:
            base = _base(scheme)
            ver_resp = requests.get(f"{base}/version", timeout=4, verify=False)
            if ver_resp.status_code in (401, 403):
                auth_required = True
                continue
            if ver_resp.ok and isinstance(ver_resp.json(), dict):
                version = ver_resp.json().get("etcdcluster", "") or ver_resp.json().get("etcdserver", "")
                scheme_used = scheme
            health_resp = requests.get(f"{base}/health", timeout=4, verify=False)
            if health_resp.status_code in (401, 403):
                auth_required = True
            elif health_resp.ok:
                status = str(health_resp.text or "")
            # try v2 keys
            try:
                kv_resp = requests.get(f"{base}/v2/keys/?recursive=true&sorted=true&limit=50", timeout=5, verify=False)
                if kv_resp.status_code in (401, 403):
                    auth_required = True
                elif kv_resp.status_code == 404:
                    # try v3
                    payload = {"key": "", "range_end": "", "limit": 50}
                    v3 = requests.post(f"{base}/v3/kv/range", json=payload, timeout=5, verify=False)
                    if v3.status_code in (401, 403):
                        auth_required = True
                    elif v3.ok:
                        try:
                            data = v3.json()
                            for kv in data.get("kvs", []) or []:
                                k = kv.get("key")
                                if k:
                                    keys_sample.append(base64.b64decode(k).decode(errors="ignore"))
                        except Exception:
                            pass
                elif kv_resp.ok:
                    try:
                        data = kv_resp.json()
                        _collect_keys(data.get("node", {}))
                    except Exception:
                        pass
            except Exception:
                pass
            break
        except Exception:
            continue

    device = Etcd(search=search, ip=ip, port=port, scheme=scheme_used, version=version, status=status,
                  keys_sample=json.dumps(keys_sample), auth_required=auth_required)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, "version": version, "status": status, "keys": keys_sample,
                      "auth_required": auth_required}
    return return_dict


def check_consul(c, match, search, config, active_probe: bool = False):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if ConsulKV.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    scheme_used = "http"
    datacenters: List[str] = []
    services: List[str] = []
    kv_roots: List[str] = []
    auth_required = False

    if not active_probe:
        device = ConsulKV(search=search, ip=ip, port=port, scheme=scheme_used,
                          datacenters=json.dumps(datacenters), services=json.dumps(services),
                          kv_roots=json.dumps(kv_roots), auth_required=auth_required)
        device.save()
        return_dict[c] = {"ip": ip, "port": port, "auth_required": auth_required}
        return return_dict

    for scheme in ("http", "https"):
        base = _http_base(ip, port, scheme=scheme)
        try:
            dc_resp = requests.get(f"{base}/v1/catalog/datacenters", timeout=4, verify=False)
            if dc_resp.status_code in (401, 403):
                auth_required = True
                continue
            if dc_resp.ok and isinstance(dc_resp.json(), list):
                datacenters = list(dc_resp.json()[:50])
                scheme_used = scheme
            svc_resp = requests.get(f"{base}/v1/catalog/services", timeout=4, verify=False)
            if svc_resp.status_code in (401, 403):
                auth_required = True
            elif svc_resp.ok and isinstance(svc_resp.json(), dict):
                services = list(svc_resp.json().keys())[:50]
            kv_resp = requests.get(f"{base}/v1/kv/?keys&limit=50", timeout=5, verify=False)
            if kv_resp.status_code in (401, 403):
                auth_required = True
            elif kv_resp.ok and isinstance(kv_resp.json(), list):
                kv_roots = list(kv_resp.json()[:50])
            break
        except Exception:
            continue

    device = ConsulKV(search=search, ip=ip, port=port, scheme=scheme_used, datacenters=json.dumps(datacenters),
                      services=json.dumps(services), kv_roots=json.dumps(kv_roots), auth_required=auth_required)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, "datacenters": datacenters, "services": services,
                      "kv_roots": kv_roots, "auth_required": auth_required}
    return return_dict


def check_rabbitmq(c, match, search, config, active_probe: bool = False):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if Rabbitmq.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    scheme_used = "http"
    version = ""
    vhosts: List[str] = []
    queues: List[str] = []
    auth_required = False

    if not active_probe:
        device = Rabbitmq(search=search, ip=ip, port=port, scheme=scheme_used, version=version,
                          vhosts=json.dumps(vhosts), queues=json.dumps(queues), auth_required=auth_required)
        device.save()
        return_dict[c] = {"ip": ip, "port": port, "auth_required": auth_required}
        return return_dict

    for scheme in ("http", "https"):
        base = _http_base(ip, port, scheme=scheme)
        try:
            overview = requests.get(f"{base}/api/overview", timeout=4, verify=False)
            if overview.status_code in (401, 403):
                auth_required = True
                continue
            if overview.ok and isinstance(overview.json(), dict):
                version = overview.json().get("rabbitmq_version", "") or overview.json().get("management_version", "")
                scheme_used = scheme
            vh = requests.get(f"{base}/api/vhosts", timeout=4, verify=False)
            if vh.status_code in (401, 403):
                auth_required = True
            elif vh.ok and isinstance(vh.json(), list):
                vhosts = [item.get("name", "") for item in vh.json() if item.get("name")] if isinstance(vh.json()[0], dict) else vh.json()[:50]
            qresp = requests.get(f"{base}/api/queues", timeout=5, verify=False)
            if qresp.status_code in (401, 403):
                auth_required = True
            elif qresp.ok and isinstance(qresp.json(), list):
                for q in qresp.json()[:50]:
                    if isinstance(q, dict) and q.get("name"):
                        queues.append(q.get("name"))
            break
        except Exception:
            continue

    device = Rabbitmq(search=search, ip=ip, port=port, scheme=scheme_used, version=version,
                      vhosts=json.dumps(vhosts), queues=json.dumps(queues), auth_required=auth_required)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, "version": version, "vhosts": vhosts, "queues": queues,
                      "auth_required": auth_required}
    return return_dict


def check_azureblob(c, match, search, config, active_probe: bool = False):
    return_dict = {}
    ip, port = extract_ip_port(match)
    host = _infer_host(match)
    if AzureBlob.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    scheme_used = "https"
    containers: List[str] = []
    auth_required = False

    if not active_probe:
        device = AzureBlob(search=search, ip=ip, port=port, scheme=scheme_used, account=host,
                           containers=json.dumps(containers), auth_required=auth_required)
        device.save()
        return_dict[c] = {"ip": ip, "port": port, "account": host, "auth_required": auth_required}
        return return_dict

    base = f"https://{host}"
    try:
        resp = requests.get(f"{base}/?comp=list&maxresults=50", timeout=8, verify=False)
        if resp.status_code in (401, 403):
            auth_required = True
        elif resp.ok and "ListContainers" in resp.text:
            try:
                parsed = jxmlease.Parser()(resp.text)
                conts = parsed.get("EnumerationResults", {}).get("Containers", {}).get("Container", [])
                if isinstance(conts, dict):
                    conts = [conts]
                for container in conts[:50]:
                    if isinstance(container, dict):
                        name = container.get("Name")
                        if name:
                            containers.append(str(name))
                    elif container:
                        containers.append(str(container))
            except Exception:
                pass
    except Exception:
        pass

    device = AzureBlob(search=search, ip=ip, port=port, scheme=scheme_used, account=host,
                       containers=json.dumps(containers), auth_required=auth_required)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, "account": host, "containers": containers, "auth_required": auth_required}
    return return_dict


def check_gcsbucket(c, match, search, config, active_probe: bool = False):
    return_dict = {}
    ip, port = extract_ip_port(match)
    host = _infer_host(match)
    if GcsBucket.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    scheme_used = "https"
    bucket_name = host
    objects: List[str] = []
    auth_required = False

    if not active_probe:
        device = GcsBucket(search=search, ip=ip, port=port, scheme=scheme_used, bucket=bucket_name,
                           objects_list=json.dumps(objects), auth_required=auth_required)
        device.save()
        return_dict[c] = {"ip": ip, "port": port, "bucket": bucket_name, "auth_required": auth_required}
        return return_dict

    base = f"https://{host}"
    try:
        resp = requests.get(f"{base}/", timeout=8, verify=False)
        if resp.status_code in (401, 403):
            auth_required = True
        elif resp.ok and "ListBucketResult" in resp.text:
            try:
                parsed = jxmlease.Parser()(resp.text)
                contents = parsed.get("ListBucketResult", {}).get("Contents", [])
                if isinstance(contents, dict):
                    contents = [contents]
                for idx, item in enumerate(contents):
                    if idx >= 50:
                        break
                    if isinstance(item, dict):
                        key_val = item.get("Key")
                        if key_val:
                            objects.append(str(key_val))
                    elif item:
                        objects.append(str(item))
            except Exception:
                pass
    except Exception:
        pass

    device = GcsBucket(search=search, ip=ip, port=port, scheme=scheme_used, bucket=bucket_name,
                       objects_list=json.dumps(objects), auth_required=auth_required)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, "bucket": bucket_name, "objects": objects, "auth_required": auth_required}
    return return_dict


def check_solr(c, match, search, config, active_probe: bool = False):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if Solr.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    scheme_used = "http"
    version = ""
    core_names: List[str] = []
    auth_required = False

    if not active_probe:
        device = Solr(search=search, ip=ip, port=port, scheme=scheme_used, version=version,
                      core_count=None, cores=json.dumps(core_names), auth_required=auth_required)
        device.save()
        return_dict[c] = {"ip": ip, "port": port, "auth_required": auth_required}
        return return_dict

    for scheme in ("http", "https"):
        base = _http_base(ip, port, scheme=scheme)
        try:
            cores_resp = requests.get(f"{base}/solr/admin/cores?action=STATUS&wt=json", timeout=6, verify=False)
            if cores_resp.status_code in (401, 403):
                auth_required = True
                continue
            if cores_resp.ok and isinstance(cores_resp.json(), dict):
                status = cores_resp.json().get("status", {})
                if isinstance(status, dict):
                    for name in list(status.keys())[:50]:
                        core_names.append(name)
                version = cores_resp.headers.get("X-Solr-Version", "") or cores_resp.json().get("responseHeader", {}).get("server", "")
                scheme_used = scheme
                break
        except Exception:
            continue

    device = Solr(search=search, ip=ip, port=port, scheme=scheme_used, version=version,
                  core_count=len(core_names) if core_names else None, cores=json.dumps(core_names), auth_required=auth_required)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, "cores": core_names, "version": version, "auth_required": auth_required}
    return return_dict


def check_gitea(c, match, search, config, active_probe: bool = False, product_hint: str = "gitea"):
    return_dict = {}
    ip, port = extract_ip_port(match)
    Model = Gitea if product_hint == "gitea" else Gogs
    if Model.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    scheme_used = "http"
    version = ""
    repos: List[str] = []
    users: List[str] = []
    auth_required = False

    if not active_probe:
        device = Model(search=search, ip=ip, port=port, scheme=scheme_used, version=version,
                       repos=json.dumps(repos), users=json.dumps(users), auth_required=auth_required)
        device.save()
        return_dict[c] = {"ip": ip, "port": port, "auth_required": auth_required}
        return return_dict

    for scheme in ("http", "https"):
        base = _http_base(ip, port, scheme=scheme)
        try:
            root = requests.get(base, timeout=6, verify=False)
            if root.status_code in (401, 403):
                auth_required = True
            if root.ok:
                scheme_used = scheme
                # Best effort parse version from headers or body
                version = root.headers.get("Server", "") or ""
            # Repos search
            repo_resp = requests.get(f"{base}/api/v1/repos/search?limit=50", timeout=6, verify=False)
            if repo_resp.status_code in (401, 403):
                auth_required = True
            elif repo_resp.ok and isinstance(repo_resp.json(), dict):
                data = repo_resp.json().get("data", []) or repo_resp.json().get("repos", [])
                for r in data:
                    if isinstance(r, dict) and r.get("full_name"):
                        repos.append(r.get("full_name"))
            # Users search
            user_resp = requests.get(f"{base}/api/v1/users/search?limit=50", timeout=6, verify=False)
            if user_resp.status_code in (401, 403):
                auth_required = True
            elif user_resp.ok and isinstance(user_resp.json(), dict):
                data = user_resp.json().get("data", []) or user_resp.json().get("users", [])
                for u in data:
                    if isinstance(u, dict) and u.get("username"):
                        users.append(u.get("username"))
            break
        except Exception:
            continue

    device = Model(search=search, ip=ip, port=port, scheme=scheme_used, version=version,
                   repos=json.dumps(repos), users=json.dumps(users), auth_required=auth_required)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, "version": version, "repos": repos, "users": users,
                      "auth_required": auth_required}
    return return_dict


def check_ftp(c, match, search, keyword, config):
    return_dict = {}
    files: list = []
    indicator: list = []

    ip, port = extract_ip_port(match)

    if Ftp.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    ftp_payload = match.get('ftp') or parse_json_payload(match.get('data')) or {}
    if isinstance(ftp_payload, str):
        ftp_payload = parse_json_payload(ftp_payload) or {}

    entries = ftp_payload.get('files') or ftp_payload.get('content') or []

    # Shodan's FTP module often only reports feature flags/anonymous access, not listings.
    if not entries and isinstance(ftp_payload, dict):
        feature_names = list((ftp_payload.get('features') or {}).keys())
        if feature_names:
            files.extend(feature_names)
        if ftp_payload.get('anonymous'):
            indicator.append('anonymous')

    def walk(nodes, parent_chain=None):
        parent_chain = parent_chain or []
        for node in nodes:
            if isinstance(node, dict):
                name = node.get('name')
                node_type = node.get('type')
                child_content = node.get('content') or []
            else:
                name = str(node)
                node_type = None
                child_content = []

            if not name:
                continue

            lower_name = name.lower()
            if keyword and keyword.lower() in lower_name:
                indicator.extend(parent_chain + [name])

            if node_type == 'd':
                files.append(name)
                if child_content:
                    walk(child_content, parent_chain + [name])

    if isinstance(entries, list):
        walk(entries)

    files = [entry for entry in files if entry not in ('.', '..')]

    device = Ftp(search=search, ip=ip, port=port, files=files, indicator=indicator)
    device.save()
    return_dict[c] = {"ip": ip, "port": port, 'files': files}

    return return_dict


def check_kibana(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)

    if Kibana.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    device = Kibana(search=search, ip=ip, port=port)
    device.save()

    return_dict[c] = {"ip": ip, "port": port}

    return return_dict


def check_couchdb(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)

    if Couchdb.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    device = Couchdb(search=search, ip=ip, port=port)
    device.save()

    return_dict[c] = {"ip": ip, "port": port}

    return return_dict


def check_sonarqube(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)

    if Sonarqube.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    url = build_service_url(match)

    device = Sonarqube(search=search, url=url, ip=ip, port=port)
    device.save()

    return_dict[c] = {"url": url, "ip": ip, "port": port}

    return return_dict


def check_rsync(c, match, search, config):
    return_dict = {}
    shares = []
    ip, port = extract_ip_port(match)

    if Rsync.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    banner = match.get('data') or ''
    if isinstance(banner, bytes):
        banner = banner.decode('utf-8', errors='ignore')

    for line in str(banner).splitlines():
        cleaned = line.strip()
        if cleaned:
            shares.append(cleaned)

    device = Rsync(search=search, ip=ip, port=port, shares=shares)
    device.save()

    return_dict[c] = {"ip": ip, "port": port, "shares": shares}

    return return_dict


def check_jenkins(c, match, search, keyword, config):
    return_dict = {}
    jobs = []
    indicators = []
    ip, port = extract_ip_port(match)

    if Jenkins.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    html_code = get_http_html(match)
    url = build_service_url(match)

    if html_code:
        try:
            soup = BeautifulSoup(html_code, features="html.parser")
            for project in soup.find_all("a", {"class": "model-link inside"}):
                href = project.get('href', '')
                if href.startswith("job"):
                    splitted = href.split("/")
                    if len(splitted) > 1:
                        job_name = splitted[1]
                        jobs.append(job_name)
                        if keyword and keyword.lower() in job_name.lower():
                            indicators.append(job_name)
        except Exception as exc:
            logger.debug("Failed to parse Jenkins HTML for %s: %s", ip, exc)

    device = Jenkins(search=search, url=url, ip=ip, port=port, jobs=jobs, indicator=indicators)
    if html_code or jobs:
        device.save()
        return_dict[c] = {"url": url, "ip": ip, "port": port, "jobs": jobs}

    return return_dict


@app.task(name="leakscope.tasks.check_mongo")
def check_mongo(c, match, search, keyword, config):
    return_dict = {}
    dbs = []
    indicators = []
    bytes_size = 0

    ip, port = extract_ip_port(match)

    if Mongo.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    mongo_payload = match.get('mongodb') or parse_json_payload(match.get('data')) or {}
    if isinstance(mongo_payload, str):
        mongo_payload = parse_json_payload(mongo_payload) or {}
    databases_info = {}
    if isinstance(mongo_payload, dict):
        databases_info = mongo_payload.get('listDatabases') or mongo_payload.get('databases') or {}

    databases = databases_info.get('databases') if isinstance(databases_info, dict) else []

    for entry in databases or []:
        if isinstance(entry, dict):
            name = entry.get('name')
            size_on_disk = entry.get('sizeOnDisk', 0)
        else:
            name = str(entry)
            size_on_disk = 0

        if not name:
            continue

        if isinstance(size_on_disk, (int, float)):
            bytes_size += size_on_disk

        dbs.append(name)
        if keyword and keyword.lower() in name.lower():
            indicators.append(name)

    size_readable = size(bytes_size) if bytes_size else "0B"
    mongo_url = f"mongodb://{ip}:{port}" if port else f"mongodb://{ip}"

    device = Mongo(search=search, ip=ip, port=port, databases=dbs, size=str(size_readable), indicator=indicators)
    if dbs or bytes_size:
        device.save()
        return_dict[c] = {"url": mongo_url, "ip": ip, "port": port, 'databases': dbs, "size": str(size_readable)}

    return return_dict


def parser_file(content, regex_str, keyword, mode=1, more_regex=None, no_dup=1):
    '''
    Parse Input
    content:    string of content to be searched
    regex_str:  string of regex (The link should be in the group(1))
    mode:       mode of parsing. Set 1 to include surrounding contexts in the result
    more_regex: string of regex to filter the result
    no_dup:     remove duplicated link (context is NOT counted)
    Return the list of ["link": link, "context": context]
    The context is optional if mode=1 is provided.
    '''
    global context_delimiter_str

    if mode == 1:
        if len(content) > 1000000:
            content = content.replace(";", ";\n").replace(",", ",\n")
        else:
            content = jsbeautifier.beautify(content)

    regex = re.compile(regex_str, re.VERBOSE)

    if mode == 1:
        all_matches = [(m.group(1), m.start(0), m.end(0)) for m in re.finditer(regex, content)]
        items = getContext(all_matches, content, context_delimiter_str=context_delimiter_str)
    else:
        items = [{"link": m.group(1)} for m in re.finditer(regex, content)]

    if no_dup:
        # Remove duplication
        all_links = set()
        no_dup_items = []
        for item in items:
            if item["link"] not in all_links:
                all_links.add(item["link"])
                no_dup_items.append(item)
        items = no_dup_items

    # Match Regex
    filtered_items = []
    for item in items:
        # Remove other capture groups from regex results
        if more_regex:
            if re.search(more_regex, item["link"]):
                filtered_items.append(item)
        else:
            filtered_items.append(item)

    return filtered_items


@app.task(name="leakscope.tasks.check_dir")
def check_dir(c, match, search, keyword, config):
    return_dict = {}
    indicators = []
    dirs = []

    ip, port = extract_ip_port(match)

    if Dirs.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    html_code = get_http_html(match)
    url = build_service_url(match)

    if html_code:
        try:
            soup = BeautifulSoup(html_code, features="html.parser")
            for project in soup.find_all("a", href=True):
                text = project.get_text().strip()
                if text in {"Name", "Last modified", "Size", "Description", "Parent Directory"}:
                    continue
                if text:
                    dirs.append(text)
                    if keyword and keyword.lower() in text.lower():
                        indicators.append(text)
        except Exception as exc:
            logger.debug("Failed to parse directory listing for %s: %s", ip, exc)

    device = Dirs(search=search, url=url, ip=ip, port=port, dirs=dirs, indicator=indicators)
    if dirs:
        device.save()
        return_dict[c] = {"url": url, "ip": ip, "port": port, 'dirs': dirs}

    return return_dict


def send_request(url):
    if urlparse(url).scheme not in ('http', 'https'):
        raise ValueError('Only HTTP(S) JavaScript URLs are supported')
    chunks = []
    length = 0
    with requests.get(url, timeout=(5, 15), stream=True) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=65536):
            length += len(chunk)
            if length > 8 * 1024 * 1024:
                raise ValueError('JavaScript response exceeds the 8 MiB limit')
            chunks.append(chunk)
    return b''.join(chunks).decode('utf-8', 'replace')


@app.task(name="leakscope.tasks.check_angular")
def check_angular(c, match, search, keyword, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    path = ""
    title = ""
    if Angular.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    try:
        html = get_http_html(match)
        if not html:
            return return_dict

        soup = BeautifulSoup(html, features="html.parser")

        for src in soup.find_all('script', {"src": True}):
            src_value = src.get('src', '')
            if 'main' in src_value:
                path = src_value
                break

        title_tag = soup.find('title')
        if title_tag and title_tag.contents:
            title = title_tag.contents[0].strip()

        if path or title:
            device = Angular(search=search, ip=ip, port=port, title=title, path=path)
            device.save()

            base_url = build_service_url(match)
            full_path = base_url + (path if path.startswith('/') else ('/' + path if path else ''))
            return_dict[c] = {"ip": ip, "port": port, 'title': title, 'path': full_path}
    except Exception as exc:
        logger.debug("Failed to parse Angular app for %s: %s", ip, exc)

    return return_dict


@app.task(name="leakscope.tasks.check_elastic")
def check_elastic(c, match, search, keyword, config, active_probe=False, result_model=None):
    ResultModel = result_model or Elastic
    return_dict = {}
    indices_list = []
    indicators = []
    bytes_size = 0

    ip, port = extract_ip_port(match)

    if ResultModel.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    elastic_payload = match.get('elastic') or parse_json_payload(match.get('data')) or {}
    if isinstance(elastic_payload, str):
        elastic_payload = parse_json_payload(elastic_payload) or {}

    http_payload = parse_json_payload(get_http_html(match))
    if not elastic_payload and http_payload:
        elastic_payload = http_payload

    def probe_elastic(ip, port):
        """Best-effort probe to fetch cluster info/indices if Shodan data is sparse."""
        base_url = f"http://{ip}:{port}"
        try:
            health = requests.get(f"{base_url}/_cluster/health", timeout=5)
            if health.ok:
                hp = health.json()
                name = hp.get("cluster_name") or hp.get("cluster_name_inferred")
            else:
                name = None
        except Exception:
            name = None

        indices_payload = None
        try:
            resp = requests.get(f"{base_url}/_cat/indices?format=json&bytes=b", timeout=8)
            if resp.ok:
                indices_payload = resp.json()
        except Exception:
            indices_payload = None

        return name, indices_payload

    cluster_name = elastic_payload.get('cluster_name') or elastic_payload.get('name') if isinstance(elastic_payload, dict) else None

    if isinstance(elastic_payload, dict):
        indices = elastic_payload.get('indices', [])
        if isinstance(indices, dict):
            indices = indices.values()

        for indice in indices or []:
            if isinstance(indice, dict):
                index_name = indice.get('index_name') or indice.get('index') or indice.get('name')
                size_in_bytes = indice.get('size_in_bytes') or indice.get('store.size_in.bytes') or 0
            else:
                index_name = str(indice)
                size_in_bytes = 0

            if index_name:
                indices_list.append(index_name)
                if keyword and keyword.lower() in index_name.lower():
                    indicators.append(index_name)

            if isinstance(size_in_bytes, (int, float)):
                bytes_size += size_in_bytes

    # If we still have no indices/size, optionally try a lightweight probe against the node.
    if active_probe and not indices_list:
        probed_name, probed_indices = probe_elastic(ip, port)
        if probed_name and not cluster_name:
            cluster_name = probed_name
        if isinstance(probed_indices, list):
            for indice in probed_indices:
                if not isinstance(indice, dict):
                    continue
                index_name = indice.get('index') or indice.get('index_name') or indice.get('id')
                size_in_bytes = indice.get('store.size') or indice.get('store.size_in_bytes') or indice.get('pri.store.size') or indice.get('docs') or 0
                if index_name:
                    indices_list.append(index_name)
                    if keyword and keyword.lower() in index_name.lower():
                        indicators.append(index_name)
                try:
                    bytes_size += int(size_in_bytes)
                except Exception:
                    continue

    if not cluster_name:
        http_title = (match.get('http') or {}).get('title')
        cluster_name = http_title or "Unknown"

    new_size = size(bytes_size) if bytes_size else "0B"

    values = dict(search=search, name=cluster_name, ip=ip, port=port, size=new_size, indices=indices_list)
    if any(field.name == 'indicator' for field in ResultModel._meta.fields):
        values['indicator'] = indicators
    device = ResultModel(**values)
    device.save()
    return_dict[c] = {"name": cluster_name, "ip": ip, "port": port, 'size': new_size, "indice": indices_list}

    return return_dict


def check_opensearch(c, match, search, keyword, config, active_probe=False):
    return check_elastic(c, match, search, keyword, config, active_probe=active_probe, result_model=OpenSearch)


def check_grafana(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if Grafana.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    url = build_service_url(match)
    title = (match.get('http') or {}).get('title') or ""

    device = Grafana(search=search, ip=ip, port=port, url=url, title=title)
    device.save()
    return_dict[c] = {"url": url, "ip": ip, "port": port, "title": title}
    return return_dict


def check_prometheus(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if Prometheus.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    url = build_service_url(match)
    title = (match.get('http') or {}).get('title') or ""

    device = Prometheus(search=search, ip=ip, port=port, url=url, title=title)
    device.save()
    return_dict[c] = {"url": url, "ip": ip, "port": port, "title": title}
    return return_dict


def check_minio(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if Minio.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    url = build_service_url(match)
    title = (match.get('http') or {}).get('title') or ""
    buckets = []

    html_code = get_http_html(match)
    if html_code:
        try:
            soup = BeautifulSoup(html_code, features="html.parser")
            for tag in soup.find_all("a", href=True):
                text = tag.get_text().strip()
                href = tag.get('href', '')
                if "bucket" in href.lower() or "bucket" in text.lower():
                    buckets.append(text or href)
        except Exception:
            pass

    device = Minio(search=search, ip=ip, port=port, url=url, title=title, buckets=buckets)
    device.save()
    return_dict[c] = {"url": url, "ip": ip, "port": port, "title": title, "buckets": buckets}
    return return_dict


def check_swagger(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if Swagger.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    url = build_service_url(match)
    title = (match.get('http') or {}).get('title') or ""

    device = Swagger(search=search, ip=ip, port=port, url=url, title=title)
    device.save()
    return_dict[c] = {"url": url, "ip": ip, "port": port, "title": title}
    return return_dict


def check_mongoexpress(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if MongoExpress.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    url = build_service_url(match)
    title = (match.get('http') or {}).get('title') or ""

    device = MongoExpress(search=search, ip=ip, port=port, url=url, title=title)
    device.save()
    return_dict[c] = {"url": url, "ip": ip, "port": port, "title": title}
    return return_dict


def check_nexus(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if is_blacklisted(ip, config):
        return return_dict

    existing = Nexus.objects.filter(ip=ip, port=port, provider=search.provider).first()
    if existing:
        return_dict[c] = {"url": existing.url, "ip": existing.ip, "port": existing.port,
                          "title": existing.title, "version": existing.version}
        return return_dict

    url = build_service_url(match)
    title = (match.get('http') or {}).get('title') or ""
    version = (match.get('http') or {}).get('server') or ""
    device = Nexus(search=search, ip=ip, port=port, url=url, title=title, version=version, product="nexus")
    device.save()
    return_dict[c] = {"url": url, "ip": ip, "port": port, "title": title, "version": version}
    return return_dict


def check_artifactory(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if is_blacklisted(ip, config):
        return return_dict

    existing = Artifactory.objects.filter(ip=ip, port=port, provider=search.provider).first()
    if existing:
        return_dict[c] = {"url": existing.url, "ip": existing.ip, "port": existing.port,
                          "title": existing.title, "version": existing.version}
        return return_dict

    url = build_service_url(match)
    title = (match.get('http') or {}).get('title') or ""
    server_hdr = (match.get('http') or {}).get('server') or ""
    device = Artifactory(search=search, ip=ip, port=port, url=url, title=title, version=server_hdr, product="artifactory")
    device.save()
    return_dict[c] = {"url": url, "ip": ip, "port": port, "title": title, "version": server_hdr}
    return return_dict


def check_registry(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if is_blacklisted(ip, config):
        return return_dict

    existing = Registry.objects.filter(ip=ip, port=port, provider=search.provider).first()
    if existing:
        return_dict[c] = {
            "url": existing.url,
            "ip": existing.ip,
            "port": existing.port,
            "title": existing.title,
            "version": existing.version,
            "product": existing.product,
        }
        return return_dict

    url = build_service_url(match)
    http_info = match.get("http") or {}
    title = http_info.get('title') or ""
    server_hdr = http_info.get('server') or ""
    html = get_http_html(match) or ""
    product = "harbor" if "harbor" in (title + " " + html).lower() else "registry"
    if match.get("type") == "dockerapi":
        product = "dockerapi"
    elif match.get("type") == "harbor":
        product = "harbor"

    device = Registry(
        search=search,
        ip=ip,
        port=port,
        url=url,
        title=title,
        version=server_hdr,
        product=product,
        is_harbor=product == "harbor",
    )
    device.save()
    return_dict[c] = {"url": url, "ip": ip, "port": port, "title": title, "version": server_hdr, "product": product}
    return return_dict


def getContext(list_matches, content, include_delimiter=0, context_delimiter_str="\n"):
    '''
    Parse Input
    list_matches:       list of tuple (link, start_index, end_index)
    content:            content to search for the context
    include_delimiter   Set 1 to include delimiter in context
    '''
    items = []
    for m in list_matches:
        match_str = m[0]
        match_start = m[1]
        match_end = m[2]
        context_start_index = match_start
        context_end_index = match_end
        delimiter_len = len(context_delimiter_str)
        content_max_index = len(content) - 1

        while content[context_start_index] != context_delimiter_str and context_start_index > 0:
            context_start_index = context_start_index - 1

        while content[context_end_index] != context_delimiter_str and context_end_index < content_max_index:
            context_end_index = context_end_index + 1

        if include_delimiter:
            context = content[context_start_index: context_end_index]
        else:
            context = content[context_start_index + delimiter_len: context_end_index]

        item = {
            "link": match_str,
            "context": context
        }
        items.append(item)

    return items


def check_gitlab(c, match, search, config):
    return_dict = {}

    ip, port = extract_ip_port(match)

    if Gitlab.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    url = build_service_url(match)
    device = Gitlab(search=search, url=url, ip=ip, port=port)
    device.save()

    return_dict[c] = {"url": url, "ip": ip, "port": port}

    return return_dict


def check_keys(c, match, search, config):
    return_dict = {}

    ip, port = extract_ip_port(match)
    title = (match.get('http') or {}).get('title', '')

    if Keys.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    device = Keys(search=search, ip=ip, port=port, title=title)
    device.save()

    return_dict[c] = {"ip": ip, "port": port, 'title': title}

    return return_dict


def check_amazonbe(c, match, search, config):
    return_dict = {}

    ip, port = extract_ip_port(match)
    buckets = set()

    if Amazonbe.objects.filter(ip=ip, port=port, provider=search.provider).exists() or is_blacklisted(ip, config):
        return return_dict

    banner = get_http_html(match) or match.get('data', '')

    try:
        soup = BeautifulSoup(str(banner), "html.parser")

        for tag in soup.find_all(href=True):
            href = tag.get('href', '')
            if "amazonaws.com" in href:
                buckets.add(parse_bucket(href))

        for tag in soup.find_all("script", {"src": True}):
            src = tag.get('src', '')
            if "amazonaws.com" in src:
                buckets.add(parse_bucket(src))

        for tag in soup.find_all("img", {"src": True}):
            src = tag.get('src', '')
            if "amazonaws.com" in src:
                buckets.add(parse_bucket(src))

        meta = soup.find("meta", property="og:image")
        if meta and "amazonaws.com" in meta.get('content', ''):
            buckets.add(parse_bucket(meta['content']))
    except Exception as exc:
        logger.debug("Failed to parse Amazon references for %s: %s", ip, exc)

    if buckets:
        device = Amazonbe(search=search, ip=ip, port=port, buckets=list(buckets))
        device.save()
        return_dict[c] = {"ip": ip, "port": port, 'buckets': list(buckets)}

    return return_dict
