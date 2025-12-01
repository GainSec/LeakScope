from django.shortcuts import render
import os
import socket
from pathlib import Path
from leakscope_app import forms
from leakscope_app.models import Search, Amazons3be, Gitlab, Elastic, Rethink, Mongo, Cassandra, Ftp, AmazonBuckets, Github, FingerprintLog, CustomQuery, Dirs, Jenkins, Keys, Angular, Sonarqube, Couchdb, Kibana, Rsync, Minio, Grafana, Prometheus, Swagger, MongoExpress, OpenSearch, Amazonbe, Javascript, BlacklistEntry, Nexus, Artifactory, Registry, Etcd, ConsulKV, Rabbitmq, Solr, Gitea, Gogs, AzureBlob, GcsBucket
from leakscope import tasks
from leakscope.tasks import check_main
from leakscope.tasks import _http_base
from django.apps import apps
import json
import hashlib
from django.db import models
from django.http import HttpResponse, JsonResponse, FileResponse
from django.http import HttpResponseRedirect
import random
from celery.result import AsyncResult
import csv
from datetime import datetime
from django.utils import timezone
import requests
from django.apps import apps as django_apps
import base64
import json
from leakscope.tasks import queries_zoomeye
from pymongo import MongoClient


types = ['angular','amazons3be',"gitlab","elastic","dirs","jenkins","mongo","rsync",'sonarqube','couchdb',"kibana","cassandra","rethink", "ftp",
         "opensearch","grafana","prometheus","minio","swagger","mongoexpress","nexus","artifactory","registry","harbor","dockerapi","etcd","consul","rabbitmq","solr","gitea","gogs","azureblob","gcsbucket"]


def _flag(value):
    return str(value).lower() in {'1', 'true', 'yes', 'on'}

def _normalize_terms(value):
    if isinstance(value, dict):
        return sorted(value.keys())
    if isinstance(value, list):
        return sorted([str(v) for v in value])
    return []


def _fingerprint(type_name, ip, port):
    raw = f"{(type_name or '').lower()}|{ip or ''}|{port or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()

def _get_private_ip():
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "unknown"

def _get_public_ip():
    try:
        resp = requests.get("https://api.ipify.org", timeout=3)
        if resp.ok:
            return resp.text.strip()
    except Exception:
        pass
    return "unknown"

def index(request):

    credits = tasks.check_credits()
    zoomeye_credits = tasks.check_credits(provider="zoomeye")
    for_later_counter = 0
    confirmed_counter = 0
    all_counter = 0
    labels = []
    data = []
    random_leaks_context = {}
    for_later_context = {}
    open_stats = []

    app_models = apps.get_app_config('leakscope_app').get_models()
    for i in app_models:
        field_names = {f.name for f in i._meta.fields}
        if 'for_later' not in field_names or 'confirmed' not in field_names:
            continue
        try:
            for_later_counter += i.objects.filter(for_later=True).count()
            confirmed_counter += i.objects.filter(confirmed=True).count()
            all_counter += i.objects.all().count()
            labels.append(i.__name__)
            data.append(i.objects.all().count())
        except Exception as e:
            print(e)

    random_model = random.choice(labels)
    model = apps.get_model('leakscope_app', random_model)
    random_leaks = model.objects.filter(confirmed=False,for_later=False)
    try:
        random_leaks_context[random_model] = random.sample(list(random_leaks),8)
    except:
        random_leaks_context[random_model] = []

    try:
        for_later = model.objects.filter(confirmed=False,for_later=True).order_by('-id')
        for_later_context[random_model] = list(for_later)
    except Exception as e:
        print(e)

    try:
        checked_elastic = Elastic.objects.filter(confirmed=True) | Elastic.objects.filter(for_later=True)
        percentage_of_checked_elastic = checked_elastic.count()

        checked_rethink = Rethink.objects.filter(confirmed=True) | Rethink.objects.filter(for_later=True)
        percentage_of_checked_rethink = checked_rethink.count()

        checked_mongo = Mongo.objects.filter(confirmed=True) | Mongo.objects.filter(for_later=True)
        percentage_of_checked_mongo = checked_mongo.count()

        checked_cassandra = Cassandra.objects.filter(confirmed=True) | Cassandra.objects.filter(for_later=True)
        percentage_of_checked_cassandra = checked_cassandra.count()

        open_stats.append(percentage_of_checked_elastic)
        open_stats.append(percentage_of_checked_rethink)
        open_stats.append(percentage_of_checked_mongo)
        open_stats.append(percentage_of_checked_cassandra)

    except Exception as e:
        print(e)

    # Build provider-split counts for stacked chart
    provider_counts = {"shodan": {}, "zoomeye": {}}
    for model in app_models:
        try:
            provider_field = None
            fields = {f.name for f in model._meta.fields}
            if 'provider' in fields:
                provider_field = 'provider'
            qs = model.objects.all()
            if provider_field:
                grouped = qs.values_list('provider').annotate(models.Count('id'))
                for provider, count in grouped:
                    prov = (provider or 'shodan').lower()
                    provider_counts.setdefault(prov, {})[model.__name__] = provider_counts.setdefault(prov, {}).get(model.__name__, 0) + count
            else:
                # Fallback if no provider field: count everything as shodan
                count = qs.count()
                provider_counts['shodan'][model.__name__] = provider_counts['shodan'].get(model.__name__, 0) + count
        except Exception:
            continue

    shodan_data = [provider_counts.get("shodan", {}).get(lbl, 0) for lbl in labels]
    zoomeye_data = [provider_counts.get("zoomeye", {}).get(lbl, 0) for lbl in labels]

    context = {'credits':credits,
               'zoomeye_credits': zoomeye_credits,
               "for_later_counter":for_later_counter,
               "confirmed_counter":confirmed_counter,
               "all_counter":all_counter,
               "labels": json.dumps(labels or []),
               'data': json.dumps(data or []),
               'shodan_data': json.dumps(shodan_data or []),
               'zoomeye_data': json.dumps(zoomeye_data or []),
               "random_leaks":random_leaks_context,
               "for_later":for_later_context,
               "percentages":open_stats}

    return render(request, 'index.html', context)

