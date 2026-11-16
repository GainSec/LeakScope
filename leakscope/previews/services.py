"""Bounded service previews behind the orchestration active-probe gate."""
from leakscope.shared import (
    requests,
    BeautifulSoup,
    Any,
    Dict,
    List,
    ftplib,
    socket,
    MongoClient,
    Cluster,
    NoHostAvailable,
    ExecutionProfile,
    EXEC_PROFILE_DEFAULT,
    DCAwareRoundRobinPolicy,
    base64,
    jxmlease,
    _safe_int,
    _json_safe,
    _http_base,
    _parse_link_next,
)

def _preview_elastic(ip: str, port: Any) -> Dict[str, Any]:
    base = _http_base(ip, port, scheme="http")
    summary = ""
    items: List[Dict[str, Any]] = []
    try:
        health = requests.get(f"{base}/_cluster/health", timeout=6)
        if health.status_code in (401, 403):
            return {"summary": "auth required", "items": []}
        if health.ok:
            hp = health.json()
            summary = f"cluster={hp.get('cluster_name')} status={hp.get('status')}"
        else:
            summary = f"HTTP {health.status_code}"
    except Exception:
        summary = ""
    try:
        resp = requests.get(f"{base}/_cat/indices?format=json&bytes=b", timeout=8)
        if resp.status_code in (401, 403):
            return {"summary": "auth required", "items": []}
        if resp.ok:
            for idx in resp.json()[:10]:
                items.append({
                    "index": idx.get("index") or idx.get("id"),
                    "docs": idx.get("docs.count") or idx.get("docs"),
                    "store_bytes": idx.get("store.size") or idx.get("store.size_in_bytes") or idx.get("pri.store.size")
                })
        elif not items:
            summary = summary or f"HTTP {resp.status_code}"
    except Exception:
        pass
    # sample docs from first index
    for idx in items[:1]:
        name = idx.get("index")
        if not name:
            continue
        try:
            doc_resp = requests.get(f"{base}/{name}/_search?size=10", timeout=6)
            if doc_resp.ok:
                docs = doc_resp.json().get("hits", {}).get("hits", [])
                idx["sample_docs"] = [d.get("_source") for d in docs if isinstance(d, dict)]
        except Exception:
            continue
    return {"summary": summary or "Elastic/OpenSearch preview", "items": items}


def _preview_dirs(url: str) -> Dict[str, Any]:
    summary = ""
    items = []
    try:
        resp = requests.get(url, timeout=6)
        if resp.status_code in (401, 403):
            return {"summary": "auth required", "items": []}
        if resp.ok:
            soup = BeautifulSoup(resp.text, features="html.parser")
            for a in soup.find_all("a", href=True)[:10]:
                text = a.get_text().strip()
                href = a.get("href", "")
                if text and text not in {"Name", "Last modified", "Size", "Description", "Parent Directory"}:
                    items.append({"name": text, "href": href})
            summary = f"Found {len(items)} entries"
        else:
            summary = f"HTTP {resp.status_code}"
    except Exception:
        summary = "Failed to fetch directory listing"
    return {"summary": summary, "items": items}


def _preview_nexus(url: str) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    try:
        r = requests.get(f"{url}/service/rest/v1/repositories", timeout=8)
        if r.status_code in (401, 403):
            return {"summary": "auth required", "items": []}
        if r.ok:
            repos = r.json()
            summary = f"{len(repos)} repos"
            for repo in repos[:50]:
                items.append({"repo": repo.get("name"), "format": repo.get("format"), "type": repo.get("type")})
        else:
            summary = f"HTTP {r.status_code}"
    except Exception:
        summary = "Failed to list repos"
    return {"summary": summary or "Nexus preview", "items": items}


def _preview_artifactory(url: str) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    base = url[:-1] if url.endswith("/") else url
    try:
        r = requests.get(f"{base}/api/repositories", timeout=8)
        if r.status_code in (401, 403):
            return {"summary": "auth required", "items": []}
        if r.ok:
            repos = r.json()
            summary = f"{len(repos)} repos"
            for repo in repos[:50]:
                key = repo.get("key") or repo.get("repoKey") or repo.get("repo")
                items.append({"repo": key, "type": repo.get("type"), "packageType": repo.get("packageType")})
        else:
            summary = f"HTTP {r.status_code}"
    except Exception:
        summary = "Failed to list repos"
    return {"summary": summary or "Artifactory preview", "items": items}


