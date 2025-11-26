from celery import shared_task
import logging
import json
import os
import requests
import celery
from git import Repo
import tempfile
from celery.exceptions import Ignore
import celery_progress
from hurry.filesize import size
from bs4 import BeautifulSoup
from celery import shared_task, current_task
from celery_progress.backend import ProgressRecorder
import smtplib
import ast
import email.message
import math
import re
import jsbeautifier
from gzip import GzipFile
import ssl
from typing import Any, Dict, Optional, Tuple

try:
    from StringIO import StringIO
    readBytesCustom = StringIO
except ImportError:
    from io import BytesIO
    readBytesCustom = BytesIO

from urllib.request import Request, urlopen

import hashlib
from urllib.parse import urlparse
import jxmlease



from leaklooker_app.models import Monitor, Search, Rethink, Cassandra, Gitlab, Elastic, Dirs, Jenkins, Mongo, Rsync, \
    Sonarqube, Couchdb, Kibana, Ftp, Amazonbe, AmazonBuckets, Keys, Github, Amazons3be, Angular, Javascript, \
    OpenSearch, Grafana, Prometheus, Minio, Swagger, MongoExpress
from leaklooker.shodan_client import ShodanClient, ShodanClientError

broker_url = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
app = celery.Celery('leaklooker', broker=broker_url, backend=broker_url)
logger = logging.getLogger(__name__)

def _env_list(name: str):
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_config():
    cfg = {
        "config": {
            "SHODAN_API_KEY": os.environ.get("SHODAN_API_KEY", ""),
            "monitoring": {
                "gmail_email": os.environ.get("GMAIL_EMAIL", ""),
                "gmail_password": os.environ.get("GMAIL_PASSWORD", ""),
            },
            "blacklist": _env_list("BLACKLIST"),
        }
    }

    # Optionally merge config.json if it exists as a file.
    try:
        if os.path.isfile("config.json"):
            with open("config.json", "r") as config_file:
                file_cfg = json.load(config_file)
                if isinstance(file_cfg, dict):
                    file_section = file_cfg.get("config", {})
                    cfg["config"]["blacklist"].extend(file_section.get("blacklist", []))
                    monitor_section = file_section.get("monitoring", {})
                    if monitor_section:
                        cfg["config"]["monitoring"].update(monitor_section)
                    if file_section.get("SHODAN_API_KEY"):
                        cfg["config"]["SHODAN_API_KEY"] = file_section.get("SHODAN_API_KEY")
    except Exception:
        logger.exception("Failed to read config.json; using environment configuration only")

    cfg["config"]["blacklist"] = list({item for item in cfg["config"]["blacklist"]})
    return cfg


def is_blacklisted(value: str, config: Dict[str, Any]) -> bool:
    return value in config.get("config", {}).get("blacklist", [])

