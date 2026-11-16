from leakscope.services.registry import canonical_type, model_for_type, discovery_handler, dispatch_discovery, preview_for_type
"""Stable Celery task facade and run orchestration.

Implementations live in providers, handlers, previews and exporters; existing
public imports and serialized task names remain compatible.
"""
from leakscope.shared import (
    shared_task,
    logging,
    json,
    os,
    Path,
    uuid4,
    requests,
    celery,
    Repo,
    tempfile,
    Ignore,
    celery_progress,
    size,
    BeautifulSoup,
    current_task,
    ProgressRecorder,
    smtplib,
    ast,
    email,
    math,
    re,
    jsbeautifier,
    GzipFile,
    ssl,
    Any,
    Dict,
    Optional,
    Tuple,
    List,
    ftplib,
    socket,
    MongoClient,
    Cluster,
    NoHostAvailable,
    ExecutionProfile,
    EXEC_PROFILE_DEFAULT,
    DCAwareRoundRobinPolicy,
    SimpleStatement,
    PlainTextAuthProvider,
    json_util,
    Request,
    urlopen,
    hashlib,
    base64,
    timezone,
    urlparse,
    jxmlease,
    Monitor,
    Search,
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
    FingerprintLog,
    CustomQuery,
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
    ShodanClient,
    ShodanClientError,
    ZoomEyeClient,
    ZoomEyeClientError,
    normalize_match,
    app,
    settings,
    get_config,
    redact,
    canonical_fingerprint,
    publish_state,
    logger,
    _env_list,
    is_blacklisted,
    _fingerprint,
    record_fingerprint,
    regex_str,
    context_delimiter_str,
    buckets_all,
    keys_all,
    BASE64_CHARS,
    HEX_CHARS,
    exclude,
    _load_wordlist,
    buckets_bruteforce,
    azure_buckets_bruteforce,
    gcs_buckets_bruteforce,
    build_shodan_query,
    build_provider_query,
    render_query_template,
    infer_scheme,
    build_service_url,
    get_http_html,
    parse_json_payload,
    extract_ip_port,
    _safe_int,
    _json_safe,
    _write_export_file,
    _limit_max,
    _http_base,
    _parse_link_next,
    _infer_host,
    parse_bucket,
    shannon_entropy,
    clone_git_repo,
    get_strings_of_set,
    get_secrets,
    rules
)

from leakscope.providers.discovery import (
    queries_shodan,
    queries_zoomeye,
    get_shodan_client,
    get_zoomeye_client,
    provider_queries,
    check_credits,
    stats
)

from leakscope.handlers.discovery import (
    check_amazons3be,
    check_rethink,
    check_cassandra,
    check_etcd,
    check_consul,
    check_rabbitmq,
    check_azureblob,
    check_gcsbucket,
    check_solr,
    check_gitea,
    check_ftp,
    check_kibana,
    check_couchdb,
    check_sonarqube,
    check_rsync,
    check_jenkins,
    check_mongo,
    parser_file,
    check_dir,
    send_request,
    check_angular,
    check_elastic,
    check_opensearch,
    check_grafana,
    check_prometheus,
    check_minio,
    check_swagger,
    check_mongoexpress,
    check_nexus,
    check_artifactory,
    check_registry,
    getContext,
    check_gitlab,
    check_keys,
    check_amazonbe
)

from leakscope.previews.services import (
    _preview_elastic,
    _preview_dirs,
    _preview_nexus,
    _preview_artifactory,
    _preview_registry,
    _preview_gitlab,
    _preview_sonarqube,
    _preview_kibana,
    _preview_grafana,
    _preview_prometheus,
    _preview_swagger,
    _preview_minio,
    _preview_angular,
    _preview_cassandra,
    _preview_rethink,
    _preview_keys,
    _preview_amazonbe,
    _preview_javascript,
    _preview_github,
    _preview_jenkins,
    _preview_ftp,
    _preview_couch,
    _preview_s3,
    _preview_mongo,
    _preview_rsync,
    _preview_etcd,
    _preview_consul,
    _preview_rabbitmq,
    _preview_solr,
    _preview_gitea,
    _preview_azureblob,
    _preview_gcsbucket
)

from leakscope.exporters.service import (
    export_preview_data
)