def _preview_registry(url: str, repo_cap: int = 50, tag_cap: int = 20) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    base = url[:-1] if url.endswith("/") else url
    try:
        probe = requests.get(f"{base}/v2/", timeout=6)
        if probe.status_code in (401, 403):
            return {"summary": "auth required", "items": [], "auth_required": True}
        if not probe.ok:
            return {"summary": f"HTTP {probe.status_code}", "items": []}
    except Exception:
        return {"summary": "Probe failed", "items": []}

    repos: List[str] = []
    next_url = f"{base}/v2/_catalog?n={repo_cap}"
    while next_url and len(repos) < repo_cap:
        try:
            resp = requests.get(next_url, timeout=8)
            if resp.status_code in (401, 403):
                return {"summary": "auth required", "items": [], "auth_required": True}
            if not resp.ok:
                break
            payload = resp.json()
            repos.extend(payload.get("repositories", []))
            link = resp.headers.get("Link")
            nxt = _parse_link_next(link) if link else None
            if nxt:
                next_url = nxt if nxt.startswith("http") else f"{base}{nxt}"
            else:
                next_url = None
        except Exception:
            break

    repos = repos[:repo_cap]
    for repo in repos[:10]:
        try:
            tag_resp = requests.get(f"{base}/v2/{repo}/tags/list?n={tag_cap}", timeout=8)
            if tag_resp.status_code in (401, 403):
                items.append({"repo": repo, "error": "auth required"})
                continue
            if tag_resp.ok:
                payload = tag_resp.json()
                tags = payload.get("tags") or []
                items.append({"repo": repo, "tags": (tags or [])[:tag_cap]})
            else:
                items.append({"repo": repo, "error": f"HTTP {tag_resp.status_code}"})
        except Exception:
            items.append({"repo": repo, "error": "tag listing failed"})

    summary = f"Repos listed: {len(repos)}"
    return {"summary": summary, "items": items, "repo_count": len(repos)}


def _preview_gitlab(url: str) -> Dict[str, Any]:
    base = url.rstrip("/")
    targets = [base, f"{base}/users/sign_in"]
    status = []
    try:
        for t in targets:
            resp = requests.get(t, timeout=6)
            status.append({"url": t, "code": resp.status_code, "title": (BeautifulSoup(resp.text, "html.parser").title.string or "").strip() if resp.text else ""})
        return {"summary": f"Probed {len(status)} endpoints", "items": status}
    except Exception:
        return {"summary": "Probe failed", "items": []}


def _preview_sonarqube(url: str) -> Dict[str, Any]:
    base = url.rstrip("/")
    summary = ""
    items = []
    try:
        r = requests.get(f"{base}/api/system/status", timeout=6)
        if r.ok:
            payload = r.json()
            summary = f"Status={payload.get('status')}"
            items.append(payload)
    except Exception:
        summary = summary or "Status probe failed"
    try:
        v = requests.get(f"{base}/api/server/version", timeout=4)
        if v.ok:
            items.append({"version": v.text.strip()})
    except Exception:
        pass
    return {"summary": summary or "SonarQube preview", "items": items}


def _preview_kibana(url: str) -> Dict[str, Any]:
    try:
        r = requests.get(url, timeout=6)
        title = ""
        if r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            title = (soup.title.string or "").strip() if soup.title else ""
        return {"summary": f"HTTP {r.status_code}", "items": [{"title": title}]}
    except Exception:
        return {"summary": "Probe failed", "items": []}


def _preview_grafana(url: str) -> Dict[str, Any]:
    base = url.rstrip("/")
    try:
        r = requests.get(f"{base}/api/health", timeout=6)
        if r.ok:
            payload = r.json()
            return {"summary": f"status={payload.get('status')}", "items": [payload]}
        return {"summary": f"HTTP {r.status_code}", "items": []}
    except Exception:
        return {"summary": "Probe failed", "items": []}


