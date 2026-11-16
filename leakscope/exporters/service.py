"""Live service export task; public registration remains leakscope.tasks.export_preview_data."""
from leakscope.shared import (
    shared_task,
    json,
    requests,
    Ignore,
    Any,
    Dict,
    Optional,
    List,
    MongoClient,
    Cluster,
    NoHostAvailable,
    ExecutionProfile,
    EXEC_PROFILE_DEFAULT,
    DCAwareRoundRobinPolicy,
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
    AmazonBuckets,
    Keys,
    Github,
    Amazons3be,
    Angular,
    Javascript,
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
    redact,
    publish_state,
    _safe_int,
    _write_export_file,
    _limit_max,
    _http_base,
    _parse_link_next,
)


class ExportError(dict):
    """Internal failure record, distinct from a document's own ``error`` field."""


@shared_task(bind=True, name="leakscope.tasks.export_preview_data")
def export_preview_data(self, type: str, target_id: Any, selection: Optional[Dict[str, Any]] = None,
                        max_items: Any = 1000, fmt: str = "json", active_probe: bool = False):
    from leakscope.services.registry import canonical_type
    type_lower = canonical_type(type)
    if not active_probe:
        publish_state(self, state="SUCCESS", meta={"state": "SKIPPED", "requires_active": True})
        raise Ignore()

    selection = selection or {}
    max_items = _limit_max(max_items)
    sel_index = (selection.get("index") or selection.get("db") or "").strip()
    sel_collection = (selection.get("collection") or "").strip()
    sel_repo = (selection.get("repo") or "").strip()
    sel_bucket = (selection.get("bucket") or "").strip()
    sel_filter = (selection.get("filter") or "").strip()
    sel_keyspace = (selection.get("keyspace") or "").strip()
    sel_table = (selection.get("table") or "").strip()
    sel_max_repos = selection.get("max_repos")
    sel_max_tags = selection.get("max_tags")

    def _clean_name(val: Any) -> str:
        if not isinstance(val, str):
            return str(val or "").strip()
        return val.strip().strip("'\"/ ")

    def _json_safe_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
        try:
            # Ensure meta can be serialized; fall back to string conversion.
            json.dumps(meta, default=str)
            return meta
        except Exception:
            safe_meta = {}
            for k, v in meta.items():
                try:
                    safe_meta[k] = json.loads(json.dumps(v, default=str))
                except Exception:
                    safe_meta[k] = str(v)
            return safe_meta

    from leakscope.services.registry import model_for_type
    Model = model_for_type(type_lower)
    if not Model:
        publish_state(self, state="FAILURE", meta={"error": f"Export not implemented for {type_lower}"})
        raise Ignore()

    try:
        obj = Model.objects.get(id=target_id)
    except Model.DoesNotExist:
        publish_state(self, state="FAILURE", meta={"error": "Target not found"})
        raise Ignore()

    try:
        if type_lower in ("elastic", "opensearch"):
            base = _http_base(obj.ip, obj.port, scheme="http")
            target_index = _clean_name(sel_index)
            indices = []
            index_error = None
            try:
                resp = requests.get(f"{base}/_cat/indices?format=json&bytes=b", timeout=8)
                if resp.ok:
                    indices = resp.json()
                else:
                    index_error = f"HTTP {resp.status_code} while listing indices"
            except Exception as exc:
                index_error = str(exc)
            if not target_index and indices:
                target_index = indices[0].get("index") or indices[0].get("id") or indices[0].get("index_name")
            docs = []
            if target_index:
                try:
                    sr = requests.get(f"{base}/{target_index}/_search?size={max_items}", timeout=10)
                    if sr.ok:
                        hits = sr.json().get("hits", {}).get("hits", [])
                        for h in hits:
                            docs.append(h.get("_source", h))
                    else:
                        docs = [ExportError({"error": f"HTTP {sr.status_code}"})]
                except Exception as exc:
                    docs = [ExportError({"error": str(exc)})]
            else:
                docs = [ExportError({"error": index_error or "No index available; select an index to export"})]
            export_payload = docs

        elif type_lower == "mongo":
            try:
                client = MongoClient(f"mongodb://{obj.ip}:{obj.port}/", serverSelectionTimeoutMS=4000)
                db_name = selection.get("db") or sel_index
                coll_name = sel_collection
                dbs = client.list_database_names()
                db_name = db_name or (dbs[0] if dbs else None)
                if not db_name:
                    export_payload = [ExportError({"error": "no database found"})]
                else:
                    db = client[db_name]
                    colls = db.list_collection_names()
                    coll_name = coll_name or (colls[0] if colls else None)
                    if coll_name:
                        cursor = db[coll_name].find().limit(max_items)
                        export_payload = [doc for doc in cursor]
                    else:
                        export_payload = [ExportError({"error": "no collection found"})]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]

        elif type_lower == "couchdb":
            base = _http_base(obj.ip, obj.port, scheme="http")
            db_name = selection.get("db") or sel_index
            export_payload = []
            try:
                dbs = requests.get(f"{base}/_all_dbs", timeout=6).json()
                db_name = db_name or (dbs[0] if dbs else None)
                if db_name:
                    r = requests.get(f"{base}/{db_name}/_all_docs?include_docs=true&limit={max_items}", timeout=10)
                    if r.ok:
                        rows = r.json().get("rows", [])
                        export_payload = [row.get("doc") for row in rows if row.get("doc")]
                    else:
                        export_payload = [ExportError({"error": f"HTTP {r.status_code}"})]
                else:
                    export_payload = [ExportError({"error": "no db found"})]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]

        elif type_lower in ("amazons3be", "amazonbuckets"):
            bucket = sel_bucket or getattr(obj, "bucket", None) or getattr(obj, "buckets", None)
            if isinstance(bucket, list):
                bucket = bucket[0] if bucket else None
            export_payload = []
            if bucket:
                url = bucket if bucket.startswith("http") else f"https://{bucket}"
                try:
                    resp = requests.get(url, timeout=10)
                    if resp.ok:
                        export_payload = [{"bucket": bucket, "content": resp.text[:500000]}]
                    else:
                        export_payload = [ExportError({"bucket": bucket, "error": f"HTTP {resp.status_code}"})]
                except Exception as exc:
                    export_payload = [ExportError({"bucket": bucket, "error": str(exc)})]
            else:
                export_payload = [ExportError({"error": "No bucket available; select a bucket to export"})]
        elif type_lower == "nexus":
            base = getattr(obj, "url", "") or _http_base(obj.ip, obj.port, scheme="http")
            export_payload = []
            repos = []
            try:
                r = requests.get(f"{base}/service/rest/v1/repositories", timeout=8)
                if r.status_code in (401, 403):
                    raise Exception("auth required")
                if r.ok:
                    repos = r.json()
                else:
                    export_payload = [ExportError({"error": f"HTTP {r.status_code} while listing repositories"})]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]
            selected_repo = sel_repo or selection.get("repo") or selection.get("index")
            if repos and not selected_repo:
                selected_repo = repos[0].get("name")
            if selected_repo:
                try:
                    sr = requests.get(f"{base}/service/rest/v1/search?repository={selected_repo}&sort=version&direction=desc&max={max_items}", timeout=10)
                    if sr.status_code in (401,403):
                        export_payload = [ExportError({"error":"auth required"})]
                    elif sr.ok:
                        export_payload = sr.json().get("items", [])[:max_items]
                    else:
                        export_payload = [ExportError({"error": f"HTTP {sr.status_code} while searching repository"})]
                except Exception as exc:
                    export_payload = [ExportError({"error": str(exc)})]
            if not export_payload and repos:
                export_payload = repos[:max_items]

        elif type_lower == "artifactory":
            base = getattr(obj, "url", "") or _http_base(obj.ip, obj.port, scheme="http")
            base = base[:-1] if base.endswith("/") else base
            export_payload = []
            repos = []
            try:
                r = requests.get(f"{base}/api/repositories", timeout=8)
                if r.status_code in (401,403):
                    raise Exception("auth required")
                if r.ok:
                    repos = r.json()
                else:
                    export_payload = [ExportError({"error": f"HTTP {r.status_code} while listing repositories"})]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]
            selected_repo = sel_repo or selection.get("repo") or selection.get("index")
            if repos and not selected_repo:
                selected_repo = repos[0].get("key") or repos[0].get("repoKey") or repos[0].get("repo")
            if selected_repo:
                try:
                    sr = requests.get(f"{base}/api/storage/{selected_repo}", timeout=10)
                    if sr.status_code in (401,403):
                        export_payload = [ExportError({"error":"auth required"})]
                    elif sr.ok:
                        data = sr.json()
                        children = data.get("children", [])[:max_items]
                        export_payload = [{"repo": selected_repo, "children": children}]
                    else:
                        export_payload = [ExportError({"error": f"HTTP {sr.status_code} while listing repository contents"})]
                except Exception as exc:
                    export_payload = [ExportError({"error": str(exc)})]
            if not export_payload and repos:
                export_payload = repos[:max_items]

        elif type_lower == "registry":
            base = getattr(obj, "url", "") or _http_base(obj.ip, obj.port, scheme="http")
            base = base[:-1] if base.endswith("/") else base
            repo_cap = _limit_max(sel_max_repos or selection.get("max_repos", max_items), default=200, hard_max=5000)
            tag_cap = _limit_max(sel_max_tags or selection.get("max_tags", 50), default=50, hard_max=1000)
            export_payload = []
            try:
                probe = requests.get(f"{base}/v2/", timeout=6)
                if probe.status_code in (401, 403):
                    export_payload = [ExportError({"error": "auth required"})]
                elif not probe.ok:
                    export_payload = [ExportError({"error": f"HTTP {probe.status_code}"})]
                else:
                    repos: List[str] = []
                    next_url = f"{base}/v2/_catalog?n={repo_cap}"
                    while next_url and len(repos) < repo_cap:
                        resp = requests.get(next_url, timeout=8)
                        if resp.status_code in (401, 403):
                            export_payload = [ExportError({"error": "auth required"})]
                            break
                        if not resp.ok:
                            export_payload = [ExportError({"error": f"HTTP {resp.status_code}"})]
                            break
                        payload = resp.json()
                        repos.extend(payload.get("repositories", []))
                        link = resp.headers.get("Link")
                        nxt = _parse_link_next(link) if link else None
                        if nxt:
                            next_url = nxt if nxt.startswith("http") else f"{base}{nxt}"
                        else:
                            next_url = None

                    repos = repos[:repo_cap]
                    selected_repo = sel_repo or selection.get("repo") or selection.get("index")
                    if selected_repo:
                        if selected_repo not in repos and repos:
                            repos = [selected_repo] + repos
                        else:
                            repos = [selected_repo]

                    for repo in repos:
                        try:
                            t_resp = requests.get(f"{base}/v2/{repo}/tags/list?n={tag_cap}", timeout=8)
                            if t_resp.status_code in (401, 403):
                                export_payload.append(ExportError({"repo": repo, "error": "auth required"}))
                            elif t_resp.ok:
                                data = t_resp.json()
                                tags = data.get("tags") or []
                                export_payload.append({"repo": repo, "tags": (tags or [])[:tag_cap]})
                            else:
                                export_payload.append(ExportError({"repo": repo, "error": f"HTTP {t_resp.status_code}"}))
                        except Exception:
                            export_payload.append(ExportError({"repo": repo, "error": "tag listing failed"}))

                    if not repos and not export_payload:
                        export_payload = [{"message": "No repositories found"}]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]
        elif type_lower == "gitlab":
            export_payload = [{"ip": obj.ip, "port": obj.port, "url": getattr(obj, "url", "")}]
        elif type_lower == "sonarqube":
            export_payload = [{"ip": obj.ip, "port": obj.port, "url": getattr(obj, "url", "")}]
        elif type_lower == "kibana":
            export_payload = [{"ip": obj.ip, "port": obj.port}]
        elif type_lower == "grafana":
            export_payload = [{"ip": obj.ip, "port": obj.port, "url": getattr(obj, "url", ""), "title": getattr(obj, "title", "")}]
        elif type_lower == "prometheus":
            export_payload = [{"ip": obj.ip, "port": obj.port, "url": getattr(obj, "url", ""), "title": getattr(obj, "title", "")}]
        elif type_lower == "mongoexpress":
            export_payload = [{"ip":obj.ip,"port":obj.port,"url":getattr(obj,"url",""),"title":getattr(obj,"title","")}]
        elif type_lower == "minio":
            buckets = getattr(obj, "buckets", "") or ""
            export_payload = [{"ip": obj.ip, "port": obj.port, "url": getattr(obj, "url", ""), "title": getattr(obj, "title", ""), "buckets": buckets}]
        elif type_lower == "swagger":
            export_payload = [{"ip": obj.ip, "port": obj.port, "url": getattr(obj, "url", ""), "title": getattr(obj, "title", "")}]
        elif type_lower == "angular":
            export_payload = [{"ip": obj.ip, "port": obj.port, "title": getattr(obj, "title", ""), "path": getattr(obj, "path", "")}]
        elif type_lower == "keys":
            export_payload = [{"ip": obj.ip, "port": obj.port, "title": getattr(obj, "title", "")}]
        elif type_lower == "amazonbe":
            buckets = getattr(obj, "buckets", "") or ""
            export_payload = [{"ip": obj.ip, "port": obj.port, "buckets": buckets}]
        elif type_lower == "dirs":
            dirs = getattr(obj, "dirs", []) or []
            if isinstance(dirs, str):
                dirs = [d.strip() for d in dirs.split(",") if d.strip()]
            flt = sel_filter or (selection.get("index") or "").strip()
            if flt:
                dirs = [d for d in dirs if flt.lower() in d.lower()]
            export_payload = [{"ip": obj.ip, "port": obj.port, "url": getattr(obj, "url", ""), "dirs": dirs[:max_items]}]
        elif type_lower == "jenkins":
            jobs = getattr(obj, "jobs", []) or []
            if isinstance(jobs, str):
                jobs = [j.strip() for j in jobs.split(",") if j.strip()]
            flt = sel_filter or (selection.get("index") or "").strip()
            if flt:
                jobs = [j for j in jobs if flt.lower() in j.lower()]
            export_payload = [{"ip": obj.ip, "port": obj.port, "url": getattr(obj, "url", ""), "jobs": jobs[:max_items]}]
        elif type_lower == "ftp":
            files = getattr(obj, "files", []) or []
            if isinstance(files, str):
                files = [f.strip() for f in files.split(",") if f.strip()]
            flt = sel_filter or (selection.get("index") or "").strip()
            if flt:
                files = [f for f in files if flt.lower() in f.lower()]
            export_payload = [{"ip": obj.ip, "port": obj.port, "files": files[:max_items]}]
        elif type_lower == "rsync":
            shares = getattr(obj, "shares", []) or []
            if isinstance(shares, str):
                shares = [s.strip() for s in shares.split(",") if s.strip()]
            flt = sel_filter or (selection.get("index") or "").strip()
            if flt:
                shares = [s for s in shares if flt.lower() in s.lower()]
            export_payload = [{"ip": obj.ip, "port": obj.port, "shares": shares[:max_items]}]
        elif type_lower == "cassandra":
            keyspaces = getattr(obj, "keyspaces", []) or []
            if isinstance(keyspaces, str):
                keyspaces = [k.strip() for k in keyspaces.split(",") if k.strip()]
            flt = sel_filter or sel_keyspace or (selection.get("index") or "").strip()
            if flt:
                keyspaces = [k for k in keyspaces if flt.lower() in k.lower()]

            # Optional live, no-auth schema peek to enrich keyspace/table listing using cassandra-driver
            live_keyspaces: List[str] = []
            table_map: Dict[str, List[str]] = {}
            auth_required = False
            candidate_ports = []
            cport = _safe_int(obj.port, 9042)
            for p in [9042, cport]:
                if p not in candidate_ports:
                    candidate_ports.append(p)
            for p in candidate_ports:
                try:
                    profile = ExecutionProfile(
                        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc="dc1", used_hosts_per_remote_dc=0),
                        request_timeout=3,
                    )
                    cluster = Cluster(
                        contact_points=[obj.ip],
                        port=p,
                        connect_timeout=3,
                        protocol_version=4,
                        execution_profiles={EXEC_PROFILE_DEFAULT: profile},
                        allow_beta_protocol_version=False,
                    )
                    session = cluster.connect()
                    try:
                        ks_rows = session.execute("SELECT keyspace_name FROM system_schema.keyspaces")
                        live_keyspaces = [r.keyspace_name for r in ks_rows][:25]
                    except Exception as exc:
                        if "auth" in str(exc).lower() or "permission" in str(exc).lower():
                            auth_required = True
                    try:
                        tbl_rows = session.execute("SELECT keyspace_name, table_name FROM system_schema.tables")
                        for r in tbl_rows:
                            table_map.setdefault(r.keyspace_name, [])
                            if len(table_map[r.keyspace_name]) < 25:
                                table_map[r.keyspace_name].append(r.table_name)
                    except Exception as exc:
                        if "auth" in str(exc).lower() or "permission" in str(exc).lower():
                            auth_required = True
                    cluster.shutdown()
                    break
                except NoHostAvailable:
                    continue
                except Exception as exc:
                    if "auth" in str(exc).lower() or "permission" in str(exc).lower():
                        auth_required = True

            combined_keyspaces = list({*(k for k in keyspaces), *(k for k in live_keyspaces)})
            if flt:
                combined_keyspaces = [k for k in combined_keyspaces if flt.lower() in k.lower()]

            export_payload = [{
                "ip": obj.ip,
                "port": obj.port,
                "keyspaces": combined_keyspaces[:max_items],
                "tables": table_map,
                "table_requested": sel_table,
                "auth_required": auth_required,
                "note": "No-auth schema peek only; provide keyspace/table to export. Auth-required clusters are not accessed, and rows are not fetched without open access."
            }]
        elif type_lower == "rethink":
            dbs = getattr(obj, "databases", []) or []
            if isinstance(dbs, str):
                dbs = [d.strip() for d in dbs.split(",") if d.strip()]
            flt = sel_filter or sel_keyspace or (selection.get("index") or "").strip()
            if flt:
                dbs = [d for d in dbs if flt.lower() in d.lower()]
            export_payload = [{"ip": obj.ip, "port": obj.port, "databases": dbs[:max_items]}]
        elif type_lower == "javascript":
            secrets = getattr(obj, "secrets", "") or ""
            context = getattr(obj, "context", "") or ""
            secrets_list = [s.strip() for s in secrets.split(",") if s.strip()] if isinstance(secrets, str) else list(secrets)
            ctx_list = [c.strip() for c in context.split(",") if c.strip()] if isinstance(context, str) else list(context)
            flt = sel_filter or (selection.get("index") or "").strip()
            items = []
            for idx, sec in enumerate(secrets_list):
                if flt and flt.lower() not in sec.lower():
                    continue
                items.append({"secret": sec, "context": ctx_list[idx] if idx < len(ctx_list) else ""})
            export_payload = items[:max_items]
        elif type_lower == "github":
            secrets = getattr(obj, "secret", "") or ""
            paths = getattr(obj, "path", "") or ""
            commit = getattr(obj, "commit", "")
            secrets_list = list(secrets) if isinstance(secrets, list) else [s.strip() for s in str(secrets).split(",") if s.strip()]
            paths_list = list(paths) if isinstance(paths, list) else [p.strip() for p in str(paths).split(",") if p.strip()]
            flt = sel_filter or (selection.get("index") or "").strip()
            items = []
            for idx, sec in enumerate(secrets_list):
                if flt and flt.lower() not in sec.lower():
                    continue
                items.append({"secret": sec, "path": paths_list[idx] if idx < len(paths_list) else "", "commit": commit})
            export_payload = items[:max_items]
        elif type_lower == "solr":
            base = _http_base(obj.ip, obj.port, scheme=getattr(obj, "scheme", "http") or "http")
            core_name = _clean_name(sel_index) or ""
            export_payload = []
            try:
                cores = []
                c_resp = requests.get(f"{base}/solr/admin/cores?action=STATUS&wt=json", timeout=8, verify=False)
                if c_resp.status_code in (401, 403):
                    export_payload = [ExportError({"error": "auth required"})]
                elif c_resp.ok and isinstance(c_resp.json(), dict):
                    status = c_resp.json().get("status", {})
                    cores = list(status.keys())
                elif not c_resp.ok:
                    export_payload = [ExportError({"error": f"HTTP {c_resp.status_code} while listing cores"})]
                if not core_name and cores:
                    core_name = cores[0]
                if core_name:
                    rows_cap = _limit_max(max_items, default=50, hard_max=200)
                    s_resp = requests.get(f"{base}/solr/{core_name}/select?q=*:*&rows={rows_cap}&wt=json", timeout=8, verify=False)
                    if s_resp.status_code in (401, 403):
                        export_payload = [ExportError({"error": "auth required"})]
                    elif s_resp.ok:
                        docs = s_resp.json().get("response", {}).get("docs", [])
                        export_payload = docs[:rows_cap]
                    else:
                        export_payload = [ExportError({"error": f"HTTP {s_resp.status_code}"})]
                elif not export_payload:
                    export_payload = [{"message": "No cores found"}]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]
        elif type_lower in ("gitea", "gogs"):
            repos = getattr(obj, "repos", "") or ""
            if isinstance(repos, str):
                try:
                    repos_list = json.loads(repos)
                except Exception:
                    repos_list = [r.strip() for r in repos.split(",") if r.strip()]
            else:
                repos_list = list(repos)
            flt = sel_filter or (selection.get("index") or "").strip()
            if flt:
                repos_list = [r for r in repos_list if flt.lower() in str(r).lower()]
            export_payload = [{"repo": r} for r in repos_list[:max_items]]
            if not export_payload:
                export_payload = [{"message": "No repos found"}]
        elif type_lower == "azureblob":
            base_host = getattr(obj, "account", "") or getattr(obj, "search", None)
            host = base_host if base_host else obj.ip
            container = _clean_name(sel_index) or ""
            try:
                max_cap = _limit_max(max_items, default=50, hard_max=200)
                base = f"{_http_base(host, obj.port, scheme=getattr(obj, 'scheme', 'https') or 'https')}".rstrip("/")
                url = f"{base}/?comp=list&maxresults={max_cap}" if not container else f"{base}/{container}?restype=container&comp=list&maxresults={max_cap}"
                resp = requests.get(url, timeout=8, verify=False)
                if resp.status_code in (401, 403):
                    export_payload = [ExportError({"error": "auth required"})]
                elif resp.ok and "EnumerationResults" in resp.text:
                    try:
                        parsed = jxmlease.Parser()(resp.text)
                        if not container:
                            conts = parsed.get("EnumerationResults", {}).get("Containers", {}).get("Container", [])
                            if isinstance(conts, dict):
                                conts = [conts]
                            export_payload = [{"container": c.get("Name")} for c in conts if isinstance(c, dict) and c.get("Name")]
                        else:
                            blobs = parsed.get("EnumerationResults", {}).get("Blobs", {}).get("Blob", [])
                            if isinstance(blobs, dict):
                                blobs = [blobs]
                            export_payload = [{"blob": b.get("Name")} for b in blobs if isinstance(b, dict) and b.get("Name")][:max_cap]
                            if not export_payload:
                                export_payload = [{"message": "No blobs found"}]
                    except Exception as exc:
                        export_payload = [ExportError({"error": str(exc)})]
                else:
                    export_payload = [ExportError({"error": f"HTTP {resp.status_code}"})]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]
        elif type_lower == "gcsbucket":
            host = getattr(obj, "bucket", "") or getattr(obj, "account", "") or obj.ip
            prefix = _clean_name(sel_index) or ""
            try:
                max_cap = _limit_max(max_items, default=50, hard_max=200)
                base = f"{_http_base(host, obj.port, scheme=getattr(obj, 'scheme', 'https') or 'https')}".rstrip("/")
                url = f"{base}/"
                if prefix:
                    url = f"{base}/?prefix={prefix}&max-keys={max_cap}"
                resp = requests.get(url, timeout=8, verify=False)
                if resp.status_code in (401, 403):
                    export_payload = [ExportError({"error": "auth required"})]
                elif resp.ok and "ListBucketResult" in resp.text:
                    try:
                        parsed = jxmlease.Parser()(resp.text)
                        contents = parsed.get("ListBucketResult", {}).get("Contents", [])
                        if isinstance(contents, dict):
                            contents = [contents]
                        rows = []
                        for idx, item in enumerate(contents):
                            if idx >= max_cap:
                                break
                            if isinstance(item, dict) and item.get("Key"):
                                rows.append({"object": item.get("Key")})
                        export_payload = rows if rows else [{"message": "No objects found"}]
                    except Exception as exc:
                        export_payload = [ExportError({"error": str(exc)})]
                else:
                    export_payload = [ExportError({"error": f"HTTP {resp.status_code}"})]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]
        elif type_lower == "etcd":
            base = _http_base(obj.ip, obj.port, scheme=getattr(obj, "scheme", "http") or "http")
            prefix = _clean_name(sel_index) or ""
            try:
                resp = requests.get(f"{base}/v2/keys/{prefix}?recursive=true&sorted=true&limit={max_items}", timeout=8, verify=False)
                if resp.status_code in (401, 403):
                    export_payload = [ExportError({"error": "auth required"})]
                elif resp.status_code == 404:
                    payload = {"key": "", "range_end": "", "limit": max_items}
                    v3 = requests.post(f"{base}/v3/kv/range", json=payload, timeout=8, verify=False)
                    if v3.ok:
                        data = []
                        for kvs in v3.json().get("kvs", []) or []:
                            k = kvs.get("key")
                            v = kvs.get("value")
                            if k:
                                key_dec = base64.b64decode(k).decode(errors="ignore")
                                val_dec = base64.b64decode(v).decode(errors="ignore") if v else ""
                                data.append({"key": key_dec, "value": val_dec})
                        export_payload = data
                    else:
                        export_payload = [ExportError({"error": f"HTTP {v3.status_code}"})]
                elif resp.ok:
                    data = []
                    try:
                        def _collect(node):
                            if isinstance(node, dict):
                                key = node.get("key")
                                val = node.get("value")
                                if key:
                                    data.append({"key": key, "value": val})
                                for child in node.get("nodes", []) or []:
                                    _collect(child)
                        _collect(resp.json().get("node", {}))
                    except Exception:
                        pass
                    export_payload = data[:max_items]
                else:
                    export_payload = [ExportError({"error": f"HTTP {resp.status_code}"})]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]
        elif type_lower == "consul":
            base = _http_base(obj.ip, obj.port, scheme=getattr(obj, "scheme", "http") or "http")
            prefix = _clean_name(sel_index) or ""
            try:
                keys_url = f"{base}/v1/kv/{prefix}?keys&recurse&limit={max_items}" if prefix else f"{base}/v1/kv/?keys&limit={max_items}"
                resp = requests.get(keys_url, timeout=8, verify=False)
                if resp.status_code in (401, 403):
                    export_payload = [ExportError({"error": "auth required"})]
                elif resp.ok:
                    data = []
                    if isinstance(resp.json(), list):
                        for k in resp.json()[:max_items]:
                            data.append({"key": k})
                    export_payload = data
                else:
                    export_payload = [ExportError({"error": f"HTTP {resp.status_code}"})]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]
        elif type_lower == "rabbitmq":
            base = _http_base(obj.ip, obj.port, scheme=getattr(obj, "scheme", "http") or "http")
            try:
                qresp = requests.get(f"{base}/api/queues", timeout=8, verify=False)
                if qresp.status_code in (401, 403):
                    export_payload = [ExportError({"error": "auth required"})]
                elif qresp.ok:
                    data = []
                    if isinstance(qresp.json(), list):
                        cap = _limit_max(max_items, default=50, hard_max=500)
                        for q in qresp.json()[:cap]:
                            if isinstance(q, dict):
                                data.append({"name": q.get("name"), "vhost": q.get("vhost"), "messages": q.get("messages")})
                    export_payload = data
                else:
                    export_payload = [ExportError({"error": f"HTTP {qresp.status_code}"})]
            except Exception as exc:
                export_payload = [ExportError({"error": str(exc)})]
        else:
            raise ValueError(f"Export is not supported for {type_lower}")

        errors = [redact(row["error"]) for row in export_payload if isinstance(row, ExportError)]
        data_count = sum(not isinstance(row, ExportError) for row in export_payload)
        if errors and not data_count:
            publish_state(self, state="FAILURE", meta=_json_safe_meta({
                "type": type_lower, "error": "; ".join(errors), "errors": errors,
                "data_count": 0, "error_count": len(errors),
            }))
            raise Ignore()
        file_info = _write_export_file(export_payload, fmt or "json")
        meta = {"type": type_lower, "download_token": file_info["token"], "filename": file_info["path"],
                "data_count": data_count, "error_count": len(errors), "partial": bool(errors)}
        if errors:
            meta["errors"] = errors
        publish_state(self, state="SUCCESS", meta=_json_safe_meta(meta))
        raise Ignore()
    except Ignore:
        raise
    except Exception as exc:
        publish_state(self, state="FAILURE", meta=_json_safe_meta({"error": redact(exc)}))
        raise Ignore()
