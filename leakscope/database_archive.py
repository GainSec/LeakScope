"""The whole database as one file, and back again.

The file is gzip-compressed JSON lines: a header, one line per saved row, and a closing line with the
row count of every table. Import replaces everything in one transaction, so a file that is cut short,
from another version, or broken leaves the current data exactly as it was.
"""
import gzip
import json
import os
import re
from datetime import datetime, time, timedelta
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management.color import no_style
from django.core.serializers.base import DeserializationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

FORMAT = 'leakscope-database'
VERSION = 1
# Rebuilt by migrations or tied to one browser; never carried between databases.
LEFT_OUT = {'contenttypes.contenttype', 'auth.permission', 'sessions.session', 'admin.logentry'}
SAFETY_COPIES = 3
INCOMING = re.compile(r'^incoming-[0-9a-f]{32}\.jsonl\.gz$')
ACTIVE = ('queued', 'running')


class ArchiveError(ValueError):
    """The file cannot be imported; the message is safe to show."""


def folder():
    path = Path(settings.DATA_DIR) / 'database-archives'
    path.mkdir(parents=True, exist_ok=True)
    return path


def tables():
    return [model for model in apps.get_models()
            if model._meta.managed and not model._meta.proxy and model._meta.label_lower not in LEFT_OUT]


def layout():
    """The applied migrations: two databases with the same list have the same tables and columns."""
    return sorted('%s.%s' % pair for pair in MigrationRecorder(connection).applied_migrations())


class _Exact(DjangoJSONEncoder):
    """Times keep their microseconds; the stock encoder rounds them to milliseconds."""

    def default(self, value):
        if isinstance(value, (datetime, time)):
            return value.isoformat()
        return super().default(value)


def _line(out, value):
    out.write(json.dumps(value, cls=_Exact, ensure_ascii=False, separators=(',', ':')).encode() + b'\n')


def _rows(model):
    chunk = []
    for item in model._base_manager.order_by('pk').iterator(chunk_size=1000):
        chunk.append(item)
        if len(chunk) == 500:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def write(stream):
    """Write every table to stream and return {table: rows}."""
    counts = {}
    with gzip.GzipFile(fileobj=stream, mode='wb') as out:
        # One snapshot, so rows written by a job that is running do not half-appear.
        snapshot = connection.vendor == 'postgresql' and not connection.in_atomic_block
        with transaction.atomic():
            if snapshot:
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            _line(out, {'format': FORMAT, 'version': VERSION, 'created': timezone.now(), 'layout': layout()})
            for model in tables():
                label, count = model._meta.label_lower, 0
                for chunk in _rows(model):
                    for row in serializers.serialize('python', chunk, use_natural_foreign_keys=True):
                        _line(out, row)
                    count += len(chunk)
                counts[label] = count
        _line(out, {'end': True, 'rows': counts})
    return counts


def inspect(path):
    """Read the header and refuse a file this database cannot take."""
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as lines:
            header = json.loads(lines.readline() or 'null')
    except (OSError, EOFError, ValueError, UnicodeDecodeError):
        raise ArchiveError('This is not a LeakScope database file.')
    if not isinstance(header, dict) or header.get('format') != FORMAT:
        raise ArchiveError('This is not a LeakScope database file.')
    if header.get('version') != VERSION:
        raise ArchiveError('This database file was made by a different LeakScope version and cannot be imported here.')
    if header.get('layout') != layout():
        raise ArchiveError('This database file was made by a different LeakScope version. Export and import need the same version on both sides.')
    return header


def _insert(model, objects):
    """Insert rows exactly as saved: raw, so "last updated" times are not replaced with now."""
    fields = model._meta.concrete_fields
    size = max(1, connection.ops.bulk_batch_size(fields, objects))
    for start in range(0, len(objects), size):
        model._base_manager._insert(objects[start:start + size], fields=fields, raw=True, using=connection.alias)


def _clear(models):
    names = {model._meta.db_table for model in models}
    for model in models:
        names.update(field.remote_field.through._meta.db_table for field in model._meta.local_many_to_many
                     if field.remote_field.through._meta.auto_created)
    with connection.cursor() as cursor:
        for statement in connection.ops.sql_flush(no_style(), sorted(names), reset_sequences=False, allow_cascade=True):
            cursor.execute(statement)