def _preview_prometheus(url: str) -> Dict[str, Any]:
    base = url.rstrip("/")
    items = []
    summary = ""
    try:
        t = requests.get(f"{base}/api/v1/targets", timeout=6)
        if t.ok:
            data = t.json().get("data", {})
            items.append({"active": len(data.get("activeTargets", [])), "dropped": len(data.get("droppedTargets", []))})
            summary = "Targets fetched"
    except Exception:
        summary = summary or "Targets unavailable"
    try:
        r = requests.get(f"{base}/api/v1/status/runtimeinfo", timeout=6)
        if r.ok:
            items.append({"runtime": r.json().get("data", {})})
    except Exception:
        pass
    return {"summary": summary or "Prometheus preview", "items": items}


def _preview_swagger(url: str) -> Dict[str, Any]:
    try:
        r = requests.get(url, timeout=6)
        title = ""
        if r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            title = (soup.title.string or "").strip() if soup.title else ""
        return {"summary": f"HTTP {r.status_code}", "items": [{"title": title}]}
    except Exception:
        return {"summary": "Probe failed", "items": []}


def _preview_minio(url: str) -> Dict[str, Any]:
    items = []
    summary = ""
    try:
        r = requests.get(url, timeout=6)
        if r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup.find_all("a", href=True)[:10]:
                text = tag.get_text().strip() or tag.get("href", "")
                if text:
                    items.append({"bucket": text})
            summary = f"Buckets: {len(items)}"
        else:
            summary = f"HTTP {r.status_code}"
    except Exception:
        summary = "Probe failed"
    return {"summary": summary, "items": items}


def _preview_angular(url: str) -> Dict[str, Any]:
    try:
        r = requests.get(url, timeout=6)
        scripts = []
        title = ""
        if r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            for s in soup.find_all("script", src=True):
                if "main" in s.get("src", "") or "runtime" in s.get("src", ""):
                    scripts.append(s.get("src"))
            if soup.title:
                title = (soup.title.string or "").strip()
        return {"summary": f"Scripts: {len(scripts)}", "items": [{"scripts": scripts, "title": title}]}
    except Exception:
        return {"summary": "Probe failed", "items": []}


def _preview_cassandra(ip: str, port: Any) -> Dict[str, Any]:
    """
    Light-weight, no-auth preview for Cassandra.
    Uses cassandra-driver for unauthenticated system.local + schema peek if open; otherwise falls back to TCP probes.
    """
    items: List[Dict[str, Any]] = []
    notes: List[str] = []

    thrift_port = _safe_int(port, 9160)
    cql_port = _safe_int(port, 9042)
    summary_parts = []

    # Try native driver first (no auth). Prefer 9042, then provided port if different.
    attempted_ports = []
    for cport in [9042, cql_port]:
        if cport in attempted_ports:
            continue
        attempted_ports.append(cport)
        try:
            profile = ExecutionProfile(
                load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="dc1", used_hosts_per_remote_dc=0),
                request_timeout=3,
            )
            cluster = Cluster(
                contact_points=[ip],
                port=cport,
                connect_timeout=3,
                protocol_version=4,
                execution_profiles={EXEC_PROFILE_DEFAULT: profile},
                allow_beta_protocol_version=False,
            )
            session = cluster.connect()
            try:
                row = session.execute("SELECT cluster_name, data_center, rack, release_version, cql_version FROM system.local").one()
                if row:
                    items.append({"cluster": row.cluster_name, "dc": row.data_center, "rack": row.rack,
                                  "release_version": row.release_version, "cql_version": row.cql_version, "port": cport})
                    summary_parts.append("CQL open (no auth)")
            except Exception as exc:
                notes.append(f"system.local query failed: {exc.__class__.__name__}")
            try:
                ks_rows = session.execute("SELECT keyspace_name FROM system_schema.keyspaces")
                ks_list = [r.keyspace_name for r in ks_rows][:20]
                if ks_list:
                    items.append({"keyspaces": ks_list})
            except Exception as exc:
                notes.append(f"Keyspace list failed: {exc.__class__.__name__}")
            try:
                tbl_rows = session.execute("SELECT keyspace_name, table_name FROM system_schema.tables")
                tbls: Dict[str, List[str]] = {}
                for r in tbl_rows:
                    tbls.setdefault(r.keyspace_name, [])
                    if len(tbls[r.keyspace_name]) < 20:
                        tbls[r.keyspace_name].append(r.table_name)
                if tbls:
                    items.append({"tables": tbls})
            except Exception:
                pass
            cluster.shutdown()
            break
        except NoHostAvailable:
            notes.append(f"CQL driver connect failed (port {cport}): NoHostAvailable")
        except Exception as exc:
            notes.append(f"CQL driver connect failed (port {cport}): {exc.__class__.__name__}")

    # Check legacy Thrift port
    try:
        with socket.create_connection((ip, thrift_port), timeout=4):
            items.append({"tcp": "ok", "port": thrift_port, "protocol": "thrift"})
    except Exception as exc:
        notes.append(f"Thrift {thrift_port} unreachable: {exc.__class__.__name__}")

    # Check CQL port(s) and send OPTIONS to get a fingerprint
    for cport in attempted_ports:
        try:
            with socket.create_connection((ip, cport), timeout=4) as cql:
                items.append({"tcp": "ok", "port": cport, "protocol": "cql"})
                try:
                    # Native protocol v4 OPTIONS (opcode 0x05) with empty body
                    import struct
                    header = struct.pack(">bbbbI", 0x04, 0x00, 0x01, 0x05, 0)
                    cql.sendall(header)
                    resp = cql.recv(2048)
                    if resp:
                        items.append({"cql_options_hex": resp.hex()[:200], "port": cport})
                except Exception as exc:  # pragma: no cover
                    notes.append(f"CQL OPTIONS failed on {cport}: {exc.__class__.__name__}")
        except Exception as exc:
            notes.append(f"CQL {cport} unreachable: {exc.__class__.__name__}")

    if any(i.get("protocol") == "cql" for i in items):
        summary_parts.append("CQL port reachable")
    if any(i.get("protocol") == "thrift" for i in items):
        summary_parts.append("Thrift port reachable")
    if not summary_parts:
        summary_parts.append("Connection failed")
    summary = "; ".join(summary_parts)
    if notes:
        items.append({"notes": "; ".join(notes[:3])})

    return {"summary": summary, "items": items[:5]}