def landing(request):
    env_keys = ["SHODAN_API_KEY", "ZOOMEYE_API_KEY", "BLACKLIST"]
    env_status = []
    for key in env_keys:
        env_status.append({"key": key, "set": bool(os.environ.get(key))})

    total_entries = 0
    for model in apps.get_app_config('leakscope_app').get_models():
        try:
            total_entries += model.objects.count()
        except Exception:
            continue
    # Credits: count unique searches that produced stored results (Shodan only charges for non-empty hits)
    shodan_search_ids = set()
    zoomeye_search_ids = set()
    try:
        for model in apps.get_app_config('leakscope_app').get_models():
            search_field = None
            for field in model._meta.fields:
                if field.name == "search" and getattr(field, "related_model", None) == Search:
                    search_field = field
                    break
            if not search_field:
                continue
            ids = model.objects.values_list("search_id", flat=True).distinct()
            if ids:
                shodan_search_ids.update(list(Search.objects.filter(id__in=ids, provider="shodan").values_list("id", flat=True)))
                zoomeye_search_ids.update(list(Search.objects.filter(id__in=ids, provider="zoomeye").values_list("id", flat=True)))
        shodan_credits_used = len(shodan_search_ids)
        zoomeye_credits_used = len(zoomeye_search_ids)
    except Exception:
        shodan_credits_used = 0
        zoomeye_credits_used = 0

    context = {
        "private_ip": _get_private_ip(),
        "public_ip": _get_public_ip(),
        "env_status": env_status,
        "total_entries": total_entries,
        "shodan_credits_used": shodan_credits_used,
        "zoomeye_credits_used": zoomeye_credits_used,
    }
    return render(request, "landing.html", context)


def stats(request, type=None):
    count = tasks.stats(type)
    return HttpResponse(json.dumps({"stat": count}), content_type='application/json')


def stats_db(request, type=None):
    model_map = {
        "angular": Angular,
        "amazons3be": Amazons3be,
        "amazonbe": Amazonbe,
        "amazonbuckets": AmazonBuckets,
        "gitlab": Gitlab,
        "elastic": Elastic,
        "dirs": Dirs,
        "jenkins": Jenkins,
        "mongo": Mongo,
        "rsync": Rsync,
        "sonarqube": Sonarqube,
        "couchdb": Couchdb,
        "kibana": Kibana,
        "cassandra": Cassandra,
        "rethink": Rethink,
        "ftp": Ftp,
        "opensearch": OpenSearch,
        "grafana": Grafana,
        "prometheus": Prometheus,
        "minio": Minio,
        "swagger": Swagger,
        "mongoexpress": MongoExpress,
        "keys": Keys,
        "javascript": Javascript,
        "github": Github,
        "nexus": Nexus,
        "artifactory": Artifactory,
        "registry": Registry,
        "harbor": Registry,
        "dockerapi": Registry,
        "etcd": Etcd,
        "consul": ConsulKV,
        "rabbitmq": Rabbitmq,
        "solr": Solr,
        "gitea": Gitea,
        "gogs": Gogs,
        "azureblob": AzureBlob,
        "gcsbucket": GcsBucket,
    }
    Model = model_map.get((type or "").lower())
    count = Model.objects.count() if Model else 0
    return HttpResponse(json.dumps({"stat": count}), content_type='application/json')


def browse(request, type=None):
    model_map = {
        "angular": Angular,
        "amazons3be": Amazons3be,
        "amazonbe": Amazonbe,
        "amazonbuckets": AmazonBuckets,
        "gitlab": Gitlab,
        "elastic": Elastic,
        "dirs": Dirs,
        "jenkins": Jenkins,
        "mongo": Mongo,
        "rsync": Rsync,
        "sonarqube": Sonarqube,
        "couchdb": Couchdb,
        "kibana": Kibana,
        "cassandra": Cassandra,
        "rethink": Rethink,
        "ftp": Ftp,
        "opensearch": OpenSearch,
        "grafana": Grafana,
        "prometheus": Prometheus,
        "minio": Minio,
        "swagger": Swagger,
        "mongoexpress": MongoExpress,
        "keys": Keys,
        "javascript": Javascript,
        "github": Github,
        "nexus": Nexus,
        "artifactory": Artifactory,
        "registry": Registry,
        "harbor": Registry,
        "dockerapi": Registry,
        "etcd": Etcd,
        "consul": ConsulKV,
        "rabbitmq": Rabbitmq,
        "solr": Solr,
        "gitea": Gitea,
        "gogs": Gogs,
        "azureblob": AzureBlob,
        "gcsbucket": GcsBucket,
    }
    Model = model_map.get((type or "").lower())
    if not Model:
        return HttpResponse("Unsupported type", status=400)
    try:
        objs = Model.objects.all().order_by("-id")[:500]
    except Exception:
        objs = Model.objects.all()
    return render(request, 'browse.html', {'type': type, 'all': objs})
def database(request):
    status_map = {fp.fingerprint: fp.status for fp in FingerprintLog.objects.all()}
    hidden_entries = []
    results = []
    for i in apps.get_models(include_auto_created=False, include_swapped=False):
        if i.__name__.lower() in types:
            objs = list(i.objects.all())
            for obj in objs:
                fp = _fingerprint(i.__name__, getattr(obj, "ip", ""), getattr(obj, "port", ""))
                status = status_map.get(fp, '')
                if status == "hidden":
                    hidden_entries.append({
                        "type": i.__name__,
                        "ip": getattr(obj, "ip", ""),
                        "port": getattr(obj, "port", ""),
                        "keyword": getattr(getattr(obj, "search", None), "keyword", ""),
                        "created": getattr(getattr(obj, "search", None), "created_on", ""),
                        "provider": getattr(getattr(obj, "search", None), "provider", "shodan"),
                    })
                    continue
                obj.status = status
            results.append(objs)

    return render(request, 'database.html', {"results": results, "hidden_entries": hidden_entries})