def _demo_query_result(provider, query, config, type_name):
    """Use provider evidence only; legacy handlers can perform direct probes."""
    from leakscope.phase3.providers import search_provider
    from leakscope.phase3.state import ingest_observation
    result=search_provider(provider,query,config,demo=True)
    endpoint_ids=[];payload={};filtered=0
    for row in result.get('observations',[])[:1]:
        metadata=row.get('metadata') or {}
        if is_blacklisted(row.get('address',''),{'config':config}) or is_blacklisted(metadata.get('resolved_ip',''),{'config':config}):
            filtered+=1;continue
        if not row.get('service_type'):
            row=dict(row,service_type=type_name,metadata=dict(metadata,service_type_basis='Requested legacy Demo search profile; identity not verified'))
        endpoint=ingest_observation(provider,row)
        endpoint_ids.append(str(endpoint.pk))
        payload[0]={0:{'ip':endpoint.address,'port':str(endpoint.port),'endpoint_id':str(endpoint.pk),'title':endpoint.title}}
    return {key:value for key,value in result.items() if key!='observations'} | {
        'type':type_name or 'custom','provider':provider,'total':len(endpoint_ids),'events':result.get('total',0),
        'results':payload,'endpoint_ids':endpoint_ids,'stored':len(endpoint_ids),'filtered':filtered,
        'completed_pages':result.get('pages',0),'requested_pages':1,'results_url':'/assets/',
        'partial':bool(result.get('partial') or filtered),'automatic_target_requests':False}