def _preview_rethink(ip: str, port: Any) -> Dict[str, Any]:
    try:
        with socket.create_connection((ip, _safe_int(port, 28015)), timeout=5):
            return {"summary": "TCP connection ok", "items": [{"ip": ip, "port": port}]}
    except Exception:
        return {"summary": "Connection failed", "items": []}


def _preview_keys(obj) -> Dict[str, Any]:
    title = getattr(obj, "title", "")
    return {"summary": "Stored key metadata", "items": [{"ip": obj.ip, "port": obj.port, "title": title}]}


def _preview_amazonbe(obj) -> Dict[str, Any]:
    buckets = getattr(obj, "buckets", []) or []
    if isinstance(buckets, str):
        buckets_list = buckets.split(",")
    else:
        buckets_list = list(buckets)
    buckets_list = [b.strip() for b in buckets_list if b]
    return {"summary": f"Buckets: {len(buckets_list)}", "items": [{"bucket": b} for b in buckets_list[:20]]}


def _preview_javascript(obj) -> Dict[str, Any]:
    secrets = getattr(obj, "secrets", "") or ""
    context = getattr(obj, "context", "") or ""
    secrets_list = [s.strip() for s in secrets.split(",") if s.strip()] if isinstance(secrets, str) else list(secrets)
    ctx_list = [c.strip() for c in context.split(",") if c.strip()] if isinstance(context, str) else list(context)
    return {"summary": f"Secrets: {len(secrets_list)}", "items": [{"secret": s, "context": ctx_list[idx] if idx < len(ctx_list) else ""} for idx, s in enumerate(secrets_list[:20])]}


def _preview_github(obj) -> Dict[str, Any]:
    secrets = getattr(obj, "secret", "") or ""
    paths = getattr(obj, "path", "") or ""
    commits = getattr(obj, "commit", "") or ""
    secrets_list = list(secrets) if isinstance(secrets, list) else [s.strip() for s in str(secrets).split(",") if s.strip()]
    paths_list = list(paths) if isinstance(paths, list) else [p.strip() for p in str(paths).split(",") if p.strip()]
    items = []
    for idx, sec in enumerate(secrets_list[:20]):
        items.append({"secret": sec, "path": paths_list[idx] if idx < len(paths_list) else "", "commit": commits})
    return {"summary": f"Secrets: {len(secrets_list)}", "items": items}