def keyword(request):

    return render(request, "search_keyword.html",{})

def search(request,type):
    context = {"type":type}

    return render(request, 'search.html', context)

def keyword_search_results(request):
    if request.is_ajax() and request.method == 'GET':

        keyword = request.GET['keyword']
        exclude = request.GET.get('exclude', '')
        country = request.GET['country']
        network = request.GET['network']
        type = request.GET['type']
        page = request.GET.get('page') or "1"
        page_count = request.GET.get('page_count') or "1"
        provider = (request.GET.get('provider') or 'shodan').lower()
        zoomeye_pagesize = request.GET.get('zoomeye_pagesize') or ""
        active_probe = request.GET.get('active_probe') in {'1', 'true', 'True', 'on'}
        search = Search(type=type, keyword=keyword,country=country,network=network, provider=provider)
        print(search.id)

        search.save()

        gitlab_search_task = tasks.check_main.delay(fk=search.id, page=page, keyword=keyword, country=country,
                                          network=network, type=type, exclude=exclude, provider=provider, page_count=page_count,
                                          active_probe=active_probe, zoomeye_pagesize=zoomeye_pagesize)
        request.session['task_id'] = gitlab_search_task.task_id

        # return render(request, 'search.html', context={'task_id': gitlab_search_task.task_id, 'type':type})
        return HttpResponse(json.dumps({'task_id': gitlab_search_task.task_id, "type":type}), content_type='application/json')
    else:
        return HttpResponse(json.dumps({'OK2': '123123'}), content_type='application/json')

def search_results(request,type):
    if request.is_ajax() and request.method == "GET":
        keyword = request.GET['keyword']
        exclude = request.GET.get('exclude', '')
        country = request.GET['country']
        network = request.GET['network']
        page = request.GET['page']
        page_count = request.GET.get('page_count') or "1"
        provider = (request.GET.get('provider') or 'shodan').lower()
        active_probe = request.GET.get('active_probe') in {'1', 'true', 'True', 'on'}

        search = Search(type=type, keyword=keyword, country=country, network=network, provider=provider)
        search.save()

        gitlab_search_task = tasks.check_main.delay(fk=search.id, page=page, keyword=keyword, country=country,
                                              network=network, type=type, exclude=exclude, provider=provider, page_count=page_count,
                                              active_probe=active_probe)

        request.session['task_id'] = gitlab_search_task.task_id
        print('test')
        # return render(request, 'search.html', context={'task_id': gitlab_search_task.task_id, 'type':type})
        return HttpResponse(json.dumps({'task_id': gitlab_search_task.task_id}), content_type='application/json')
            # return HttpResponse(json.dumps(gitlab_search_task), content_type='application/json')
    else:
        return HttpResponse(json.dumps({'OK2': 'OK'}), content_type='application/json')


def get_task_info(request):
    task_id = request.GET.get('task_id', None)
    data = {}
    if task_id is not None:
        try:
            task = AsyncResult(task_id)
            meta = task.backend.get_task_meta(task_id)
            if not isinstance(meta, dict):
                meta = {}
        except Exception as exc:
            return HttpResponse(json.dumps({'state': 'FAILURE', 'result': str(exc)}, default=str), content_type='application/json', status=500)

        def _safe(obj):
            try:
                return json.loads(json.dumps(obj, default=str))
            except Exception:
                try:
                    return json.dumps(obj, default=str)
                except Exception:
                    return str(obj)

        try:
            state = meta.get('status') or meta.get('state') or task.state
            result = meta.get('result')

            if state == "PENDING":
                data = {'state': state, 'result': None}
            elif state == "PROGRESS":
                try:
                    pct = result['current'] / result['total'] * 100
                except Exception:
                    pct = None
                data = {'state': state, 'result': _safe(result)}
                if pct is not None:
                    data['percentage'] = pct
            else:
                data = {'state': state, 'result': _safe(result)}
        except Exception as exc:
            data = {'state': 'FAILURE', 'result': f'serialization error: {exc}', 'raw': _safe(meta)}

        try:
            payload = json.dumps(data, default=str)
            return HttpResponse(payload, content_type='application/json')
        except Exception as exc:
            fallback = json.dumps({'state': 'FAILURE', 'result': f'serialization error: {exc}', 'raw': _safe(data)}, default=str)
            return HttpResponse(fallback, content_type='application/json', status=500)
    else:
        return HttpResponse('No job id given.')



from django.apps import apps as django_apps