@shared_task(bind=True)
def check_main(self, fk, keyword=None, country=None, network=None, page=None, page_count=None, type=None, exclude=None,
               active_probe=False, provider: str = "shodan", zoomeye_pagesize=None, demo=False):
    from leakscope.phase3.usage_policy import validate_demo
    demo=validate_demo(demo)
    config = get_config()
    search = Search.objects.get(id=fk)
    progress_recorder = ProgressRecorder(self)
    results = {}

    provider_lower = (provider or getattr(search, "provider", "shodan") or "shodan").lower()
    if provider_lower not in ("shodan", "zoomeye"):
        if demo:raise ValueError("Legacy Demo requires Shodan or ZoomEye; use normalized discovery for Netlas")
        provider_lower = "shodan"

    type_lower = (type or "").lower()
    keyword_for_query = keyword if keyword else None

    qmap = provider_queries(provider_lower)
    base_query = qmap.get(type_lower, "")
    if not base_query and type_lower not in keys_all:
        error_msg = f"Unsupported search type: {type}"
        logger.error(error_msg)
        publish_state(self, state="FAILURE", meta={"error": error_msg})
        raise Ignore()

    final_query = build_provider_query(provider_lower, base_query, keyword=keyword_for_query, country=country,
                                       network=network, exclude=exclude)
    if demo:
        try:
            meta=_demo_query_result(provider_lower,final_query,config['config'],type_lower)
            publish_state(self,state='SUCCESS',meta=meta)
        except Exception as exc:
            publish_state(self,state='FAILURE',meta={'demo':True,'error':redact(exc),'provider':provider_lower})
        raise Ignore()
    try:
        page_num = int(page) if page else 1
    except ValueError:
        page_num = 1
    try:
        total_pages = int(page_count) if page_count else 1
    except ValueError:
        total_pages = 1
    total_pages = max(1, total_pages)

    matches = []
    fetch_errors = []
    completed_pages = 0

    try:
        events = 0
        if provider_lower == "zoomeye":
            client = get_zoomeye_client()
            sub_type = config['config'].get('ZOOMEYE_SUBTYPE', 'all')
            pagesize_raw = zoomeye_pagesize or config['config'].get('ZOOMEYE_PAGESIZE', 1)
            try:
                pagesize = max(1, min(int(pagesize_raw), 100))
            except (ValueError, TypeError):
                pagesize = 1
            for pnum in range(page_num, page_num + total_pages):
                response = client.search(final_query, page=pnum, pagesize=pagesize, sub_type=sub_type)
                raw_matches = response.get('data', []) or []
                events = max(events, response.get('total', len(raw_matches)))
                matches.extend(norm for norm in (normalize_match(m) for m in raw_matches) if norm.get('ip_str'))
                completed_pages += 1
        else:
            client = get_shodan_client()
            variants = {
                'registry': [base_query, qmap.get('harbor', ''), qmap.get('dockerapi', '')],
                'harbor': [base_query, qmap.get('registry', ''), qmap.get('dockerapi', '')],
                'dockerapi': [base_query, qmap.get('registry', ''), qmap.get('harbor', '')],
                'rethink': [base_query, 'product:"RethinkDB"', 'product:"rethinkdb"', 'rethinkdb'],
                'solr': [base_query, 'http.title:"Apache Solr"', 'http.title:"Solr Admin"'],
            }.get(type_lower, [base_query])
            for pnum in range(page_num, page_num + total_pages):
                response = None
                for variant in dict.fromkeys(variants):
                    if not variant:
                        continue
                    query = build_provider_query(provider_lower, variant, keyword=keyword_for_query,
                                                 country=country, network=network, exclude=exclude)
                    try:
                        response = client.host_search(query, page=pnum)
                        break
                    except ShodanClientError as exc:
                        if 'query' not in str(exc).lower() or 'invalid' not in str(exc).lower():
                            raise
                if response is None:
                    raise ShodanClientError('No supported query variant')
                page_matches = response.get('matches', []) or []
                events = max(events, response.get('total', len(page_matches)))
                matches.extend(page_matches)
                completed_pages += 1
        total = len(matches)
    except Exception as exc:
        message = redact(exc)
        if not completed_pages:
            publish_state(self, state="FAILURE", meta={"error": message, "provider": provider_lower})
            raise Ignore()
        # Previously completed paid pages are still processed and persisted.
        # A failed later request is not retried implicitly.
        fetch_errors.append(message)
        total = len(matches)

    if provider_lower == "zoomeye" and total == 0:
        # No results from ZoomEye; simply return empty set
        pass

    if total == 0:
        publish_state(self, state="SUCCESS",
                          meta={"type": type_lower or 'unknown', "total": 0, 'events': events, 'results': results, 'partial': bool(fetch_errors), 'errors': fetch_errors, 'completed_pages': completed_pages, 'requested_pages': total_pages})
        raise Ignore()

    default_total = total if total > 0 else 1
    result_type = 'keys' if type_lower in keys_all else type_lower

    processing_errors = []
    for index, match in enumerate(matches):
        try:
            result_type = canonical_type(type_lower)
            result_payload = dispatch_discovery(type_lower,index,match,search,config,
                                                keyword=keyword,active_probe=active_probe)

            results[index] = result_payload
        except Exception as exc:  # pragma: no cover - ensure one failure doesn't break whole batch
            processing_errors.append(redact(exc))
            logger.error("Handler failed for %s: %s", type_lower, redact(exc))
            results[index] = {}

        progress_recorder.set_progress(index + 1, total=default_total)
        # Track fingerprints and annotate status for returned payloads
        payload = results.get(index, {})
        if isinstance(payload, dict):
            for _, entry in payload.items():
                ip_addr = entry.get("ip")
                port = entry.get("port")
                status = record_fingerprint(type_lower, ip_addr, port)
                entry["status"] = status

    failed = bool(processing_errors) and len(processing_errors) == len(matches)
    publish_state(self, state="FAILURE" if failed else "SUCCESS",
                  meta={"type": result_type if matches else type_lower, "total": total,
                        'events': events, 'results': results, 'provider': provider_lower,
                        'partial': bool(fetch_errors or processing_errors) and not failed, 'errors': fetch_errors + processing_errors,
                        'completed_pages': completed_pages, 'requested_pages': total_pages,
                        'error': 'All matches failed to process' if failed else ''})

    raise Ignore()


