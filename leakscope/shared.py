"""Common data extraction, serialization and endpoint helpers."""
from celery import shared_task

import logging

import json

import os

from pathlib import Path

from uuid import uuid4

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

from typing import Any, Dict, Optional, Tuple, List

import ftplib

import socket

from pymongo import MongoClient

from cassandra.cluster import Cluster, NoHostAvailable, ExecutionProfile, EXEC_PROFILE_DEFAULT, DCAwareRoundRobinPolicy

from cassandra.query import SimpleStatement

from cassandra.auth import PlainTextAuthProvider

from bson import json_util

try:
    from StringIO import StringIO
    readBytesCustom = StringIO
except ImportError:
    from io import BytesIO
    readBytesCustom = BytesIO

from urllib.request import Request, urlopen

import hashlib

import base64

from django.utils import timezone

from urllib.parse import urlparse

import jxmlease

from leakscope_app.models import Monitor, Search, Rethink, Cassandra, Gitlab, Elastic, Dirs, Jenkins, Mongo, Rsync, \
    Sonarqube, Couchdb, Kibana, Ftp, Amazonbe, AmazonBuckets, Keys, Github, Amazons3be, Angular, Javascript, \
    OpenSearch, Grafana, Prometheus, Minio, Swagger, MongoExpress, FingerprintLog, CustomQuery, Nexus, Artifactory, Registry, \
    Etcd, ConsulKV, Rabbitmq, Solr, Gitea, Gogs, AzureBlob, GcsBucket

from leakscope.shodan_client import ShodanClient, ShodanClientError

from leakscope.zoomeye_client import ZoomEyeClient, ZoomEyeClientError, normalize_match

from leakscope.celery import app

from django.conf import settings

from leakscope.services.config import get_config, redact

from leakscope.services.identity import fingerprint as canonical_fingerprint

from leakscope.services.runs import publish_state

logger = logging.getLogger(__name__)

def _env_list(name: str):
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]

def is_blacklisted(value: str, config: Dict[str, Any]) -> bool:
    import ipaddress
    from leakscope.services.identity import normalize_host
    host = normalize_host(value)
    for entry in config.get("config", {}).get("blacklist", []):
        if host == normalize_host(entry):
            return True
        if '/' in str(entry):
            try:
                if ipaddress.ip_address(host) in ipaddress.ip_network(str(entry), strict=False):
                    return True
            except ValueError:
                pass
    return False

def _fingerprint(type_name: str, ip: str, port: Any) -> str:
    return canonical_fingerprint(type_name, ip, port)

def record_fingerprint(type_name: str, ip: str, port: Any) -> str:
    if not ip:
        return "unknown"
    fp = _fingerprint(type_name, ip, port)
    try:
        obj, created = FingerprintLog.objects.get_or_create(
            fingerprint=fp,
            defaults={"type": type_name, "ip": ip, "port": str(port or ""), "status": "new"},
        )
        if created:
            return "new"
        from django.db.models import F
        updates = {'seen_count': F('seen_count') + 1, 'last_seen': timezone.now()}
        if obj.status != 'hidden':
            updates['status'] = 'seen_before'
        FingerprintLog.objects.filter(pk=obj.pk).update(**updates)
        return "seen_before"
    except Exception as exc:  # pragma: no cover
        logger.debug("Fingerprint tracking failed for %s %s:%s - %s", type_name, ip, port, exc)
        return "unknown"

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

def _load_wordlist(fname: str) -> list:
    try:
        with open(fname, "r") as f:
            return f.readlines()
    except Exception:
        return []

buckets_bruteforce = _load_wordlist("buckets.txt")

azure_buckets_bruteforce = _load_wordlist("azure-buckets.txt") or buckets_bruteforce

gcs_buckets_bruteforce = _load_wordlist("gcs-buckets.txt") or buckets_bruteforce

from leakscope.services.query import build_shodan_query, build_provider_query, render_query_template

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

def _safe_int(val, default=0):
    try:
        return int(val)
    except Exception:
        return default

def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json_util.dumps(value))
    except Exception:
        try:
            json.dumps(value)
            return value
        except Exception:
            return str(value)

from leakscope.exporters.storage import write_export_file as _write_export_file

def _limit_max(value: Any, default: int = 1000, hard_max: int = 5000) -> int:
    try:
        iv = int(value)
    except Exception:
        iv = default
    return max(1, min(iv, hard_max))

def _http_base(ip: str, port: Any, scheme: str = "http") -> str:
    port_int = _safe_int(port, None)
    default_port = 443 if scheme == "https" else 80
    if port_int and port_int != default_port:
        return f"{scheme}://{ip}:{port_int}"
    return f"{scheme}://{ip}"

def _parse_link_next(header_val: str) -> Optional[str]:
    if not header_val:
        return None
    for part in header_val.split(","):
        if 'rel="next"' in part:
            start = part.find("<")
            end = part.find(">")
            if start != -1 and end != -1 and end > start:
                return part[start + 1:end]
    return None

def _infer_host(match: Dict[str, Any]) -> str:
    http_info = match.get("http") or {}
    host_hdr = (http_info.get("host") or "").strip()
    if host_hdr:
        return host_hdr
    return str(match.get("ip_str") or match.get("ip") or "")

def parse_bucket(link):
    parsed = urlparse(link)
    if parsed.hostname in buckets_all:
        path_splitted = parsed.path.split("/")
        return urlparse(link).hostname + "/" + path_splitted[1]
    else:
        return urlparse(link).hostname

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

def clone_git_repo(git_url):
    try:
        project_path = tempfile.mkdtemp()
        Repo.clone_from(git_url, project_path, kill_after_timeout=60, env={"GIT_TERMINAL_PROMPT": "0"})
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