def hide(request, type=None, ip=None):
    model_map = {
        "angular": Angular,
        "amazons3be": Amazons3be,
        "amazonbe": Amazonbe,
        "amazonbuckets": AmazonBuckets,
        "gitlab": Gitlab,
        "elastic": Elastic,
        "dirs": Dirs,
        "jenkins": Jenkins,
        "mongo": Mongo,
        "rsync": Rsync,
        "sonarqube": Sonarqube,
        "couchdb": Couchdb,
        "kibana": Kibana,
        "cassandra": Cassandra,
        "rethink": Rethink,
        "ftp": Ftp,
        "opensearch": OpenSearch,
        "grafana": Grafana,
        "prometheus": Prometheus,
        "minio": Minio,
        "swagger": Swagger,
        "mongoexpress": MongoExpress,
        "keys": Keys,
        "javascript": Javascript,
        "github": Github,
        "registry": Registry,
        "harbor": Registry,
        "dockerapi": Registry,
        "azureblob": AzureBlob,
        "gcsbucket": GcsBucket,
        "solr": Solr,
        "gitea": Gitea,
        "gogs": Gogs,
        "azureblob": AzureBlob,
        "gcsbucket": GcsBucket,
    }
    model = model_map.get((type or '').lower())
    if not model or not ip:
        return HttpResponse(json.dumps({'error': 'invalid'}), content_type='application/json', status=400)
    try:
        obj = model.objects.get(ip=ip)
    except Exception as exc:
        return HttpResponse(json.dumps({'error': str(exc)}), content_type='application/json', status=400)

    port = getattr(obj, "port", "")
    fp = _fingerprint(model.__name__, ip, port)
    log, _ = FingerprintLog.objects.get_or_create(
        fingerprint=fp, defaults={"type": model.__name__, "ip": ip, "port": port}
    )
    log.status = "hidden"
    log.save()
    return HttpResponse(json.dumps({'Results': 'hidden'}), content_type='application/json')


# --- Legacy routes restored as light stubs to keep UI working ---

def amazonbuckets(request):
    return render(request, "amazon.html", {})


def bruteforce_bucket(request):
    if request.is_ajax() and request.method == "GET":
        keyword = request.GET.get('keyword', '')
        active_probe = _flag(request.GET.get("active_probe", False))
        task_res = tasks.brute_buckets.delay(keyword=keyword, active_probe=active_probe)
        request.session['task_id'] = task_res.task_id
        return HttpResponse(json.dumps({'task_id': task_res.task_id}), content_type='application/json')
    return HttpResponse(json.dumps({'error': 'invalid'}), content_type='application/json', status=400)


def js(request):
    return render(request, "js.html", {})


def js_file(request):
    if request.is_ajax() and request.method == "GET":
        keyword = request.GET.get('keyword', '')
        active_probe = _flag(request.GET.get("active_probe", False))
        task_res = tasks.javascript_search.delay(keyword=keyword, active_probe=active_probe)
        request.session['task_id'] = task_res.task_id
        return HttpResponse(json.dumps({'task_id': task_res.task_id}), content_type='application/json')
    return HttpResponse(json.dumps({'error': 'invalid'}), content_type='application/json', status=400)


def github(request):
    return render(request, "github.html", {})


def github_repo(request):
    if request.is_ajax() and request.method == "GET":
        keyword = request.GET.get('keyword', '')
        active_probe = _flag(request.GET.get("active_probe", False))
        task_res = tasks.github_repo_search.delay(keyword=keyword, active_probe=active_probe)
        request.session['task_id'] = task_res.task_id
        return HttpResponse(json.dumps({'task_id': task_res.task_id}), content_type='application/json')
    return HttpResponse(json.dumps({'error': 'invalid'}), content_type='application/json', status=400)


def custom_queries(request):
    filters = []
    facets = []
    helper_error = None
    api_key = os.getenv("SHODAN_API_KEY")
    if api_key:
        try:
            from leakscope.shodan_client import ShodanClient
            client = ShodanClient(api_key)
            raw_filters = client.host_filters()
            raw_facets = client.host_facets()
            filters = list(raw_filters.keys()) if isinstance(raw_filters, dict) else list(raw_filters or [])
            facets = list(raw_facets.keys()) if isinstance(raw_facets, dict) else list(raw_facets or [])
        except Exception as exc:
            helper_error = str(exc)
    else:
        helper_error = "SHODAN_API_KEY missing"

    base_queries = {
        "elastic": 'product:"Elasticsearch" port:9200',
        "opensearch": 'product:"OpenSearch" port:9200',
        "mongo": 'product:"MongoDB" port:27017',
        "cassandra": 'product:"Cassandra" port:9042',
        "rethink": 'product:"rethinkdb" port:28015',
        "etcd": 'product:"etcd" port:2379',
        "consul": 'product:"Consul" port:8500',
        "rabbitmq": 'http.title:"RabbitMQ Management" port:15672',
        "solr": 'product:"Solr"',
        "gitea": 'http.title:"Gitea" port:3000',
        "gogs": 'http.title:"Gogs" port:3000',
        "azureblob": 'http.title:"Azure Blob Storage" OR product:"Azure Blob"',
        "gcsbucket": 'http.title:"Index of /storage.googleapis.com" OR product:"Google Cloud Storage"',
        "kibana": 'product:"Kibana"',
        "gitlab": 'http.title:"GitLab" port:80',
        "jenkins": 'product:"Jenkins"',
        "ftp": 'port:21 product:"FTP"',
        "rsync": 'port:873 "rsyncd"',
        "grafana": 'product:"Grafana"',
        "prometheus": 'product:"Prometheus"',
        "minio": 'product:"MinIO"',
        "swagger": '"swagger" "openapi"',
        "mongoexpress": '"Mongo Express" http.title:"Mongo Express"',
        "keys": '\"AWS Access Key\"',
        "javascript": 'http.component:"JavaScript"',
        "github": '\"github.com\"',
        "amazonbuckets": '\"s3.amazonaws.com\"',
        "amazons3be": 'port:443 \"s3\"',
        "amazonbe": 'port:80 \"bucket\"',
        "nexus": '"Nexus Repository Manager"',
        "artifactory": '"Artifactory"',
        "registry": 'http.title:"Docker Registry"',
        "harbor": 'http.title:"Harbor"',
        "dockerapi": '"docker-distribution-api"',
    }

    context = {
        "types": types,
        "filters": filters,
        "facets": facets,
        "helper_error": helper_error,
        "base_queries_json": json.dumps(base_queries),
        "base_queries_zoomeye_json": json.dumps(queries_zoomeye),
        "zoomeye_filters": ["country", "subdivisions", "city", "product", "service", "device", "os", "port"],
        "zoomeye_facets": ["country", "subdivisions", "city", "product", "service", "device", "os", "port"],
    }
    return render(request, "custom_queries.html", context)


