"""Private, verified SQLite export and empty-target PostgreSQL import.

Run with the application's Python interpreter. No production path is implicit.
The source is backed up via SQLite's backup API and only the copy is migrated.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
GENERATED = {'contenttypes.contenttype', 'auth.permission', 'sessions.session'}


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content)


def dump_arguments():
    # Keep primary keys, including auth users/groups. Only foreign keys use
    # natural names, so regenerated content-type/permission IDs remain valid.
    return ['dumpdata', '--natural-foreign', '--exclude', 'contenttypes',
            '--exclude', 'auth.permission', '--exclude', 'sessions', '--indent', '2']


def target_has_data(counts):
    return any(count for label, count in counts.items() if label not in GENERATED)


def setup_django():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'leakscope.settings')
    import django
    django.setup()


def canonical_row(model,row):
    # Relation membership has no ordering guarantee across SQLite/PostgreSQL.
    # Ordinary JSON arrays remain ordered evidence and must never be sorted.
    for field in model._meta.many_to_many:
        values=row['fields'].get(field.name)
        if isinstance(values,list):
            row['fields'][field.name]=sorted(values,key=lambda item:json.dumps(item,sort_keys=True,default=str))
    return row


def database_state():
    from django.apps import apps
    from django.core import serializers
    result = {}
    for model in apps.get_models():
        label = model._meta.label_lower
        if label in GENERATED:
            continue
        count = 0
        digest = hashlib.sha256()
        for obj in model.objects.order_by(model._meta.pk.name).iterator(chunk_size=1000):
            row = canonical_row(model,json.loads(serializers.serialize('json', [obj], use_natural_foreign_keys=True))[0])
            digest.update(json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
            digest.update(b'\n')
            count += 1
        result[label] = {'rows': count, 'sha256': digest.hexdigest()}
    return result


def export(source, output, manifest):
    source, output, manifest = map(Path, (source, output, manifest))
    if not source.is_file():
        raise ValueError('Source SQLite file does not exist')
    if output.exists() or manifest.exists():
        raise FileExistsError('Export outputs must not already exist')
    for path in (output, manifest):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='leakscope-sqlite-export-') as temp:
        snapshot = Path(temp) / 'db.sqlite3'
        with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
            with sqlite3.connect(snapshot) as dst:
                src.backup(dst)
        snapshot.chmod(0o600)
        env = dict(os.environ, DATABASE_URL='', LEAKSCOPE_DATA_DIR=temp,
                   DJANGO_SETTINGS_MODULE='leakscope.settings')
        migrate = [sys.executable, str(ROOT/'manage.py'), 'migrate', '--noinput', '--skip-checks']
        subprocess.run(migrate, env=env, cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                subprocess.run([sys.executable, str(ROOT/'manage.py'), *dump_arguments()], env=env,
                               cwd=ROOT, check=True, stdout=stream, stderr=subprocess.PIPE)
            state_raw = subprocess.check_output([sys.executable, str(Path(__file__).resolve()), 'state'],
                                                env=env, cwd=ROOT, stderr=subprocess.PIPE)
            with output.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            record = {'format': 1, 'fixture_sha256': digest, 'models': json.loads(state_raw)}
            write_private(manifest, (json.dumps(record, indent=2) + '\n').encode())
        except Exception:
            # Preserve the original source; remove only this tool's incomplete output.
            output.unlink(missing_ok=True)
            raise
    return record


def restore(fixture, manifest):
    setup_django()
    from django.apps import apps
    from django.core.management import call_command
    from django.core.management.color import no_style
    from django.db import connection, transaction
    if connection.vendor != 'postgresql':
        raise ValueError('Import target must be PostgreSQL')
    record = json.loads(Path(manifest).read_text())
    if record.get('format') != 1 or not isinstance(record.get('models'), dict):
        raise ValueError('Unsupported migration manifest')
    with Path(fixture).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != record.get('fixture_sha256'):
        raise ValueError('Fixture does not match its migration manifest')
    with transaction.atomic():
        current = database_state()
        if target_has_data({key: state['rows'] for key, state in current.items()}):
            raise ValueError('Import target contains application data; refusing overwrite')
        call_command('loaddata', str(Path(fixture).resolve()), verbosity=0, stdout=io.StringIO())
        # Explicitly reset every affected sequence even when a fixture has no
        # rows for a model or was exported with an explicit non-default PK.
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(no_style(), list(apps.get_models())):
                cursor.execute(sql)
        actual = database_state()
        if actual != record['models']:
            raise ValueError('Imported rows or canonical content do not match the source; import rolled back')
    return actual


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export_args = commands.add_parser('export')
    export_args.add_argument('--source', required=True)
    export_args.add_argument('--output', required=True)
    export_args.add_argument('--manifest', required=True)
    import_args = commands.add_parser('import')
    import_args.add_argument('--fixture', required=True)
    import_args.add_argument('--manifest', required=True)
    commands.add_parser('state', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.command == 'state':
            setup_django()
            print(json.dumps(database_state()))
            return 0
        if args.command == 'export':
            state = export(args.source, args.output, args.manifest)['models']
        else:
            state = restore(args.fixture, args.manifest)
        print(json.dumps({'models': len(state), 'rows': sum(item['rows'] for item in state.values()),
                          'content_verified': True, 'operation': args.command}))
        return 0
    except Exception as exc:
        # Database URLs, fixture rows and subprocess errors can contain private data.
        print(f'Migration {args.command} failed ({type(exc).__name__}); source retained, no credentials printed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