regex_str = r"""
  (?:"|')                               # Start newline delimiter
  (
    ((?:[a-zA-Z]{1,10}://|//)           # Match a scheme [a-Z]*1-10 or //
    [^"'/]{1,}\.                        # Match a domainname (any character + dot)
    [a-zA-Z]{2,}[^"']{0,})              # The domainextension and/or path
    |
    ((?:/|\.\./|\./)                    # Start with /,../,./
    [^"'><,;| *()(%%$^/\\\[\]]          # Next character can't be...
    [^"'><,;|()]{1,})                   # Rest of the characters can't be
    |
    ([a-zA-Z0-9_\-/]{1,}/               # Relative endpoint with /
    [a-zA-Z0-9_\-/]{1,}                 # Resource name
    \.(?:[a-zA-Z]{1,4}|action)          # Rest + extension (length 1-4 or action)
    (?:[\?|#][^"|']{0,}|))              # ? or # mark with parameters
    |
    ([a-zA-Z0-9_\-/]{1,}/               # REST API (no extension) with /
    [a-zA-Z0-9_\-/]{3,}                 # Proper REST endpoints usually have 3+ chars
    (?:[\?|#][^"|']{0,}|))              # ? or # mark with parameters
    |
    AIza[0-9A-Za-z-_]{35}
    |
    (xox[p|b|o|a]-[0-9]{12}-[0-9]{12}-[0-9]{12}-[a-z0-9]{32})
    |
    AKIA[0-9A-Z]{16}
    |
    amzn\.mws\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}
    |
    EAACEdEose0cBA[0-9A-Za-z]+
    |
    [f|F][a|A][c|C][e|E][b|B][o|O][o|O][k|K].*['|"][0-9a-f]{32}['|"]
    |
    [g|G][i|I][t|T][h|H][u|U][b|B].*['|"][0-9a-zA-Z]{35,40}['|"]
    |
    [a|A][p|P][i|I][_]?[k|K][e|E][y|Y].*['|"][0-9a-zA-Z]{32,45}['|"]
    |
    [s|S][e|E][c|C][r|R][e|E][t|T].*['|"][0-9a-zA-Z]{32,45}['|"]
    |
    [0-9]+-[0-9A-Za-z_]{32}\.apps\.googleusercontent\.com
    |
    ya29\.[0-9A-Za-z\-_]+
    |
    [h|H][e|E][r|R][o|O][k|K][u|U].*[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}
    |
    [a-zA-Z]{3,10}://[^/\s:@]{3,20}:[^/\s:@]{3,20}@.{1,100}["'\s]
    |
    sk_live_[0-9a-zA-Z]{24}
    |
    https://hooks.slack.com/services/T[a-zA-Z0-9_]{8}/B[a-zA-Z0-9_]{8}/[a-zA-Z0-9_]{24}
    |
    [t|T][w|W][i|I][t|T][t|T][e|E][r|R].*[1-9][0-9]+-[0-9a-zA-Z]{40}
    |
    ([a-zA-Z0-9_\-]{1,}                 # filename
    \.(?:php|asp|aspx|jsp|json|
         action|html|js|txt|xml)        # . + extension
    (?:[\?|#][^"|']{0,}|))              # ? or # mark with parameters
  )
  (?:"|')                               # End newline delimiter
"""

context_delimiter_str = "\n"

queries = {
    "gitlab": 'http.title:"GitLab"',
    "elastic": 'product:"Elastic" port:9200',
    "dirs": 'http.title:"Index of /" http.status:200',
    "jenkins": 'http.title:"Dashboard [Jenkins]"',
    "mongo": 'product:"MongoDB"',
    "rsync": 'port:873 "@RSYNCD"',
    "sonarqube": 'http.title:"SonarQube"',
    "couchdb": 'product:"CouchDB"',
    "kibana": 'product:"Kibana"',
    "cassandra": 'product:"Cassandra" port:9042',
    "rethink": 'product:"RethinkDB"',
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
    "mongoexpress": 'http.title:"Mongo Express"'
}

buckets_all = [
    "s3.ap-southeast-1.amazonaws.com",
    "s3.ap-southeast-2.amazonaws.com",
    "s3-eu-west-1.amazonaws.com",
    "s3-eu-west-2.amazonaws.com",
    "s3-us-west-2.amazonaws.com",
    "s3-us-west-1.amazonaws.com"
]
keys_all = ['api_key','stripe','secret_key','google_api_key']
BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
HEX_CHARS = "1234567890abcdefABCDEF"

exclude = ['.png','.jpg', '.sum', "yarn.lock", "package-lock.json", ".svg", ".css"]

with open ("buckets.txt","r") as f:
    buckets_bruteforce = f.readlines()

def get_shodan_client() -> ShodanClient:
    config = get_config()
    api_key = config['config'].get('SHODAN_API_KEY', '')
    return ShodanClient(api_key)