def custom_query_list(request):
    payload = []
    for q in CustomQuery.objects.all().order_by("-updated_on"):
        payload.append({
            "id": q.id,
            "name": q.name,
            "description": q.description,
            "query_template": q.query_template,
            "default_type": q.default_type,
            "active_probe_default": q.active_probe_default,
            "updated_on": q.updated_on.isoformat() if q.updated_on else "",
            "last_status": q.last_status,
            "provider": q.provider,
        })
    return JsonResponse({"results": payload})


def custom_query_save(request):
    qid = request.GET.get("id")
    name = (request.GET.get("name") or "").strip() or "Untitled"
    template = (request.GET.get("query_template") or "").strip()
    if not template:
        return JsonResponse({"error": "Query template is required"}, status=400)
    description = request.GET.get("description", "")
    default_type = request.GET.get("default_type", "")
    active_probe_default = _flag(request.GET.get("active_probe_default", False))
    provider = (request.GET.get("provider") or "shodan").lower()
    if provider not in ("shodan", "zoomeye"):
        provider = "shodan"
    if qid:
        q = CustomQuery.objects.filter(id=qid).first()
        if not q:
            return JsonResponse({"error": "Query not found"}, status=404)
    else:
        q = CustomQuery()
    q.name = name
    q.description = description
    q.query_template = template
    q.default_type = default_type
    q.active_probe_default = active_probe_default
    q.provider = provider
    q.save()
    return JsonResponse({"result": "ok", "id": q.id})


def custom_query_delete(request):
    qid = request.GET.get("id")
    if not qid:
        return JsonResponse({"error": "id required"}, status=400)
    CustomQuery.objects.filter(id=qid).delete()
    return JsonResponse({"result": "ok"})


def _render_custom_query(q: CustomQuery, keyword=None, country=None, network=None, exclude=None):
    tmpl = q.query_template or ""
    replacements = {
        "keyword": keyword or "",
        "country": country or "",
        "network": network or "",
        "exclude": exclude or "",
    }
    try:
        return tmpl.format(**replacements)
    except Exception:
        # fallback simple replace
        for k, v in replacements.items():
            tmpl = tmpl.replace("{" + k + "}", v)
        return tmpl


def custom_query_run(request):
    qid = request.GET.get("id")
    q = CustomQuery.objects.filter(id=qid).first()
    if not q:
        return JsonResponse({"error": "Query not found"}, status=404)
    keyword = request.GET.get("keyword", "")
    country = request.GET.get("country", "")
    network = request.GET.get("network", "")
    exclude = request.GET.get("exclude", "")
    page = request.GET.get("page") or "1"
    page_count = request.GET.get("page_count") or "1"
    active_probe = _flag(request.GET.get("active_probe", q.active_probe_default))
    provider = (request.GET.get("provider") or q.provider or "shodan").lower()
    if provider not in ("shodan", "zoomeye"):
        provider = "shodan"

    q.last_run_at = timezone.now()
    q.last_status = "queued"
    q.save(update_fields=["last_run_at", "last_status"])

    task_res = tasks.check_custom_query.delay(
        custom_query_id=q.id,
        keyword=keyword,
        country=country,
        network=network,
        exclude=exclude,
        page=page,
        page_count=page_count,
        active_probe=active_probe,
        provider=provider,
    )
    return JsonResponse({"task_id": task_res.task_id})


def _model_by_type(type_name):
    model_map = {
        "angular": Angular,
        "amazons3be": Amazons3be,
        "amazonbe": Amazonbe,
        "amazonbuckets": AmazonBuckets,
        "gitlab": Gitlab,
        "elastic": Elastic,
        "dirs": Dirs,
        "jenkins": Jenkins,
        "mongo": Mongo,
        "rsync": Rsync,
        "sonarqube": Sonarqube,
        "couchdb": Couchdb,
        "kibana": Kibana,
        "cassandra": Cassandra,
        "rethink": Rethink,
        "ftp": Ftp,
        "opensearch": OpenSearch,
        "grafana": Grafana,
        "prometheus": Prometheus,
        "minio": Minio,
        "swagger": Swagger,
        "mongoexpress": MongoExpress,
        "keys": Keys,
        "javascript": Javascript,
        "github": Github,
        "nexus": Nexus,
        "artifactory": Artifactory,
        "registry": Registry,
        "harbor": Registry,
        "dockerapi": Registry,
        "etcd": Etcd,
        "consul": ConsulKV,
        "rabbitmq": Rabbitmq,
        "solr": Solr,
        "gitea": Gitea,
        "gogs": Gogs,
        "azureblob": AzureBlob,
        "gcsbucket": GcsBucket,
    }
    return model_map.get((type_name or "").lower())


def _preview_types():
    return sorted({
        "angular",
        "amazons3be",
        "amazonbe",
        "amazonbuckets",
        "gitlab",
        "elastic",
        "dirs",
        "jenkins",
        "mongo",
        "rsync",
        "sonarqube",
        "couchdb",
        "kibana",
        "cassandra",
        "rethink",
        "ftp",
        "opensearch",
        "grafana",
        "prometheus",
        "minio",
        "swagger",
        "mongoexpress",
        "keys",
        "javascript",
        "github",
        "nexus",
        "artifactory",
        "registry",
        "harbor",
        "dockerapi",
        "etcd",
        "consul",
        "rabbitmq",
        "solr",
        "gitea",
        "gogs",
        "azureblob",
        "gcsbucket",
    })


def preview_page(request):
    return render(request, "preview.html", {"types": _preview_types()})