@shared_task(bind=True)
def check_custom_query(self, custom_query_id, keyword=None, country=None, network=None, exclude=None, page=None,
                       page_count=None, active_probe=None, provider: str = "shodan", demo=False):
    from leakscope.phase3.usage_policy import validate_demo
    demo=validate_demo(demo)
    config = get_config()
    result_type = "custom"
    try:
        custom_query = CustomQuery.objects.get(id=custom_query_id)
    except CustomQuery.DoesNotExist:
        error_msg = f"Custom query {custom_query_id} not found"
        logger.error(error_msg)
        publish_state(self, state="FAILURE", meta={"error": error_msg})
        raise Ignore()

    result_type = (custom_query.default_type or "custom").lower()

    provider_lower = (provider or custom_query.provider or "shodan").lower()
    if provider_lower not in ("shodan", "zoomeye"):
        if demo:raise ValueError("Legacy Demo requires Shodan or ZoomEye; use normalized discovery for Netlas")
        provider_lower = "shodan"

    # Track run metadata
    CustomQuery.objects.filter(id=custom_query_id).update(
        last_run_at=timezone.now(), last_status="running", last_error=""
    )

    active_probe_flag = custom_query.active_probe_default if active_probe is None else active_probe

    params = {
        "keyword": keyword or "",
        "country": country or "",
        "network": network or "",
        "exclude": exclude or "",
    }

    rendered_query = render_query_template(custom_query.query_template, params).strip()
    if not rendered_query:
        msg = "Rendered query is empty; check your template/parameters."
        CustomQuery.objects.filter(id=custom_query_id).update(last_status="failed", last_error=msg)
        publish_state(self, state="FAILURE", meta={"error": msg})
        raise Ignore()

    if demo:
        try:
            meta=_demo_query_result(provider_lower,rendered_query,config['config'],result_type)
            CustomQuery.objects.filter(id=custom_query_id).update(last_status='partial' if meta['partial'] else 'success',last_error='')
            publish_state(self,state='SUCCESS',meta=meta)
        except Exception as exc:
            message=redact(exc)
            CustomQuery.objects.filter(id=custom_query_id).update(last_status='failed',last_error=message)
            publish_state(self,state='FAILURE',meta={'demo':True,'error':message,'provider':provider_lower})
        raise Ignore()
    try:
        page_num = int(page) if page else 1
    except ValueError:
        page_num = 1
    try:
        total_pages = int(page_count) if page_count else 1
    except ValueError:
        total_pages = 1
    total_pages = max(1, total_pages)

    fetch_errors = []
    collected = []
    events_total = 0
    completed_pages = 0
    try:
        if provider_lower == "zoomeye":
            client = get_zoomeye_client()
            collected = []
            events_total = 0
            pagesize_raw = config['config'].get('ZOOMEYE_PAGESIZE', 1)
            try:
                pagesize = int(pagesize_raw)
            except Exception:
                pagesize = 1
            for pnum in range(page_num, page_num + total_pages):
                resp = client.search(rendered_query, page=pnum, pagesize=pagesize, sub_type=config['config'].get('ZOOMEYE_SUBTYPE', 'all'))
                events_total = max(events_total, resp.get('total', 0))
                collected.extend(resp.get('data', []) or [])
                completed_pages += 1
            response = {"data": collected, "total": events_total if events_total else len(collected)}
        else:
            client = get_shodan_client()
            collected = []
            events_total = 0
            for pnum in range(page_num, page_num + total_pages):
                resp = client.host_search(rendered_query, page=pnum)
                events_total = max(events_total, resp.get('total', 0))
                collected.extend(resp.get('matches', []) or [])
                completed_pages += 1
            response = {"matches": collected, "total": events_total if events_total else len(collected)}
    except Exception as exc:
        error_msg = redact(f"{provider_lower.title()} search failed: {exc}")
        if not completed_pages:
            CustomQuery.objects.filter(id=custom_query_id).update(last_status="failed", last_error=error_msg)
            publish_state(self, state="FAILURE", meta={"type": result_type, "total": 0, "events": 0, "results": {}, "error": error_msg})
            raise Ignore()
        fetch_errors.append(error_msg)
        response = {'data' if provider_lower == 'zoomeye' else 'matches': collected,
                    'total': events_total or len(collected)}

    if provider_lower == "zoomeye":
        raw_matches = response.get('data', []) or []
        matches = []
        for m in raw_matches:
            norm = normalize_match(m)
            if not norm.get('ip_str'):
                continue
            matches.append(norm)
        total = len(matches)
        events = response.get('total', total)
    else:
        matches = response.get('matches', [])
        total = len(matches)
        events = response.get('total', total)
    result_type = (custom_query.default_type or "custom").lower()
    search = Search(type=result_type, keyword=keyword or "", country=country or "", network=network or "", provider=provider_lower)
    search.save()

    progress_recorder = ProgressRecorder(self)
    results: Dict[int, Dict[str, Any]] = {}

    default_total = total if total > 0 else 1

    processing_errors = []
    for index, match in enumerate(matches):
        try:
            ip_addr, port = extract_ip_port(match)
            if is_blacklisted(ip_addr, config):
                results[index] = {}
                progress_recorder.set_progress(index + 1, total=default_total)
                continue
            handler = discovery_handler(result_type)
            if handler:
                payload = dispatch_discovery(result_type,index,match,search,config,
                                             keyword=keyword,active_probe=active_probe_flag) or {}
            else:
                ip_addr, port = extract_ip_port(match)
                banner = get_http_html(match) or (match.get('data') if isinstance(match.get('data'), str) else "")
                snippet = (banner or "")[:200]
                payload = {
                    index: {
                        "ip": ip_addr,
                        "port": port,
                        "snippet": snippet,
                    }
                }

            results[index] = payload
        except Exception as exc:  # pragma: no cover - keep batch alive
            processing_errors.append(redact(exc))
            logger.error("Custom handler failed: %s", redact(exc))
            results[index] = {}

        progress_recorder.set_progress(index + 1, total=default_total)
        payload = results.get(index, {})
        if isinstance(payload, dict):
            for _, entry in payload.items():
                ip_addr = entry.get("ip")
                port = entry.get("port")
                status = record_fingerprint(result_type, ip_addr, port)
                entry["status"] = status

    failed = bool(processing_errors) and len(processing_errors) == len(matches)
    CustomQuery.objects.filter(id=custom_query_id).update(
        last_status="failed" if failed else ("partial" if fetch_errors or processing_errors else "success"),
        last_error='; '.join(fetch_errors + processing_errors)[:2000])
    publish_state(self, state="FAILURE" if failed else "SUCCESS",
                  meta={"type": result_type or 'custom', "total": total,
                        'events': events, 'results': results, 'provider': provider_lower,
                        'partial': bool(fetch_errors or processing_errors) and not failed, 'errors': fetch_errors + processing_errors,
                        'completed_pages': completed_pages, 'requested_pages': total_pages,
                        'error': 'All matches failed to process' if failed else ''})
    raise Ignore()