def build_shodan_query(base_query: str, keyword: Optional[str] = None, country: Optional[str] = None,
                       network: Optional[str] = None, exclude: Optional[str] = None) -> str:
    parts = [base_query] if base_query else []

    if keyword:
        keyword = keyword.strip()
        if keyword:
            if ' ' in keyword:
                parts.append(f'"{keyword}"')
            else:
                parts.append(keyword)

    if country:
        parts.append(f"country:{country}")

    if network:
        network = network.strip()
        if network:
            if '/' in network:
                parts.append(f"net:{network}")
            else:
                parts.append(f"ip:{network}")

    if exclude:
        tokens = [t.strip() for t in exclude.split(',') if t.strip()]
        for token in tokens:
            if token.startswith('-'):
                parts.append(token)
            elif ' ' in token:
                parts.append(f'-"{token}"')
            else:
                parts.append(f"-{token}")

    return " ".join(parts).strip()


def infer_scheme(match: Dict[str, Any]) -> str:
    port = match.get('port')
    if match.get('ssl') or port in (443, 8443, 9443):
        return 'https'
    return 'http'


def build_service_url(match: Dict[str, Any], path: str = "") -> str:
    scheme = infer_scheme(match)
    ip = match.get('ip_str') or str(match.get('ip'))
    port = match.get('port')
    default_port = 443 if scheme == 'https' else 80
    if port == default_port or port is None:
        base = f"{scheme}://{ip}"
    else:
        base = f"{scheme}://{ip}:{port}"
    if path and not path.startswith('/'):
        path = '/' + path
    return f"{base}{path}"


def get_http_html(match: Dict[str, Any]) -> str:
    http_section = match.get('http') or {}
    html = http_section.get('html') or http_section.get('body')
    if isinstance(html, dict):
        html = html.get('data')
    if html:
        return html
    data = match.get('data')
    if isinstance(data, str) and data.strip().startswith('<'):
        return data
    return ''


def parse_json_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except ValueError:
            return None
    return None


def extract_ip_port(match: Dict[str, Any]) -> Tuple[str, Optional[int]]:
    ip = match.get('ip_str') or str(match.get('ip'))
    port = match.get('port')
    return ip, port

def check_credits():
    try:
        client = get_shodan_client()
        info = client.info()
        return info.get('query_credits', 0)
    except ShodanClientError as exc:
        logger.error("Unable to fetch Shodan credits: %s", exc)
    except Exception as exc:  # pragma: no cover - defensive catch
        logger.exception("Unexpected error while fetching Shodan credits")
    return 0


def stats(type=None):
    if not type:
        return 0

    query = queries.get(type)
    if not query:
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