def explore_page(request):
    return render(request, "explore.html", {"types": _preview_types()})


def preview_targets(request):
    type_name = (request.GET.get("type") or "").lower()
    Model = _model_by_type(type_name)
    if not Model:
        return JsonResponse({"results": []})
    rows = []
    for obj in Model.objects.all()[:200]:
        ip = getattr(obj, "ip", "")
        port = getattr(obj, "port", "")
        label = ip
        if port:
            label = f"{ip}:{port}"
        rows.append({"id": obj.id, "ip": ip, "port": port, "label": label})
    return JsonResponse({"results": rows})


def preview_start(request):
    type_name = request.GET.get("type")
    target_id = request.GET.get("target_id")
    active_probe = _flag(request.GET.get("active_probe", False))
    Model = _model_by_type(type_name)
    if not Model or not target_id:
        return JsonResponse({"error": "invalid"}, status=400)
    obj = Model.objects.filter(id=target_id).first()
    if not obj:
        return JsonResponse({"error": "not found"}, status=404)
    task_res = tasks.run_preview.delay(type_name, target_id, active_probe=active_probe)
    return JsonResponse({"task_id": task_res.task_id})


def export_start(request):
    type_name = request.GET.get("type")
    target_id = request.GET.get("target_id")
    active_probe = _flag(request.GET.get("active_probe", False))
    if not active_probe:
        return JsonResponse({"error": "Active probing required for export"}, status=400)
    selection = {
        "index": request.GET.get("index") or request.GET.get("db"),
        "db": request.GET.get("db"),
        "collection": request.GET.get("collection"),
        "repo": request.GET.get("repo"),
        "bucket": request.GET.get("bucket"),
        "keyspace": request.GET.get("keyspace"),
        "table": request.GET.get("table"),
        "filter": request.GET.get("filter"),
        "max_repos": request.GET.get("max_repos"),
        "max_tags": request.GET.get("max_tags"),
    }
    max_items = request.GET.get("max_items", 1000)
    fmt = request.GET.get("format", "json")
    task_res = tasks.export_preview_data.delay(
        type=type_name,
        target_id=target_id,
        selection=selection,
        max_items=max_items,
        fmt=fmt,
        active_probe=active_probe,
    )
    return JsonResponse({"task_id": task_res.task_id})


def explore_start(request):
    type_name = request.GET.get("type", "").lower()
    target_id = request.GET.get("target_id")
    active_probe = _flag(request.GET.get("active_probe", False))
    if not active_probe:
        return JsonResponse({"error": "Active probing required"}, status=400)
    Model = _model_by_type(type_name)
    if not Model or not target_id:
        return JsonResponse({"error": "invalid"}, status=400)
    obj = Model.objects.filter(id=target_id).first()
    if not obj:
        return JsonResponse({"error": "not found"}, status=404)

    def _split(val):
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        return []

    export_options = {}
    if type_name in ("elastic", "opensearch"):
        export_options["indices"] = _split(getattr(obj, "indices", ""))
    elif type_name in ("mongo", "mongoexpress"):
        export_options["dbs"] = _split(getattr(obj, "databases", ""))
    elif type_name == "couchdb":
        export_options["dbs"] = []
    elif type_name in ("registry", "harbor", "dockerapi"):
        export_options["repos"] = _split(getattr(obj, "repos", ""))
    elif type_name == "nexus":
        export_options["repos"] = _split(getattr(obj, "repos", ""))
    elif type_name == "artifactory":
        export_options["repos"] = _split(getattr(obj, "repos", ""))
    elif type_name in ("amazons3be", "amazonbuckets"):
        bucket = getattr(obj, "bucket", None) or getattr(obj, "buckets", None)
        export_options["buckets"] = _split(bucket)
    elif type_name == "amazonbe":
        export_options["buckets"] = _split(getattr(obj, "buckets", ""))
    elif type_name == "dirs":
        export_options["dirs"] = _split(getattr(obj, "dirs", ""))
    elif type_name == "jenkins":
        export_options["jobs"] = _split(getattr(obj, "jobs", ""))
    elif type_name == "ftp":
        export_options["files"] = _split(getattr(obj, "files", ""))
    elif type_name == "rsync":
        export_options["shares"] = _split(getattr(obj, "shares", ""))
    elif type_name == "cassandra":
        export_options["keyspaces"] = _split(getattr(obj, "keyspaces", ""))
        export_options["tables"] = []
    elif type_name == "rethink":
        export_options["dbs"] = _split(getattr(obj, "databases", ""))
    elif type_name == "minio":
        export_options["buckets"] = _split(getattr(obj, "buckets", ""))
    elif type_name == "javascript":
        export_options["secrets"] = _split(getattr(obj, "secrets", ""))
    elif type_name == "github":
        export_options["secrets"] = _split(getattr(obj, "secret", ""))
    elif type_name == "etcd":
        export_options["keys"] = _split(getattr(obj, "keys_sample", ""))
    elif type_name == "consul":
        export_options["keys"] = _split(getattr(obj, "kv_roots", ""))
    elif type_name == "rabbitmq":
        export_options["vhosts"] = _split(getattr(obj, "vhosts", ""))
    elif type_name == "solr":
        export_options["cores"] = _split(getattr(obj, "cores", ""))
    elif type_name in ("gitea", "gogs"):
        export_options["repos"] = _split(getattr(obj, "repos", ""))
    elif type_name == "azureblob":
        export_options["containers"] = _split(getattr(obj, "containers", ""))
    elif type_name == "gcsbucket":
        export_options["objects"] = _split(getattr(obj, "objects_list", ""))

    return JsonResponse({"type": type_name, "target_id": target_id, "export_options": export_options})


