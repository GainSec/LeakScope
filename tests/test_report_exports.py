import csv
import io
import json
import tempfile
import zipfile
from pathlib import Path

from django.test import TestCase, override_settings

from leakscope_app.models import CertificateName, CertificateRecord, EvidenceArtifact, ServiceEndpoint, ServiceObservation


def table(response):
    return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))


class CertificateExportTests(TestCase):
    def setUp(self):
        for index in range(130):
            record = CertificateRecord.objects.create(key='%064d' % index, fingerprint='fp%03d' % index, domains=['h%d.example.com' % index],
                                                      issuer={'O': '=Evil Issuer' if index == 0 else 'Issuer'})
            CertificateName.objects.create(certificate=record, name='h%d.example.com' % index)
        other = CertificateRecord.objects.create(key='z' * 64, fingerprint='other', domains=['other.test'])
        CertificateName.objects.create(certificate=other, name='other.test')

    def test_csv_holds_every_match_not_one_page(self):
        response = self.client.get('/export/certificates/', {'q': 'example.com', 'format': 'csv'})
        self.assertEqual(response.status_code, 200)
        rows = table(response)
        self.assertEqual(rows[0][:3], ['Certificate', 'Names', 'Issuer'])
        self.assertEqual(len(rows), 131)
        self.assertEqual((response['X-Total-Count'], response['X-Exported-Count']), ('130', '130'))
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertNotIn('other', {row[0] for row in rows})
        self.assertIn("'=Evil Issuer", {row[2] for row in rows}, 'a formula-leading cell is stored as text')

    def test_txt_and_bad_format(self):
        response = self.client.get('/export/certificates/', {'format': 'txt', 'limit': '5'})
        lines = response.content.decode().strip().split('\n')
        self.assertEqual(len(lines), 6)
        self.assertTrue(lines[0].startswith('Certificate\tNames'))
        self.assertEqual(self.client.get('/export/certificates/', {'format': 'exe'}).status_code, 400)


class OrganizationReportTests(TestCase):
    def setUp(self):
        created = self.client.post('/api/assets/organizations', json.dumps({'name': 'Fixture <b>Corp</b>', 'aliases': ['Fixture'],
                                   'domains': ['example.com', 'api.example.com']}), content_type='application/json')
        self.assertEqual(created.status_code, 200, created.content)
        self.id = created.json()['organization']['id']

    def get(self, fmt):
        return self.client.get('/export/organizations/%s/' % self.id, {'format': fmt})

    def test_html_report_is_escaped_and_complete(self):
        response = self.get('html')
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn('Fixture &lt;b&gt;Corp&lt;/b&gt;', page)
        self.assertNotIn('<b>Corp</b>', page)
        for word in ('Domain scopes', 'api.example.com', 'Saved results', 'Services', 'Recent evidence'):
            self.assertIn(word, page)
        self.assertNotIn('<script', page)
        self.assertIn('attachment; filename="organization-fixture-b-corp-b-', response['Content-Disposition'])

    def test_txt_csv_and_json(self):
        text = self.get('txt').content.decode()
        self.assertIn('Domain scopes\n-------------\nDomain\tSource\n', text)
        self.assertIn('example.com\tuser_scope', text)
        rows = table(self.get('csv'))
        self.assertIn(['Domain scopes'], rows)
        self.assertIn(['api.example.com', 'user_scope'], rows)
        self.assertEqual({row['name'] for row in self.get('json').json()['domains']}, {'example.com', 'api.example.com'})

    def test_unknown_organization_and_format(self):
        self.assertEqual(self.client.get('/export/organizations/00000000-0000-0000-0000-000000000000/').status_code, 404)
        self.assertEqual(self.get('pdf').status_code, 400)


class ScreenshotArchiveTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        setting = override_settings(LEAKSCOPE_CAPTURE_DIR=self.root); setting.enable(); self.addCleanup(setting.disable)
        for index, (address, stored) in enumerate((('198.51.100.9', True), ('198.51.100.10', True), ('198.51.100.11', False))):
            owner = ServiceEndpoint.objects.create(key='web%d' % index, address=address, port=443, scheme='https', service_type='https', title='<i>Title %d</i>' % index)
            proof = ServiceObservation.objects.create(endpoint=owner, provider='fetch:screenshot', fingerprint=str(index), data={'service_type': 'https'})
            if stored:
                (self.root / (owner.key + '.png')).write_bytes(b'\x89PNG' + bytes([index]) * 20)
            EvidenceArtifact.objects.create(endpoint=owner, kind='screenshot', relative_path=owner.key + '.png', mime='image/png', size=24, sha256=str(index) * 64,
                                            tool='playwright', metadata={'observation_pk': proof.pk.hex})

    def archive(self, **params):
        response = self.client.get('/export/screenshots/', params)
        self.assertEqual(response.status_code, 200, getattr(response, 'content', b'')[:200])
        return response, zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))

    def test_archive_has_images_csv_and_page(self):
        response, bundle = self.archive()
        names = bundle.namelist()
        images = [name for name in names if name.startswith('images/')]
        self.assertEqual(len(images), 2, names)
        self.assertTrue(all(name.endswith('.png') and '198-51-100' in name for name in images))
        self.assertTrue(bundle.read(images[0]).startswith(b'\x89PNG'))
        rows = list(csv.reader(io.StringIO(bundle.read('screenshots.csv').decode('utf-8-sig'))))
        self.assertEqual(rows[0][:3], ['File', 'Address', 'Port'])
        self.assertEqual(len(rows), 4)
        self.assertEqual(sorted(row[0] for row in rows[1:]), sorted(images + ['']), 'a missing file is listed without a file name')
        page = bundle.read('index.html').decode()
        for name in images:
            self.assertIn('src="%s"' % name, page)
        self.assertIn('&lt;i&gt;Title 0&lt;/i&gt;', page)
        self.assertNotIn('<i>Title', page)
        self.assertIn('1 no longer stored', page)
        self.assertEqual((response['X-Total-Count'], response['X-Exported-Count']), ('3', '2'))

    def test_filters_and_limit_apply(self):
        _, bundle = self.archive(q='198.51.100.10')
        self.assertEqual(len([name for name in bundle.namelist() if name.startswith('images/')]), 1)
        _, bundle = self.archive(limit='1')
        self.assertEqual(len(list(csv.reader(io.StringIO(bundle.read('screenshots.csv').decode('utf-8-sig'))))), 2)
        self.assertEqual(self.client.get('/export/screenshots/', {'bogus': '1', 'sort': 'nope'}).status_code, 400)

    def test_chosen_images_only(self):
        ids = [str(row.pk) for row in EvidenceArtifact.objects.order_by('relative_path')]
        response = self.client.post('/export/screenshots/', {'ids': ids[1]})
        self.assertEqual(response.status_code, 200)
        bundle = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
        images = [name for name in bundle.namelist() if name.startswith('images/')]
        self.assertEqual(len(images), 1)
        self.assertIn('198-51-100-10', images[0])
        self.assertEqual(response['X-Total-Count'], '1')
        self.assertEqual(self.client.post('/export/screenshots/', {'ids': ''}).status_code, 400)
        self.assertEqual(self.client.post('/export/screenshots/', {'ids': 'x;drop'}).status_code, 400)


class PagedTableExportTests(TestCase):
    def setUp(self):
        for index in range(230):
            owner = ServiceEndpoint.objects.create(key='e%d' % index, address='203.0.113.%d' % (index % 250), port=1000 + index, service_type='http' if index % 2 else 'ssh', title='=cmd' if index == 0 else 'T')
            ServiceObservation.objects.create(endpoint=owner, provider='shodan', fingerprint=str(index), data={'service_type': owner.service_type})

    def test_every_matching_observation_is_exported(self):
        response = self.client.get('/export/table/observations/', {'format': 'csv', 'page': '3', 'page_size': '10'})
        self.assertEqual(response.status_code, 200, response.content[:300])
        rows = table(response)
        self.assertEqual(len(rows), 231)
        self.assertIn('address', rows[0]); self.assertIn('port', rows[0])
        self.assertEqual((response['X-Total-Count'], response['X-Exported-Count']), ('230', '230'))
        self.assertIn("'=cmd", {cell for row in rows for cell in row})

    def test_txt_limit_and_unknown_table(self):
        response = self.client.get('/export/table/observations/', {'format': 'txt', 'limit': '7'})
        self.assertEqual(len(response.content.decode().strip().split('\n')), 8)
        self.assertEqual(self.client.get('/export/table/secrets/').status_code, 404)
        self.assertEqual(self.client.get('/export/table/findings/', {'format': 'csv'}).status_code, 200)
        self.assertEqual(self.client.get('/export/table/cloud/', {'format': 'csv'}).status_code, 200)


class RunHistoryExportTests(TestCase):
    def test_every_run_is_exported_not_the_newest_hundred(self):
        from leakscope_app.models import Run
        Run.objects.bulk_create([Run(kind='discover', type='http', provider='shodan', state='succeeded', error='=bad' if index == 0 else '') for index in range(140)])
        response = self.client.get('/export/runs/', {'format': 'csv'})
        rows = table(response)
        self.assertEqual(len(rows), 141)
        self.assertEqual(rows[0][:4], ['Operation', 'Type', 'Provider', 'State'])
        self.assertEqual((response['X-Total-Count'], response['X-Exported-Count']), ('140', '140'))
        self.assertIn("'=bad", {row[8] for row in rows})
        self.assertEqual(len(self.client.get('/export/runs/', {'format': 'txt', 'limit': '3'}).content.decode().strip().split('\n')), 4)
        self.assertEqual(self.client.get('/export/runs/', {'format': 'xml'}).status_code, 400)