@shared_task(bind=True)
def check_main(self, fk, keyword=None, country=None, network=None, page=None, type=None, exclude=None):
    config = get_config()
    search = Search.objects.get(id=fk)
    progress_recorder = ProgressRecorder(self)
    results = {}

    type_lower = (type or "").lower()

    if not keyword:
        keyword_for_query = None
    else:
        keyword_for_query = keyword

    base_query = queries.get(type_lower, "")
    if not base_query and type_lower not in keys_all:
        error_msg = f"Unsupported search type: {type}"
        logger.error(error_msg)
        self.update_state(state="FAILURE", meta={"error": error_msg})
        raise Ignore()

    shodan_query = build_shodan_query(base_query, keyword=keyword_for_query, country=country, network=network,
                                      exclude=exclude)
    try:
        page_num = int(page) if page else 1
    except ValueError:
        page_num = 1

    try:
        client = get_shodan_client()
        response = client.host_search(shodan_query, page=page_num)
    except ShodanClientError as exc:
        logger.error("Shodan search failed for %s: %s", type, exc)
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise Ignore()
    except Exception as exc:  # pragma: no cover - defensive catch
        logger.exception("Unexpected error during Shodan search for %s", type)
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise Ignore()

    matches = response.get('matches', [])
    total = len(matches)
    events = response.get('total', total)

    if total == 0:
        self.update_state(state="SUCCESS",
                          meta={"type": type_lower or 'unknown', "total": 0, 'events': events, 'results': results})
        raise Ignore()

    default_total = total if total > 0 else 1
    result_type = 'keys' if type_lower in keys_all else type_lower

    for index, match in enumerate(matches):
        try:
            if type_lower in keys_all:
                result_payload = check_keys(index, match, search, config=config)
                result_type = 'keys'
            elif type_lower in ('asia', 'europe', 'north america'):
                result_payload = check_amazonbe(index, match, search, config=config)
                result_type = type_lower
            else:
                result_type = type_lower
                if type_lower == 'gitlab':
                    result_payload = check_gitlab(index, match, search, config=config)
                elif type_lower == 'elastic':
                    result_payload = check_elastic(index, match, search, keyword=keyword, config=config)
                elif type_lower == 'angular':
                    result_payload = check_angular(index, match, search, keyword=keyword, config=config)
                elif type_lower == 'amazons3be':
                    result_payload = check_amazons3be(index, match, search, keyword=keyword, config=config)
                elif type_lower == 'dirs':
                    result_payload = check_dir(index, match, search, keyword=keyword, config=config)
                elif type_lower == 'jenkins':
                    result_payload = check_jenkins(index, match, search, keyword=keyword, config=config)
                elif type_lower == 'mongo':
                    result_payload = check_mongo(index, match, search, keyword=keyword, config=config)
                elif type_lower == 'rsync':
                    result_payload = check_rsync(index, match, search, config=config)
                elif type_lower == 'ftp':
                    result_payload = check_ftp(index, match, search, keyword=keyword, config=config)
                elif type_lower == 'sonarqube':
                    result_payload = check_sonarqube(index, match, search, config=config)
                elif type_lower == 'couchdb':
                    result_payload = check_couchdb(index, match, search, config=config)
                elif type_lower == 'kibana':
                    result_payload = check_kibana(index, match, search, config=config)
                elif type_lower == 'cassandra':
                    result_payload = check_cassandra(index, match, search, config=config)
                elif type_lower == 'rethink':
                    result_payload = check_rethink(index, match, search, config=config)
                elif type_lower == 'opensearch':
                    result_payload = check_opensearch(index, match, search, keyword=keyword, config=config)
                elif type_lower == 'grafana':
                    result_payload = check_grafana(index, match, search, config=config)
                elif type_lower == 'prometheus':
                    result_payload = check_prometheus(index, match, search, config=config)
                elif type_lower == 'minio':
                    result_payload = check_minio(index, match, search, config=config)
                elif type_lower == 'swagger':
                    result_payload = check_swagger(index, match, search, config=config)
                elif type_lower == 'mongoexpress':
                    result_payload = check_mongoexpress(index, match, search, config=config)
                else:
                    logger.warning("No handler implemented for type %s", type_lower)
                    result_payload = {}

            results[index] = result_payload
        except Exception as exc:  # pragma: no cover - ensure one failure doesn't break whole batch
            logger.exception("Failed to process %s result for IP %s", type_lower, match.get('ip_str'))
            results[index] = {}

        progress_recorder.set_progress(index + 1, total=default_total)

    self.update_state(state="SUCCESS",
                      meta={"type": result_type if matches else type_lower, "total": total,
                            'events': events, 'results': results})

    raise Ignore()

    if type.lower() == "mongo":
        for c, i in enumerate(req_json['events']):
            results_mongo = check_mongo(c, i, search, keyword=keyword, config=config)
            progress_recorder.set_progress(c + 1, total=total)
            results[c] = results_mongo

        self.update_state(state="SUCCESS",
                          meta={"type": type.lower(), "total": total, 'events': events, 'results': results})
        raise Ignore()
    if type.lower() == "rsync":
        for c, i in enumerate(req_json['events']):
            results_rsync = check_rsync(c, i, search, config=config)
            progress_recorder.set_progress(c + 1, total=total)
            results[c] = results_rsync

        self.update_state(state="SUCCESS",
                          meta={"type": type.lower(), "total": total, 'events': events, 'results': results})
        raise Ignore()

    if type.lower() == "ftp":
        for c, i in enumerate(req_json['events']):
            results_ftp = check_ftp(c, i, search, keyword=keyword, config=config)
            progress_recorder.set_progress(c + 1, total=total)
            results[c] = results_ftp

        self.update_state(state="SUCCESS",
                          meta={"type": type.lower(), "total": total, 'events': events, 'results': results})

        raise Ignore()

    if type.lower() == "sonarqube":
        for c, i in enumerate(req_json['events']):
            results_sonarqube = check_sonarqube(c, i, search, config=config)
            progress_recorder.set_progress(c + 1, total=total)
            results[c] = results_sonarqube

        self.update_state(state="SUCCESS",
                          meta={"type": type.lower(), "total": total, 'events': events, 'results': results})
        raise Ignore()

    if type.lower() == "couchdb":
        for c, i in enumerate(req_json['events']):
            results_couchdb = check_couchdb(c, i, search, config=config)
            progress_recorder.set_progress(c + 1, total=total)
            results[c] = results_couchdb

        self.update_state(state="SUCCESS",
                          meta={"type": type.lower(), "total": total, 'events': events, 'results': results})
        raise Ignore()

    if type.lower() == "kibana":
        for c, i in enumerate(req_json['events']):
            results_kibana = check_kibana(c, i, search, config=config)
            progress_recorder.set_progress(c + 1, total=total)
            results[c] = results_kibana

        self.update_state(state="SUCCESS",
                          meta={"type": type.lower(), "total": total, 'events': events, 'results': results})
        raise Ignore()

    if type.lower() == "cassandra":
        for c, i in enumerate(req_json['events']):
            results_cassandra = check_cassandra(c, i, search, config=config)
            progress_recorder.set_progress(c + 1, total=total)
            results[c] = results_cassandra

        self.update_state(state="SUCCESS",
                          meta={"type": type.lower(), "total": total, 'events': events, 'results': results})
        raise Ignore()

    if type.lower() == "rethink":
        for c, i in enumerate(req_json['events']):
            results_rethink = check_rethink(c, i, search, config=config)
            progress_recorder.set_progress(c + 1, total=total)
            results[c] = results_rethink

        self.update_state(state="SUCCESS",
                          meta={"type": type.lower(), "total": total, 'events': events, 'results': results})
        raise Ignore()


    return results


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
def monitor(types, keyword="", network=""):
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

    try:
        client = get_shodan_client()
    except ShodanClientError as exc:
        logger.error("Unable to initialise Shodan client for monitor: %s", exc)
        return {}

    config = get_config()
    query_hint = keyword or network or ""

    return_dict = {}

    for monitor_type in type_list:
        type_lower = monitor_type.lower()
        return_dict[type_lower] = {"keyword": query_hint, "results": {}}

        base_query = queries.get(type_lower, "")
        if not base_query and type_lower not in keys_all:
            logger.warning("Skipping unsupported monitor type %s", monitor_type)
            continue

        search = Search(type=monitor_type, keyword=keyword, network=network)
        search.save()

        shodan_query = build_shodan_query(base_query, keyword=keyword, network=network)
        try:
            response = client.host_search(shodan_query, page=1)
        except ShodanClientError as exc:
            logger.error("Shodan monitor query failed for %s: %s", monitor_type, exc)
            continue
        except Exception as exc:  # pragma: no cover
            logger.exception("Unexpected error during Shodan monitor query for %s", monitor_type)
            continue

        matches = response.get('matches', [])

        for c, match in enumerate(matches):
            if type_lower in keys_all:
                payload = check_keys(c, match, search, config=config)
            elif type_lower in ('asia', 'europe', 'north america'):
                payload = check_amazonbe(c, match, search, config=config)
            elif type_lower == 'gitlab':
                payload = check_gitlab(c, match, search, config=config)
            elif type_lower == 'elastic':
                payload = check_elastic(c, match, search, keyword=keyword, config=config)
            elif type_lower == 'angular':
                payload = check_angular(c, match, search, keyword=keyword, config=config)
            elif type_lower == 'amazons3be':
                payload = check_amazons3be(c, match, search, keyword=keyword, config=config)
            elif type_lower == 'dirs':
                payload = check_dir(c, match, search, keyword=keyword, config=config)
            elif type_lower == 'jenkins':
                payload = check_jenkins(c, match, search, keyword=keyword, config=config)
            elif type_lower == 'mongo':
                payload = check_mongo(c, match, search, keyword=keyword, config=config)
            elif type_lower == 'rsync':
                payload = check_rsync(c, match, search, config=config)
            elif type_lower == 'ftp':
                payload = check_ftp(c, match, search, keyword=keyword, config=config)
            elif type_lower == 'sonarqube':
                payload = check_sonarqube(c, match, search, config=config)
            elif type_lower == 'couchdb':
                payload = check_couchdb(c, match, search, config=config)
            elif type_lower == 'kibana':
                payload = check_kibana(c, match, search, config=config)
            elif type_lower == 'cassandra':
                payload = check_cassandra(c, match, search, config=config)
            elif type_lower == 'rethink':
                payload = check_rethink(c, match, search, config=config)
            else:
                payload = {}

            if payload:
                for key, value in payload.items():
                    if value:
                        return_dict[type_lower]['results'][key] = value

    if return_dict:
        send_mail(return_dict)

    return return_dict