def explore_next(request):
    type_name = request.GET.get("type", "").lower()
    target_id = request.GET.get("target_id")
    active_probe = _flag(request.GET.get("active_probe", False))
    if not active_probe:
        return JsonResponse({"error": "Active probing required"}, status=400)
    Model = _model_by_type(type_name)
    if not Model or not target_id:
        return JsonResponse({"error": "invalid"}, status=400)
    obj = Model.objects.filter(id=target_id).first()
    if not obj:
        return JsonResponse({"error": "not found"}, status=404)

    def _split(val):
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        return []

    selection = {
        "index": request.GET.get("index") or request.GET.get("db"),
        "db": request.GET.get("db"),
        "collection": request.GET.get("collection"),
        "repo": request.GET.get("repo"),
        "bucket": request.GET.get("bucket"),
        "filter": request.GET.get("filter", ""),
        "keyspace": request.GET.get("keyspace"),
        "max_items": request.GET.get("max_items"),
    }
    filter_text = (selection.get("filter") or "").lower()
    items = []

    def _clean_name(val):
        if not isinstance(val, str):
            return str(val or "").strip()
        return val.strip().strip("'\"/ ")

    if type_name in ("elastic", "opensearch"):
        indices = _split(getattr(obj, "indices", ""))
        if selection.get("index"):
            base = _http_base(obj.ip, obj.port, scheme="http")
            target_index = _clean_name(selection.get("index"))
            try:
                max_items = int(selection.get("max_items") or 20)
            except Exception:
                max_items = 20
            max_items = max(1, min(max_items, 200))
            try:
                sr = requests.get(f"{base}/{target_index}/_search?size={max_items}", timeout=8)
                if sr.ok:
                    hits = sr.json().get("hits", {}).get("hits", [])
                    items = [h.get("_source", h) for h in hits]
                elif sr.status_code in (401, 403):
                    items = [{"error": "auth required"}]
                else:
                    items = [{"error": f"HTTP {sr.status_code}"}]
            except Exception as exc:
                items = [{"error": str(exc)}]
        else:
            if filter_text:
                indices = [i for i in indices if filter_text in i.lower()]
            items = indices
    elif type_name in ("mongo", "mongoexpress"):
        if selection.get("db"):
            try:
                client = MongoClient(f"mongodb://{obj.ip}:{obj.port}/", serverSelectionTimeoutMS=4000)
                colls = client[selection.get("db")].list_collection_names()
                if filter_text:
                    colls = [c for c in colls if filter_text in c.lower()]
                items = colls
            except Exception as exc:
                items = [{"error": str(exc)}]
        else:
            dbs = _split(getattr(obj, "databases", ""))
            if filter_text:
                dbs = [d for d in dbs if filter_text in d.lower()]
            items = dbs
    elif type_name in ("registry", "harbor", "dockerapi"):
        repos = _split(getattr(obj, "repos", ""))
        if filter_text:
            repos = [r for r in repos if filter_text in r.lower()]
        items = repos
    elif type_name in ("nexus", "artifactory"):
        repos = _split(getattr(obj, "repos", ""))
        if filter_text:
            repos = [r for r in repos if filter_text in r.lower()]
        items = repos
    elif type_name in ("amazons3be", "amazonbuckets", "amazonbe", "minio"):
        buckets = _split(getattr(obj, "buckets", "") or getattr(obj, "bucket", ""))
        if filter_text:
            buckets = [b for b in buckets if filter_text in b.lower()]
        items = buckets
    elif type_name == "dirs":
        dirs = _split(getattr(obj, "dirs", ""))
        if filter_text:
            dirs = [d for d in dirs if filter_text in d.lower()]
        items = dirs
    elif type_name == "jenkins":
        jobs = _split(getattr(obj, "jobs", ""))
        if filter_text:
            jobs = [j for j in jobs if filter_text in j.lower()]
        items = jobs
    elif type_name == "ftp":
        files = _split(getattr(obj, "files", ""))
        if filter_text:
            files = [f for f in files if filter_text in f.lower()]
        items = files
    elif type_name == "rsync":
        shares = _split(getattr(obj, "shares", ""))
        if filter_text:
            shares = [s for s in shares if filter_text in s.lower()]
        items = shares
    elif type_name == "cassandra":
        keyspaces = _split(getattr(obj, "keyspaces", ""))
        if filter_text:
            keyspaces = [k for k in keyspaces if filter_text in k.lower()]
        items = keyspaces
    elif type_name == "rethink":
        dbs = _split(getattr(obj, "databases", ""))
        if filter_text:
            dbs = [d for d in dbs if filter_text in d.lower()]
        items = dbs
    elif type_name in ("javascript", "github"):
        secrets = _split(getattr(obj, "secrets", "") or getattr(obj, "secret", ""))
        if filter_text:
            secrets = [s for s in secrets if filter_text in s.lower()]
        items = secrets
    elif type_name == "solr":
        cores = _split(getattr(obj, "cores", ""))
        if filter_text:
            cores = [c for c in cores if filter_text in str(c).lower()]
        items = cores
    elif type_name in ("gitea", "gogs"):
        repos = _split(getattr(obj, "repos", ""))
        if filter_text:
            repos = [r for r in repos if filter_text in str(r).lower()]
        items = repos
    elif type_name == "azureblob":
        containers = _split(getattr(obj, "containers", ""))
        if filter_text:
            containers = [c for c in containers if filter_text in str(c).lower()]
        items = containers
    elif type_name == "gcsbucket":
        objects = _split(getattr(obj, "objects_list", ""))
        if filter_text:
            objects = [o for o in objects if filter_text in str(o).lower()]
        items = objects
    elif type_name == "etcd":
        try:
            base = _http_base(obj.ip, obj.port, scheme=getattr(obj, "scheme", "http") or "http")
            prefix = _clean_name(selection.get("index") or "")
            max_items = int(selection.get("max_items") or 20)
            max_items = max(1, min(max_items, 100))
            resp = requests.get(f"{base}/v2/keys/{prefix}?recursive=true&sorted=true&limit={max_items}", timeout=8, verify=False)
            if resp.status_code == 404:
                payload = {"key": "", "range_end": "", "limit": max_items}
                v3 = requests.post(f"{base}/v3/kv/range", json=payload, timeout=8, verify=False)
                if v3.ok:
                    data = []
                    for kvs in v3.json().get("kvs", []) or []:
                        k = kvs.get("key")
                        if k:
                            data.append(base64.b64decode(k).decode(errors="ignore"))
                    items = data
                elif v3.status_code in (401, 403):
                    items = [{"error": "auth required"}]
            elif resp.status_code in (401, 403):
                items = [{"error": "auth required"}]
            elif resp.ok:
                data = []
                try:
                    def _collect(node):
                        if isinstance(node, dict):
                            key = node.get("key")
                            if key:
                                data.append(key)
                            for child in node.get("nodes", []) or []:
                                _collect(child)
                    _collect(resp.json().get("node", {}))
                    items = data[:max_items]
                except Exception:
                    items = []
        except Exception as exc:
            items = [{"error": str(exc)}]
    elif type_name == "consul":
        try:
            base = _http_base(obj.ip, obj.port, scheme=getattr(obj, "scheme", "http") or "http")
            prefix = _clean_name(selection.get("index") or "")
            max_items = int(selection.get("max_items") or 20)
            max_items = max(1, min(max_items, 100))
            keys_url = f"{base}/v1/kv/{prefix}?keys&recurse&limit={max_items}" if prefix else f"{base}/v1/kv/?keys&limit={max_items}"
            resp = requests.get(keys_url, timeout=8, verify=False)
            if resp.status_code in (401, 403):
                items = [{"error": "auth required"}]
            elif resp.ok and isinstance(resp.json(), list):
                data = resp.json()
                if filter_text:
                    data = [k for k in data if filter_text in k.lower()]
                items = data[:max_items]
        except Exception as exc:
            items = [{"error": str(exc)}]
    elif type_name == "solr":
        cores = _split(getattr(obj, "cores", ""))
        if filter_text:
            cores = [c for c in cores if filter_text in str(c).lower()]
        items = cores
    elif type_name in ("gitea", "gogs"):
        repos = _split(getattr(obj, "repos", ""))
        if filter_text:
            repos = [r for r in repos if filter_text in str(r).lower()]
        items = repos
    elif type_name == "rabbitmq":
        try:
            base = _http_base(obj.ip, obj.port, scheme=getattr(obj, "scheme", "http") or "http")
            qresp = requests.get(f"{base}/api/queues", timeout=8, verify=False)
            if qresp.status_code in (401, 403):
                items = [{"error": "auth required"}]
            elif qresp.ok and isinstance(qresp.json(), list):
                data = qresp.json()
                cap = max(1, min(int(selection.get("max_items") or 50), 200))
                filtered = []
                for q in data:
                    name = q.get("name") if isinstance(q, dict) else q
                    if filter_text and name and filter_text not in name.lower():
                        continue
                    filtered.append(name if isinstance(name, str) else q)
                    if len(filtered) >= cap:
                        break
                items = filtered
        except Exception as exc:
            items = [{"error": str(exc)}]

    return JsonResponse({"type": type_name, "target_id": target_id, "items": items})


