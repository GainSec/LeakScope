import gzip
import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from leakscope import database_archive as archive
from leakscope_app.models import BlacklistEntry, Run


def exported():
    out = io.BytesIO()
    counts = archive.write(out)
    return out.getvalue(), counts


def lines(data):
    return [json.loads(line) for line in gzip.decompress(data).decode().splitlines()]


class Scratch:
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        override = override_settings(DATA_DIR=Path(folder.name))
        override.enable()
        self.addCleanup(override.disable)

    def stored(self, data, name='incoming-' + '0' * 32 + '.jsonl.gz'):
        path = archive.folder() / name
        path.write_bytes(data)
        return path


class ArchiveTests(Scratch, TransactionTestCase):
    def test_export_then_import_brings_back_the_same_rows_and_their_times(self):
        BlacklistEntry.objects.create(value='keep.example')
        run = Run.objects.create(kind='discover', state='succeeded')
        old = timezone.now() - timedelta(days=30)
        Run.objects.filter(pk=run.pk).update(updated_at=old, created_at=old)
        data, counts = exported()
        self.assertEqual(counts['leakscope_app.run'], 1)
        rows = lines(data)
        self.assertEqual(rows[0]['format'], 'leakscope-database')
        self.assertEqual(rows[-1], {'end': True, 'rows': counts})

        Run.objects.all().delete()
        BlacklistEntry.objects.create(value='made-after-export.example')
        restored = archive.restore(self.stored(data))
        self.assertEqual(restored, counts)
        self.assertEqual(list(BlacklistEntry.objects.values_list('value', flat=True)), ['keep.example'])
        back = Run.objects.get(pk=run.pk)
        self.assertEqual((back.updated_at, back.created_at), (old, old))
        self.assertTrue(BlacklistEntry.objects.create(value='new-row.example').pk > 0, 'new rows still get an id')

    def test_a_file_cut_short_changes_nothing(self):
        BlacklistEntry.objects.create(value='keep.example')
        data, _ = exported()
        whole = gzip.decompress(data).decode().splitlines()
        short = gzip.compress(('\n'.join(whole[:-1]) + '\n').encode())
        BlacklistEntry.objects.create(value='current.example')
        with self.assertRaisesMessage(archive.ArchiveError, 'incomplete'):
            archive.restore(self.stored(short))
        self.assertEqual(BlacklistEntry.objects.count(), 2)
        with self.assertRaisesMessage(archive.ArchiveError, 'damaged'):
            archive.restore(self.stored(data[:len(data) // 2]))
        self.assertEqual(BlacklistEntry.objects.count(), 2)

    def test_files_that_are_not_ours_or_from_another_version_are_refused(self):
        data, _ = exported()
        with self.assertRaisesMessage(archive.ArchiveError, 'not a LeakScope database file'):
            archive.inspect(self.stored(b'plain text'))
        with self.assertRaisesMessage(archive.ArchiveError, 'not a LeakScope database file'):
            archive.inspect(self.stored(gzip.compress(b'{"format":"other"}\n')))
        rows = gzip.decompress(data).decode().splitlines()
        header = json.loads(rows[0])
        header['layout'] = header['layout'][:-1]
        other = gzip.compress(('\n'.join([json.dumps(header)] + rows[1:]) + '\n').encode())
        with self.assertRaisesMessage(archive.ArchiveError, 'different LeakScope version'):
            archive.restore(self.stored(other))

    def test_the_job_saves_a_copy_first_records_the_outcome_and_removes_the_upload(self):
        BlacklistEntry.objects.create(value='keep.example')
        data, _ = exported()
        BlacklistEntry.objects.create(value='current.example')
        path = self.stored(data)
        result = archive.run_import(path.name, 'mine.jsonl.gz')
        self.assertEqual(result['state'], 'done')
        self.assertEqual(result['file'], 'mine.jsonl.gz')
        self.assertFalse(path.exists())
        copy = archive.folder() / result['safety_copy']
        self.assertIn('current.example', gzip.decompress(copy.read_bytes()).decode())
        self.assertEqual(BlacklistEntry.objects.count(), 1)
        self.assertEqual(archive.status()['state'], 'done')

        bad = self.stored(gzip.compress(b'{"format":"other"}\n'))
        self.assertEqual(archive.run_import(bad.name)['state'], 'failed')
        self.assertEqual(BlacklistEntry.objects.count(), 1)
        with self.assertRaises(archive.ArchiveError):
            archive.run_import('../../etc/passwd')

    def test_only_the_newest_safety_copies_are_kept(self):
        for stamp in ('20260101-000000', '20260102-000000', '20260103-000000', '20260104-000000'):
            (archive.folder() / ('before-import-%s.jsonl.gz' % stamp)).write_bytes(b'x')
        archive.safety_copy()
        names = sorted(p.name for p in archive.folder().glob('before-import-*'))
        self.assertEqual(len(names), 3)
        self.assertNotIn('before-import-20260101-000000.jsonl.gz', names)


class ViewTests(Scratch, TestCase):
    def upload(self, data, confirm='REPLACE'):
        return self.client.post('/api/settings/database/import', {'file': SimpleUploadedFile('mine.jsonl.gz', data), 'confirm': confirm})

    def test_download_is_a_complete_archive(self):
        BlacklistEntry.objects.create(value='keep.example')
        response = self.client.get('/export/database/')
        self.assertEqual(response.status_code, 200)
        self.assertRegex(response['Content-Disposition'], r'attachment; filename="leakscope-database-\d{8}-\d{6}\.jsonl\.gz"')
        rows = lines(b''.join(response.streaming_content))
        self.assertTrue(rows[-1]['end'])
        self.assertTrue(any(row.get('fields', {}).get('value') == 'keep.example' for row in rows))

    def test_import_needs_a_file_the_typed_word_and_no_jobs(self):
        data, _ = exported()
        with mock.patch('leakscope.tasks.restore_database.delay') as queue:
            self.assertEqual(self.client.post('/api/settings/database/import', {'confirm': 'REPLACE'}).status_code, 400)
            self.assertEqual(self.upload(data, confirm='yes').status_code, 400)
            self.assertEqual(self.upload(b'nonsense').status_code, 400)
            run = Run.objects.create(kind='discover', state='running')
            busy = self.upload(data)
            self.assertEqual(busy.status_code, 409)
            self.assertIn('1 job queued or running', busy.json()['error'])
            run.delete()
            queue.assert_not_called()
            self.assertEqual(list(archive.folder().glob('incoming-*')), [])

            accepted = self.upload(data)
            self.assertEqual(accepted.status_code, 202)
            name, original = queue.call_args.args
            self.assertTrue((archive.folder() / name).is_file())
            self.assertEqual(original, 'mine.jsonl.gz')
            self.assertEqual(accepted.json()['import']['state'], 'queued')
            self.assertEqual(self.upload(data).status_code, 409, 'one import at a time')
            self.assertEqual(self.client.get('/export/database/').status_code, 409)
        self.assertTrue(self.client.get('/api/settings/database').json()['importing'])

    def test_a_status_that_stopped_moving_no_longer_blocks(self):
        archive.set_status(state='running')
        self.assertTrue(archive.importing())
        path = archive.folder() / 'import-status.json'
        value = json.loads(path.read_text())
        value['updated'] = (timezone.now() - timedelta(hours=3)).isoformat()
        path.write_text(json.dumps(value))
        self.assertFalse(archive.importing())