## { 'gitlab': {'keyword":"fa"}, 0 : {'ip':1111.1.1}}

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
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(gmail_user, gmail_password)
        server.sendmail(msg['From'], [msg['To']], msg.as_string())
        server.close()
        logger.info('Monitoring email sent')
    except Exception as exc:
        logger.error('Failed to send monitoring email: %s', exc)

def check_amazons3be(c, match, search, keyword, config):
    return_dict = {}
    parser = jxmlease.Parser()

    ip, port = extract_ip_port(match)
    files: list = []
    indicators: list = []

    if Amazons3be.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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

    if Rethink.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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

    if databases:
        device = Rethink(search=search, ip=ip, port=port, databases=databases)
        device.save()
        return_dict[c] = {"ip": ip, "port": port, 'databases': databases}

    return return_dict


def check_cassandra(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    keyspaces = []

    if Cassandra.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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

    if keyspaces:
        device = Cassandra(search=search, ip=ip, port=port, keyspaces=keyspaces)
        device.save()
        return_dict[c] = {"ip": ip, "port": port, 'keyspaces': keyspaces}

    return return_dict


def check_ftp(c, match, search, keyword, config):
    return_dict = {}
    files: list = []
    indicator: list = []

    ip, port = extract_ip_port(match)

    if Ftp.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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

    if Kibana.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
        return return_dict

    device = Kibana(search=search, ip=ip, port=port)
    device.save()

    return_dict[c] = {"ip": ip, "port": port}

    return return_dict


def check_couchdb(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)

    if Couchdb.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
        return return_dict

    device = Couchdb(search=search, ip=ip, port=port)
    device.save()

    return_dict[c] = {"ip": ip, "port": port}

    return return_dict


def check_sonarqube(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)

    if Sonarqube.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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

    if Rsync.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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

    if Jenkins.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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

@app.task
def check_mongo(c, match, search, keyword, config):
    return_dict = {}
    dbs = []
    indicators = []
    bytes_size = 0

    ip, port = extract_ip_port(match)

    if Mongo.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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

    ip = keyword.split("/")
    if mode == 1:
        # Beautify
        if len(content) > 1000000:
            content = content.replace(";",";\r\n").replace(",",",\r\n")
            with open(ip[2] +".js",'w') as f:
                f.write(content)
        else:
            content = jsbeautifier.beautify(content)
            with open(ip[2] +".js",'w') as f:
                f.write(content)

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


@app.task
def check_dir(c, match, search, keyword, config):
    return_dict = {}
    indicators = []
    dirs = []

    ip, port = extract_ip_port(match)

    if Dirs.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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
    '''
    Send requests with Requests
    '''
    q = Request(url)

    q.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) \
        AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36')
    q.add_header('Accept', 'text/html,\
        application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8')
    q.add_header('Accept-Language', 'en-US,en;q=0.8')
    q.add_header('Accept-Encoding', 'gzip')

    try:
        sslcontext = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        response = urlopen(q, context=sslcontext)
    except:
        sslcontext = ssl.SSLContext(ssl.PROTOCOL_TLSv1)
        response = urlopen(q, context=sslcontext)

    if response.info().get('Content-Encoding') == 'gzip':
        data = GzipFile(fileobj=readBytesCustom(response.read())).read()
    elif response.info().get('Content-Encoding') == 'deflate':
        data = response.read().read()
    else:
        data = response.read()

    return data.decode('utf-8', 'replace')