def _preview_jenkins(url: str) -> Dict[str, Any]:
    summary = ""
    items = []
    try:
        api_url = url.rstrip("/") + "/api/json"
        resp = requests.get(api_url, timeout=6)
        if resp.ok:
            data = resp.json()
            jobs = data.get("jobs", [])
            for job in jobs[:10]:
                items.append({"name": job.get("name"), "color": job.get("color")})
            summary = f"Jobs: {len(jobs)}"
        else:
            summary = f"HTTP {resp.status_code}"
    except Exception:
        summary = "Failed to fetch Jenkins API"
    return {"summary": summary, "items": items}


def _preview_ftp(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items = []
    try:
        ftp = ftplib.FTP()
        ftp.connect(ip, _safe_int(port, 21), timeout=6)
        ftp.login()  # anonymous
        listing = ftp.nlst()
        for entry in listing[:10]:
            items.append({"path": entry})
        summary = f"Entries: {len(listing)}"
        try:
            ftp.quit()
        except Exception:
            pass
    except Exception:
        summary = "FTP preview failed"
    return {"summary": summary, "items": items}


def _preview_couch(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items = []
    try:
        base = _http_base(ip, port, scheme="http")
        resp = requests.get(f"{base}/_all_dbs", timeout=6)
        if resp.ok:
            dbs = resp.json()
            for db in dbs[:10]:
                items.append({"db": db})
            # sample docs from first db
            if dbs:
                dbname = dbs[0]
                doc_resp = requests.get(f"{base}/{dbname}/_all_docs?limit=10&include_docs=true", timeout=6)
                if doc_resp.ok:
                    rows = doc_resp.json().get("rows", [])
                    items.append({"db": dbname, "sample_docs": [r.get("doc") for r in rows if isinstance(r, dict)]})
            summary = f"Databases: {len(dbs)}"
        else:
            summary = f"HTTP {resp.status_code}"
    except Exception:
        summary = "Failed to fetch CouchDB databases"
    return {"summary": summary, "items": items}


def _preview_s3(bucket: str) -> Dict[str, Any]:
    summary = ""
    items = []
    try:
        url = f"https://{bucket}"
        resp = requests.get(url, timeout=6)
        if resp.ok and "ListBucketResult" in resp.text:
            parser = jxmlease.Parser()
            root = parser(resp.text)
            contents = root.get('ListBucketResult', {}).get('Contents', [])
            if isinstance(contents, dict):
                contents = [contents]
            for item in contents[:10]:
                if isinstance(item, dict):
                    key_val = item.get('Key', '')
                    size_val = item.get('Size', '')
                    head_size = size_val
                    try:
                        head_resp = requests.head(f"{url}/{key_val}", timeout=5)
                        if head_resp.ok:
                            head_size = head_resp.headers.get("Content-Length", head_size)
                            content_type = head_resp.headers.get("Content-Type", "")
                        else:
                            content_type = ""
                    except Exception:
                        content_type = ""
                    items.append({"key": key_val, "size": head_size, "content_type": content_type})
            summary = f"Objects: {len(items)}"
        else:
            summary = f"HTTP {resp.status_code if 'resp' in locals() else ''}"
    except Exception:
        summary = "Failed to fetch bucket listing"
    return {"summary": summary, "items": items}


def _preview_mongo(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items = []
    try:
        client = MongoClient(host=ip, port=_safe_int(port, 27017), serverSelectionTimeoutMS=3000, connectTimeoutMS=3000)
        dbs = client.list_database_names()
        for db in dbs[:10]:
            items.append({"db": db})
        if dbs:
            dbname = dbs[0]
            coll_names = client[dbname].list_collection_names()
            if coll_names:
                coll = client[dbname][coll_names[0]]
                docs = list(coll.find({}, limit=10))
                items.append({"db": dbname, "collection": coll_names[0], "sample_docs": _json_safe(docs)})
        summary = f"Databases: {len(dbs)}"
    except Exception:
        summary = "Mongo preview failed"
    return {"summary": summary, "items": items}


def _preview_rsync(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items = []
    try:
        sock = socket.create_connection((ip, _safe_int(port, 873)), timeout=5)
        sock.sendall(b"@RSYNCD: 31.0\n")
        data = sock.recv(4096).decode(errors="ignore")
        if "RSYNCD" not in data:
            summary = "Not an rsync server"
        else:
            sock.sendall(b"#list\n")
            mod_data = sock.recv(8192).decode(errors="ignore")
            for line in mod_data.splitlines():
                if line.startswith("@RSYNCD") or line.strip() == "" or line.startswith("#"):
                    continue
                parts = line.split("\t", 1)
                items.append({"module": parts[0], "desc": parts[1] if len(parts) > 1 else ""})
            items = items[:10]
            summary = f"Modules: {len(items)}"
        sock.close()
    except Exception:
        summary = "Rsync preview failed"
    return {"summary": summary, "items": items}


def _preview_etcd(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    auth_required = False
    for scheme in ("http", "https"):
        base = _http_base(ip, port, scheme=scheme)
        try:
            ver = requests.get(f"{base}/version", timeout=4, verify=False)
            if ver.status_code in (401, 403):
                auth_required = True
                continue
            if ver.ok and isinstance(ver.json(), dict):
                summary = f"version={ver.json().get('etcdserver') or ver.json().get('etcdcluster')}"
            health = requests.get(f"{base}/health", timeout=4, verify=False)
            if health.status_code in (401, 403):
                auth_required = True
            elif health.ok:
                summary = summary or health.text
            kv = requests.get(f"{base}/v2/keys/?recursive=true&sorted=true&limit=10", timeout=5, verify=False)
            if kv.status_code == 404:
                payload = {"key": "", "range_end": "", "limit": 10}
                v3 = requests.post(f"{base}/v3/kv/range", json=payload, timeout=5, verify=False)
                if v3.ok:
                    try:
                        for kvs in v3.json().get("kvs", []) or []:
                            k = kvs.get("key")
                            if k:
                                items.append({"key": base64.b64decode(k).decode(errors="ignore")})
                    except Exception:
                        pass
            elif kv.status_code in (401, 403):
                auth_required = True
            elif kv.ok:
                try:
                    data = kv.json()
                    def _collect(node):
                        if isinstance(node, dict):
                            key = node.get("key")
                            if key:
                                items.append({"key": key})
                            for child in node.get("nodes", []) or []:
                                _collect(child)
                    _collect(data.get("node", {}))
                except Exception:
                    pass
            break
        except Exception:
            continue
    if auth_required and not items:
        summary = "auth required"
    return {"summary": summary or ("keys: %s" % len(items)), "items": items[:10]}


def _preview_consul(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    auth_required = False
    for scheme in ("http", "https"):
        base = _http_base(ip, port, scheme=scheme)
        try:
            dcs = requests.get(f"{base}/v1/catalog/datacenters", timeout=4, verify=False)
            if dcs.status_code in (401, 403):
                auth_required = True
                continue
            if dcs.ok and isinstance(dcs.json(), list):
                for dc in dcs.json()[:10]:
                    items.append({"datacenter": dc})
            svcs = requests.get(f"{base}/v1/catalog/services", timeout=4, verify=False)
            if svcs.status_code in (401, 403):
                auth_required = True
            elif svcs.ok and isinstance(svcs.json(), dict):
                for svc in list(svcs.json().keys())[:10]:
                    items.append({"service": svc})
            kv = requests.get(f"{base}/v1/kv/?keys&limit=10", timeout=4, verify=False)
            if kv.status_code in (401, 403):
                auth_required = True
            elif kv.ok and isinstance(kv.json(), list):
                for k in kv.json()[:10]:
                    items.append({"key": k})
            break
        except Exception:
            continue
    if auth_required and not items:
        summary = "auth required"
    return {"summary": summary or ("items: %s" % len(items)), "items": items[:15]}


def _preview_rabbitmq(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    auth_required = False
    for scheme in ("http", "https"):
        base = _http_base(ip, port, scheme=scheme)
        try:
            ov = requests.get(f"{base}/api/overview", timeout=4, verify=False)
            if ov.status_code in (401, 403):
                auth_required = True
                continue
            if ov.ok and isinstance(ov.json(), dict):
                summary = f"version={ov.json().get('rabbitmq_version','')}"
            vhosts = requests.get(f"{base}/api/vhosts", timeout=4, verify=False)
            if vhosts.status_code in (401, 403):
                auth_required = True
            elif vhosts.ok and isinstance(vhosts.json(), list):
                for vh in vhosts.json()[:10]:
                    if isinstance(vh, dict) and vh.get("name"):
                        items.append({"vhost": vh.get("name")})
                    elif isinstance(vh, str):
                        items.append({"vhost": vh})
            queues = requests.get(f"{base}/api/queues", timeout=5, verify=False)
            if queues.status_code in (401, 403):
                auth_required = True
            elif queues.ok and isinstance(queues.json(), list):
                for q in queues.json()[:10]:
                    if isinstance(q, dict) and q.get("name"):
                        items.append({"queue": q.get("name"), "vhost": q.get("vhost")})
            break
        except Exception:
            continue
    if auth_required and not items:
        summary = "auth required"
    return {"summary": summary or ("items: %s" % len(items)), "items": items[:15]}


def _preview_solr(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    auth_required = False
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
                    items = [{"core": name} for name in list(status.keys())[:15]]
                version = cores_resp.headers.get("X-Solr-Version", "") or ""
                summary = summary or (f"version={version}" if version else "")
                break
        except Exception:
            continue
    if auth_required and not items:
        summary = "auth required"
    return {"summary": summary or ("cores: %s" % len(items)), "items": items[:15]}


def _preview_gitea(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    auth_required = False
    for scheme in ("http", "https"):
        base = _http_base(ip, port, scheme=scheme)
        try:
            resp = requests.get(f"{base}/api/v1/repos/search?limit=15", timeout=6, verify=False)
            if resp.status_code in (401, 403):
                auth_required = True
                continue
            if resp.ok and isinstance(resp.json(), dict):
                data = resp.json().get("data", []) or resp.json().get("repos", [])
                for r in data[:15]:
                    if isinstance(r, dict) and r.get("full_name"):
                        items.append({"repo": r.get("full_name")})
            break
        except Exception:
            continue
    if auth_required and not items:
        summary = "auth required"
    return {"summary": summary or ("repos: %s" % len(items)), "items": items[:15]}


def _preview_azureblob(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    auth_required = False
    base = _http_base(ip, port, scheme="https")
    try:
        resp = requests.get(f"{base}/?comp=list&maxresults=15", timeout=6, verify=False)
        if resp.status_code in (401, 403):
            auth_required = True
        elif resp.ok and "EnumerationResults" in resp.text:
            try:
                parsed = jxmlease.Parser()(resp.text)
                conts = parsed.get("EnumerationResults", {}).get("Containers", {}).get("Container", [])
                if isinstance(conts, dict):
                    conts = [conts]
                for c in conts[:15]:
                    if isinstance(c, dict) and c.get("Name"):
                        items.append({"container": c.get("Name")})
            except Exception:
                pass
    except Exception:
        pass
    if auth_required and not items:
        summary = "auth required"
    return {"summary": summary or ("containers: %s" % len(items)), "items": items[:15]}


def _preview_gcsbucket(ip: str, port: Any) -> Dict[str, Any]:
    summary = ""
    items: List[Dict[str, Any]] = []
    auth_required = False
    base = _http_base(ip, port, scheme="https")
    try:
        resp = requests.get(f"{base}/", timeout=6, verify=False)
        if resp.status_code in (401, 403):
            auth_required = True
        elif resp.ok and "ListBucketResult" in resp.text:
            try:
                parsed = jxmlease.Parser()(resp.text)
                contents = parsed.get("ListBucketResult", {}).get("Contents", [])
                if isinstance(contents, dict):
                    contents = [contents]
                for c in contents[:15]:
                    if isinstance(c, dict) and c.get("Key"):
                        items.append({"object": c.get("Key")})
            except Exception:
                pass
    except Exception:
        pass
    if auth_required and not items:
        summary = "auth required"
    return {"summary": summary or ("objects: %s" % len(items)), "items": items[:15]}