@shared_task(bind=True)
def run_preview(self, type=None, target_id=None, active_probe=False):
    if not active_probe:
        publish_state(self, state="FAILURE", meta={"error": "Active probing required for preview"})
        raise Ignore()

    type_lower = canonical_type(type)
    Model = model_for_type(type_lower)
    if not Model:
        publish_state(self, state="FAILURE", meta={"error": f"Preview not implemented for {type_lower}"})
        raise Ignore()

    try:
        obj = Model.objects.get(id=target_id)
    except Model.DoesNotExist:
        publish_state(self, state="FAILURE", meta={"error": "Target not found"})
        raise Ignore()

    result = preview_for_type(type_lower,obj)

    safe_items = _json_safe(result.get("items", []))
    safe_summary = _json_safe(result.get("summary", ""))
    publish_state(self, state="SUCCESS", meta={"type": type_lower, "summary": safe_summary,
                                             "items": safe_items})
    raise Ignore()


@app.task
def monitor_periodic():
    all = Monitor.objects.all()

    for i in all:
        try:
            types = ast.literal_eval(i.types)
        except Exception:
            types = i.types
        monitor(types, keyword=i.keyword, network=i.network)


@app.task
def monitor(types, keyword="", network="", provider: str = "shodan"):
    def _normalize_types(raw_types):
        if isinstance(raw_types, (list, tuple)):
            collected = []
            for entry in raw_types:
                if isinstance(entry, str):
                    collected.extend([part.strip() for part in entry.split(',') if part.strip()])
                else:
                    collected.append(str(entry).strip())
            return collected
        if isinstance(raw_types, str):
            return [part.strip() for part in raw_types.split(',') if part.strip()]
        return [str(raw_types).strip()]

    type_list = _normalize_types(types)

    if not type_list:
        return {}

    provider_lower = (provider or "shodan").lower()
    try:
        client = get_zoomeye_client() if provider_lower == "zoomeye" else get_shodan_client()
    except (ShodanClientError, ZoomEyeClientError) as exc:
        logger.error("Unable to initialise %s client for monitor: %s", provider_lower, exc)
        return {}

    config = get_config()
    query_hint = keyword or network or ""

    return_dict = {}

    qmap = provider_queries(provider_lower)

    for monitor_type in type_list:
        type_lower = monitor_type.lower()
        return_dict[type_lower] = {"keyword": query_hint, "results": {}}

        base_query = qmap.get(type_lower, "")
        if not base_query and type_lower not in keys_all:
            logger.warning("Skipping unsupported monitor type %s", monitor_type)
            continue

        search = Search(type=monitor_type, keyword=keyword, network=network, provider=provider_lower)
        search.save()

        final_query = build_provider_query(provider_lower, base_query, keyword=keyword, network=network)
        try:
            if provider_lower == "zoomeye":
                response = client.search(final_query, page=1, pagesize=1, sub_type=config['config'].get('ZOOMEYE_SUBTYPE', 'all'))
                raw_matches = response.get('data', []) or []
                matches = []
                for m in raw_matches:
                    norm = normalize_match(m)
                    if not norm.get('ip_str'):
                        continue
                    matches.append(norm)
            else:
                response = client.host_search(final_query, page=1)
                matches = response.get('matches', [])
        except (ShodanClientError, ZoomEyeClientError) as exc:
            logger.error("%s monitor query failed for %s: %s", provider_lower, monitor_type, exc)
            continue
        except Exception as exc:  # pragma: no cover
            logger.exception("Unexpected error during %s monitor query for %s", provider_lower, monitor_type)
            continue

        for c, match in enumerate(matches):
            if discovery_handler(type_lower):
                payload = dispatch_discovery(type_lower,c,match,search,config,keyword=keyword,active_probe=False)
            else:
                payload = {}

            if payload:
                for key, value in payload.items():
                    if value:
                        return_dict[type_lower]['results'][key] = value

    if return_dict:
        send_mail(return_dict)

    return return_dict