@app.task
def check_angular(c, match, search, keyword, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    path = ""
    title = ""
    if Angular.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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


@app.task
def check_elastic(c, match, search, keyword, config):
    return_dict = {}
    indices_list = []
    indicators = []
    bytes_size = 0

    ip, port = extract_ip_port(match)

    if Elastic.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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

    # If we still have no indices/size, try a lightweight probe against the node.
    if not indices_list:
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

    device = Elastic(search=search, name=cluster_name, ip=ip, port=port, size=new_size, indices=indices_list,
                     indicator=indicators)
    device.save()
    return_dict[c] = {"name": cluster_name, "ip": ip, "port": port, 'size': new_size, "indice": indices_list}

    return return_dict


def check_opensearch(c, match, search, keyword, config):
    # Reuse elastic parsing patterns for OpenSearch
    return check_elastic(c, match, search, keyword, config)


def check_grafana(c, match, search, config):
    return_dict = {}
    ip, port = extract_ip_port(match)
    if Grafana.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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
    if Prometheus.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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
    if Minio.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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
    if Swagger.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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
    if MongoExpress.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
        return return_dict

    url = build_service_url(match)
    title = (match.get('http') or {}).get('title') or ""

    device = MongoExpress(search=search, ip=ip, port=port, url=url, title=title)
    device.save()
    return_dict[c] = {"url": url, "ip": ip, "port": port, "title": title}
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

    if Gitlab.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
        return return_dict

    url = build_service_url(match)
    device = Gitlab(search=search, url=url, ip=ip, port=port)
    device.save()

    return_dict[c] = {"url": url, "ip": ip, "port": port}

    return return_dict


def parse_bucket(link):
    parsed = urlparse(link)
    if parsed.hostname in buckets_all:
        path_splitted = parsed.path.split("/")
        return urlparse(link).hostname + "/" + path_splitted[1]
    else:
        return urlparse(link).hostname

def check_keys(c, match, search, config):
    return_dict = {}

    ip, port = extract_ip_port(match)
    title = (match.get('http') or {}).get('title', '')

    if Keys.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
        return return_dict

    device = Keys(search=search, ip=ip, port=port, title=title)
    device.save()

    return_dict[c] = {"ip": ip, "port": port, 'title': title}

    return return_dict

def shannon_entropy(data, iterator):
    """
    Borrowed from http://blog.dkbza.org/2007/05/scanning-data-for-entropy-anomalies.html
    """
    if not data:
        return 0
    entropy = 0
    for x in iterator:
        p_x = float(data.count(x))/len(data)
        if p_x > 0:
            entropy += - p_x*math.log(p_x, 2)
    return entropy


def check_amazonbe(c, match, search, config):
    return_dict = {}

    ip, port = extract_ip_port(match)
    buckets = set()

    if Amazonbe.objects.filter(ip=ip).exists() or is_blacklisted(ip, config):
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


@shared_task(bind=True)
def brute_buckets(self, keyword):
    progress_recorder = ProgressRecorder(self)

    total = len(buckets_bruteforce)
    k = []
    for c,i in enumerate(buckets_bruteforce):


        req = requests.get("https://" + keyword + i.rstrip() + ".s3.amazonaws.com", verify=False)


        if req.status_code == 200 or req.status_code == 403:
            am = AmazonBuckets(bucket=keyword + i.rstrip() + ".s3.amazonaws.com", confirmed=False,for_later=False)
            am.save()

        progress_recorder.set_progress(c+1, total=total)

        self.update_state(state="PROGRESS",
                          meta={"results": "https://" + keyword + i.rstrip() + ".s3.amazonaws.com",
                                "code": req.status_code, "percentage": c / total * 100})

    self.update_state(state="SUCCESS",
                      meta={"type": 'amazon', "total": total})

    raise Ignore()

def clone_git_repo(git_url):
    try:
        project_path = tempfile.mkdtemp()
        Repo.clone_from(git_url, project_path)
        return project_path
    except Exception as e:
        print(e.args)

def get_strings_of_set(word, char_set, threshold=20):
    count = 0
    letters = ""
    strings = []
    for char in word:
        if char in char_set:
            letters += char
            count += 1
        else:
            if count > threshold:
                strings.append(letters)
            letters = ""
            count = 0
    if count > threshold:
        strings.append(letters)
    return strings

def get_secrets(diff, branch_name,prev_commit):
    stringsFound = set()
    paths = []
    path_secrets = {'path':[], 'secrets':[]}

    for blob in diff: # for every text in diff
        text = blob.diff.decode('utf-8', errors='replace') # extract text from blob
        # extract path from blob, sometimes it's a_path, sometimes it's b_path.
        path = blob.b_path if blob.b_path else blob.a_path

        if any(x in path for x in exclude):
            pass
        else:

            for line in text.split('\n'): # for every line in diff's blob
                for word in line.split():
                    base64_strings = get_strings_of_set(word, BASE64_CHARS) # check if string contains characters from base64 char set
                    hex_strings = get_strings_of_set(word, HEX_CHARS) # check if string contains character from hex char set
                    for string in base64_strings: # if any string was found
                        b64Entropy = shannon_entropy(string, BASE64_CHARS) # calculate entropy
                        if b64Entropy > 4.5:
                            stringsFound.add(string)
                            path_secrets['path'].append(path)# add string to list
                            path_secrets['secrets'].append(string)
                            # text = text.replace(string,colors.WARNING + string + bcolors.ENDC) # it is raw whole commit text
                    for string in hex_strings:
                        hexEntropy = shannon_entropy(string, HEX_CHARS)
                        if hexEntropy > 3:
                            stringsFound.add(string)
                            path_secrets['path'].append(path)
                            path_secrets['secrets'].append((string))
                            # text = text.replace(string,bcolors.WARNING + string + bcolors.ENDC)

    return path_secrets

def rules(diff):

    path_secrets = {'path':[], 'secrets':[]}
    with open('rules.json', "r") as ruleFile:
        rules = json.loads(ruleFile.read())
        for rule in rules:
            rules[rule] = re.compile(rules[rule])

        regex_matches = []

        for blob in diff:
            path = blob.b_path if blob.b_path else blob.a_path
            if any(x in path for x in exclude):
                pass
            else:
                for key in rules:
                    try:
                        text = blob.diff.decode('utf-8', errors='replace')
                        found_strings = rules[key].findall(text)
                        if len(found_strings) > 0:
                            for i in found_strings:
                                path_secrets['secrets'].append(i)


                            path_secrets['path'].append(path)
                    except:
                        pass

    return path_secrets

@shared_task(bind=True)
def javascript_search(self, keyword):
    ff = send_request(keyword)
    enpoints = parser_file(ff, regex_str, keyword)

    links = []
    context = []

    for i in enpoints:
        links.append(i['link'])
        context.append(i['context'])

    js = Javascript(secrets=links, context=context, path=keyword)
    js.save()

    self.update_state(state="SUCCESS",
                      meta={"type": 'js', "secrets": links, 'context':context,'path':keyword})

    raise Ignore()


@shared_task(bind=True)
def github_repo_search(self,keyword):
    progress_recorder = ProgressRecorder(self)
    project_path = clone_git_repo(keyword)  # cloning repo
    repo_git = Repo(project_path)
    branches = repo_git.remotes.origin.fetch()
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
            self.update_state(state="PROGRESS",
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

    self.update_state(state="SUCCESS",
                      meta={"type": 'github', "results":results})

    raise Ignore()
