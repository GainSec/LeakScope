"""Atomic local export files and bounded expiry cleanup."""
import csv
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4
from django.conf import settings

_EXPORT=re.compile(r'^[a-f0-9]{32}\.(json|csv|md|txt)$')


def export_directory():
    return Path(getattr(settings,'LEAKSCOPE_EXPORT_DIR','/tmp/leakscope_exports'))


def cleanup_expired_exports(now=None):
    directory=export_directory()
    if not directory.exists():return 0
    cutoff=(time.time() if now is None else now)-int(getattr(settings,'LEAKSCOPE_EXPORT_TTL_SECONDS',86400))
    removed=0
    for path in directory.iterdir():
        if _EXPORT.fullmatch(path.name) and path.is_file() and path.stat().st_mtime<cutoff:
            path.unlink(missing_ok=True)
            removed+=1
    return removed


def safe_csv_cell(value):
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


def write_export_file(data,fmt='json'):
    directory=export_directory();directory.mkdir(parents=True,exist_ok=True)
    cleanup_expired_exports()
    token=uuid4().hex
    fmt=str(fmt).lower() if str(fmt).lower() in ('json','csv') else 'json'
    if fmt=='json':content=json.dumps(data,default=str)
    else:
        buf=io.StringIO()
        if isinstance(data,list) and data and isinstance(data[0],dict):
            fields=list(dict.fromkeys(key for row in data if isinstance(row,dict) for key in row))
            writer=csv.DictWriter(buf,fieldnames=fields,extrasaction='ignore')
            writer.writerow({field:safe_csv_cell(field) for field in fields})
            for row in data:writer.writerow({k:safe_csv_cell(json.dumps(v,default=str) if isinstance(v,(dict,list)) else v) for k,v in row.items()})
        else:
            writer=csv.writer(buf);writer.writerow(['data'])
            for row in data if isinstance(data,list) else [data]:writer.writerow([safe_csv_cell(json.dumps(row,default=str) if not isinstance(row,(str,int,float,bool)) else row)])
        content=buf.getvalue()
    encoded=content.encode('utf-8')
    if len(encoded)>int(getattr(settings,'LEAKSCOPE_MAX_EXPORT_BYTES',100*1024*1024)):
        raise ValueError('Export exceeds the configured size limit')
    target=directory/(token+'.'+fmt)
    fd,temp=tempfile.mkstemp(prefix='.export-',suffix='.tmp',dir=directory)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return {'token':token,'path':str(target)}


def write_export_text(content,extension):
    """Write ready-made Markdown or plain text as a private export file, with the same expiry as other exports."""
    if extension not in ('md','txt'):raise ValueError('Unsupported export format')
    directory=export_directory();directory.mkdir(parents=True,exist_ok=True)
    cleanup_expired_exports()
    encoded=str(content).encode('utf-8')
    if len(encoded)>int(getattr(settings,'LEAKSCOPE_MAX_EXPORT_BYTES',100*1024*1024)):
        raise ValueError('Export exceeds the configured size limit')
    token=uuid4().hex
    target=directory/(token+'.'+extension)
    fd,temp=tempfile.mkstemp(prefix='.export-',suffix='.tmp',dir=directory)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return {'token':token,'path':str(target)}