def send_mail(results):
    config = get_config()
    monitoring_cfg = config['config'].get('monitoring', {})
    gmail_user = monitoring_cfg.get('gmail_email')
    gmail_password = monitoring_cfg.get('gmail_password')

    if not gmail_user or not gmail_password:
        logger.warning('Skipping monitoring email: Gmail credentials not configured')
        return

    body_lines = []
    for monitor_type, payload in results.items():
        body_lines.append(
            'Your results for <b>{}</b> with keyword <b>{}</b><br>'.format(monitor_type, payload.get('keyword', ''))
        )
        if not payload.get('results'):
            body_lines.append('No results<br>')
        for item in payload.get('results', {}).values():
            ip_addr = item.get('ip')
            if ip_addr:
                body_lines.append(f"https://www.shodan.io/host/{ip_addr}<br>")

    msg = email.message.Message()
    msg['Subject'] = 'LeakLooker Notification'
    msg['From'] = gmail_user
    msg['To'] = gmail_user
    msg.add_header('Content-Type', 'text/html')
    msg.set_payload(''.join(body_lines))

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15)
        server.ehlo()
        server.login(gmail_user, gmail_password)
        server.sendmail(msg['From'], [msg['To']], msg.as_string())
        server.close()
        logger.info('Monitoring email sent')
    except Exception as exc:
        logger.error('Failed to send monitoring email: %s', exc)


@shared_task(bind=True)
def brute_buckets(self, keyword, active_probe=False):
    if not active_probe:
        # Skip network probing when disabled.
        publish_state(self,
            state="SUCCESS",
            meta={
                "type": "amazon",
                "total": 0,
                "skipped": True,
                "requires_active": True,
                "message": "Bucket brute-force requires active probing. Enable the toggle to run it.",
            },
        )
        raise Ignore()

    progress_recorder = ProgressRecorder(self)

    total = len(buckets_bruteforce)
    k = []
    for c,i in enumerate(buckets_bruteforce):


        req = requests.get("https://" + keyword + i.rstrip() + ".s3.amazonaws.com", timeout=(5, 15))


        if req.status_code == 200 or req.status_code == 403:
            am = AmazonBuckets(bucket=keyword + i.rstrip() + ".s3.amazonaws.com", confirmed=False,for_later=False)
            am.save()

        progress_recorder.set_progress(c+1, total=total)

        publish_state(self, state="PROGRESS",
                          meta={"results": "https://" + keyword + i.rstrip() + ".s3.amazonaws.com",
                                "code": req.status_code, "percentage": c / total * 100})

    publish_state(self, state="SUCCESS",
                      meta={"type": 'amazon', "total": total})

    raise Ignore()