def export_download(request, token: str):
    export_dir = Path("/tmp/leakscope_exports")
    matches = list(export_dir.glob(f"{token}.*"))
    if not matches:
        return HttpResponse("Not found", status=404)
    file_path = matches[0]
    content = file_path.read_bytes()
    file_path.unlink(missing_ok=True)
    response = HttpResponse(content, content_type="application/octet-stream")
    response['Content-Disposition'] = f'attachment; filename=\"{file_path.name}\"'
    return response


def export_results(request):
    type_name = request.GET.get("type", "").lower()
    fmt = request.GET.get("format", "csv").lower()
    Model = _model_by_type(type_name)
    if not Model:
        return HttpResponse(json.dumps({"error": "invalid type"}), content_type="application/json", status=400)
    rows = list(Model.objects.all())
    field_names = [f.name for f in Model._meta.fields if f.name not in {"id"}]
    if fmt == "json":
        payload = []
        for obj in rows:
            payload.append({f: getattr(obj, f, "") for f in field_names})
        return HttpResponse(json.dumps(payload, default=str), content_type="application/json")
    # default csv
    import csv
    from io import StringIO
    sio = StringIO()
    writer = csv.writer(sio)
    writer.writerow(field_names)
    for obj in rows:
        writer.writerow([getattr(obj, f, "") for f in field_names])
    resp = HttpResponse(sio.getvalue(), content_type="text/csv")
    resp['Content-Disposition'] = f'attachment; filename="{type_name}.csv"'
    return resp


def export_import(request):
    selected_type = request.GET.get("type") or (types[0] if types else "")
    Model = _model_by_type(selected_type)
    display_fields = []
    table_rows = []
    if Model:
        display_fields = [f.name for f in Model._meta.fields if f.name not in {"id"}]
        for obj in Model.objects.all()[:500]:
            table_rows.append([getattr(obj, f, "") for f in display_fields])

    import_status = None
    if request.method == "POST":
        raw = request.POST.get("import_blacklist", "")
        entries = [line.strip() for line in raw.splitlines() if line.strip()]
        created = 0
        for val in entries:
            _, was_created = BlacklistEntry.objects.get_or_create(value=val)
            if was_created:
                created += 1
        import_status = f"Imported {created} new blacklist entries."

    context = {
        "types": types,
        "selected_type": selected_type,
        "display_fields": display_fields,
        "table_rows": table_rows,
        "import_status": import_status,
    }
    return render(request, "export_import.html", context)