def restore(path):
    """Replace every table with the file's rows. All or nothing. Returns {table: rows}."""
    inspect(path)
    wanted = {model._meta.label_lower: model for model in tables()}
    counts = {label: 0 for label in wanted}
    try:
        with transaction.atomic():
            _clear(list(wanted.values()))
            batch, current, links, closing = [], None, [], None

            def flush():
                if not batch:
                    return
                model = wanted[current]
                if model._meta.parents:
                    for item in batch:
                        item.save()
                else:
                    _insert(model, [item.object for item in batch])
                    links.extend(item for item in batch if item.m2m_data)
                counts[current] += len(batch)
                batch.clear()

            with gzip.open(path, 'rt', encoding='utf-8') as lines:
                lines.readline()
                for raw in lines:
                    row = json.loads(raw)
                    if row.get('end'):
                        closing = row
                        break
                    label = row.get('model')
                    if label not in wanted:
                        raise ArchiveError('This database file has a table this version does not know: %s.' % str(label)[:80])
                    if label != current:
                        flush()
                        current = label
                    batch.extend(serializers.deserialize('python', [row]))
                    if len(batch) >= 500:
                        flush()
                flush()
            if not closing or closing.get('rows') != counts:
                raise ArchiveError('This database file is incomplete. Nothing was changed.')
            for item in links:
                for name, values in item.m2m_data.items():
                    getattr(item.object, name).set(values)
            with connection.cursor() as cursor:
                for statement in connection.ops.sequence_reset_sql(no_style(), list(wanted.values())):
                    cursor.execute(statement)
    except ArchiveError:
        raise
    except (OSError, EOFError, ValueError, UnicodeDecodeError, DeserializationError) as error:
        raise ArchiveError('This database file is damaged or incomplete (%s). Nothing was changed.' % type(error).__name__)
    return counts


# ---- import as a background job --------------------------------------------------------------

def _status_path():
    return folder() / 'import-status.json'


def status():
    try:
        value = json.loads(_status_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'state': 'idle'}
    return value if isinstance(value, dict) else {'state': 'idle'}


def set_status(**values):
    value = {**values, 'updated': timezone.now().isoformat()}
    scratch = _status_path().with_suffix('.tmp')
    scratch.write_text(json.dumps(value), encoding='utf-8')
    os.replace(scratch, _status_path())
    return value


def importing():
    """True while an import is waiting or running. A status that stopped moving for two hours is treated as dead."""
    value = status()
    if value.get('state') not in ACTIVE:
        return False
    try:
        return timezone.now() - datetime.fromisoformat(value['updated']) < timedelta(hours=2)
    except (KeyError, ValueError, TypeError):
        return False


def busy_jobs():
    from leakscope_app.models import Run
    return Run.objects.filter(state__in=ACTIVE).count()


def keep_upload(upload):
    """Store an uploaded file privately, check its header, and return its stored name."""
    import uuid
    name = 'incoming-%s.jsonl.gz' % uuid.uuid4().hex
    path = folder() / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as out:
        for chunk in upload.chunks():
            out.write(chunk)
    try:
        inspect(path)
    except ArchiveError:
        path.unlink(missing_ok=True)
        raise
    return name


def safety_copy():
    """Export the current database beside the app before it is replaced; keep the newest few."""
    path = folder() / ('before-import-%s.jsonl.gz' % timezone.now().strftime('%Y%m%d-%H%M%S-%f'))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as out:
        write(out)
    for old in sorted(folder().glob('before-import-*.jsonl.gz'), reverse=True)[SAFETY_COPIES:]:
        old.unlink(missing_ok=True)
    return path.name


def run_import(name, original=''):
    """The background job: safety copy, then replace. Records what happened for the Settings page."""
    if not INCOMING.match(name or ''):
        raise ArchiveError('Unknown import file.')
    path, base = folder() / name, {'file': str(original)[:200], 'started': timezone.now().isoformat()}
    try:
        set_status(state='running', step='Saving a copy of the current database', **base)
        base['safety_copy'] = safety_copy()
        set_status(state='running', step='Replacing the database', **base)
        counts = restore(path)
        return set_status(state='done', rows=sum(counts.values()), tables=sum(1 for n in counts.values() if n), **base)
    except ArchiveError as error:
        return set_status(state='failed', error=str(error), **base)
    except Exception as error:
        set_status(state='failed', error='The import stopped (%s). Nothing was changed.' % type(error).__name__, **base)
        raise
    finally:
        path.unlink(missing_ok=True)
