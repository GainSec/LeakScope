"""Canonical endpoint identifiers; source identity stays separate from review state."""
import ast
import hashlib
import ipaddress
import json


def normalize_host(host):
    value=str(host or '').strip().strip('[]').rstrip('.').lower()
    try:return str(ipaddress.ip_address(value))
    except ValueError:return value


def normalize_port(port):
    if port in (None,''):return ''
    try:return str(int(port))
    except (ValueError,TypeError):return str(port).strip()


def fingerprint(type_name,host,port):
    from .registry import canonical_type
    raw='{}|{}|{}'.format(canonical_type(type_name),normalize_host(host),normalize_port(port))
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_list(value):
    if isinstance(value,(list,tuple,set)):return list(value)
    if isinstance(value,dict):return list(value)
    if not isinstance(value,str):return []
    value=value.strip()
    for parse in (json.loads,ast.literal_eval):
        try:
            parsed=parse(value)
            if isinstance(parsed,(list,tuple,set)):return list(parsed)
            if isinstance(parsed,dict):return list(parsed)
        except (ValueError,SyntaxError,TypeError):pass
    return [x.strip() for x in value.split(',') if x.strip()]


def is_hidden(type_name,obj,ip=None,port=None):
    from leakscope_app.models import FingerprintLog, FindingReview
    from .registry import canonical_type
    object_id=getattr(obj,'pk',obj)
    review=FindingReview.objects.filter(type=canonical_type(type_name),object_id=object_id).first()
    if review is not None:return review.hidden
    host=ip if ip is not None else getattr(obj,'ip',None)
    service_port=port if port is not None else getattr(obj,'port','')
    if not host:return False
    logs=FingerprintLog.objects.filter(ip=host,status='hidden')
    for row in logs:
        if canonical_type(row.type)==canonical_type(type_name) and normalize_port(row.port)==normalize_port(service_port):
            return True
    return False


def set_hidden(type_name,obj,hidden=True):
    from leakscope_app.models import FindingReview
    from .registry import canonical_type
    return FindingReview.objects.update_or_create(type=canonical_type(type_name),object_id=getattr(obj,'pk',obj),defaults={'hidden':bool(hidden)})[0]


def hidden_ids(type_name):
    from leakscope_app.models import FindingReview
    from .registry import canonical_type
    return set(FindingReview.objects.filter(type=canonical_type(type_name),hidden=True).values_list('object_id',flat=True))