@shared_task(bind=True)
def javascript_search(self, keyword, active_probe=False):
    if not active_probe:
        publish_state(self,
            state="SUCCESS",
            meta={
                "type": "js",
                "total": 0,
                "skipped": True,
                "requires_active": True,
                "message": "Javascript fetch requires active probing. Enable the toggle to run it.",
            },
        )
        raise Ignore()

    ff = send_request(keyword)
    enpoints = parser_file(ff, regex_str, keyword)

    links = []
    context = []

    for i in enpoints:
        links.append(i['link'])
        context.append(i['context'])

    js = Javascript(secrets=links, context=context, path=keyword)
    js.save()

    publish_state(self, state="SUCCESS",
                      meta={"type": 'js', "secrets": links, 'context':context,'path':keyword})

    raise Ignore()


@shared_task(bind=True)
def github_repo_search(self, keyword, active_probe=False):
    if not active_probe:
        publish_state(self,
            state="SUCCESS",
            meta={
                "type": "github_repo",
                "total": 0,
                "skipped": True,
                "requires_active": True,
                "message": "Repository cloning requires active probing. Enable the toggle to run it.",
            },
        )
        raise Ignore()

    progress_recorder = ProgressRecorder(self)
    project_path = clone_git_repo(keyword)  # cloning repo
    repo_git = Repo(project_path)
    branches = repo_git.remotes.origin.fetch(kill_after_timeout=60)
    checked = set()  # set for already checked elements
    total2= len(list(repo_git.iter_commits('HEAD')))
    results = {}
    paths = []
    # print("Searching repo: " + keyword)
    for remote_branch in branches:  # for every branch in repo
        branch_name = remote_branch.name  # fetch branch name
        # print("Searching in branch: " + branch_name)
        previous_commit = None
        for c,current_commit in enumerate(repo_git.iter_commits(branch_name)):
            percent = round(c / total2 * 100, 2)

            progress_recorder.set_progress(c + 1, total=total2)
            publish_state(self, state="PROGRESS",
                              meta={"commit": str(current_commit), "percent": percent})
            # print("Searching in commit: " + str(current_commit))
            diff_hash = hashlib.md5((str(previous_commit) + str(current_commit)).encode(
                'utf-8')).digest()  # calculate hash from diffs to check if it has been checked already
            if not previous_commit:  # first commit
                previous_commit = current_commit
                continue
            elif diff_hash in checked:  # if hash has been checked, it means that previous commit becomes current commit
                previous_commit = current_commit
                continue
            else:

                diff = previous_commit.diff(current_commit, create_patch=True)  # calculate diff
                checked.add(diff_hash)  # add diff hash to checked list
                # all_secrets = {'paths':}
                secrets = get_secrets(diff, branch_name, previous_commit)
                secrets_from_rules = rules(diff)

                # print(secrets_from_rules)
                # print(secrets)


                # all_secrets = {**secrets, **secrets_from_rules}
                #
                # print(all_secrets)
                # two_secrets = list(secrets) + list(secrets_from_rules)
                if len(secrets['secrets']) > 0 or len(secrets_from_rules['secrets']) > 0:
                    together_paths = set(list(secrets_from_rules['path']) + list(secrets['path']))
                    together_secrets = secrets_from_rules['secrets'] + secrets['secrets']



                    asd = set(together_secrets)

                    all_secrets = {'path': list(together_paths),
                                   "secret": list(asd)}

                    results[str(previous_commit)] = all_secrets

                    # print(two_secrets)

                    am = Github(commit=str(current_commit), keyword=keyword, path=list(together_paths), secret=list(asd), confirmed=False,
                                for_later=False)
                    am.save()


            previous_commit = current_commit

    publish_state(self, state="SUCCESS",
                      meta={"type": 'github', "results":results})

    raise Ignore()


@app.task(name="leakscope.tasks.cleanup_exports")
def cleanup_exports():
    from leakscope.exporters.storage import cleanup_expired_exports
    return {'removed': cleanup_expired_exports()}


@app.task(name="leakscope.tasks.restore_database", soft_time_limit=3300, time_limit=3600)
def restore_database(name, original=""):
    from leakscope import database_archive
    return database_archive.run_import(name, original)
