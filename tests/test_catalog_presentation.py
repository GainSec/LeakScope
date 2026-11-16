"""Catalog cards have local artwork and truthful, inert official references."""
import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from xml.etree import ElementTree

from django.test import SimpleTestCase

from leakscope.phase3.catalog import catalog, catalog_entry


ICON_ROOT = Path(__file__).resolve().parents[1] / 'leakscope_app/static/img/service-icons'


class CatalogPresentationTests(SimpleTestCase):
    def test_every_actual_catalog_record_has_bounded_local_presentation(self):
        with patch('socket.socket', side_effect=AssertionError('catalog browsing must be offline')):
            rows = catalog()
        for row in rows:
            with self.subTest(key=row['key']):
                self.assertIn('presentation', row)
                presentation = row['presentation']
                self.assertEqual(set(presentation), {'icon_url', 'icon_kind', 'documentation_url', 'download_url', 'homepage_url'})
                self.assertIn(presentation['icon_kind'], {'brand', 'protocol', 'generic'})
                self.assertRegex(presentation['icon_url'], r'^/static/img/service-icons/[a-z0-9-]+\.(?:svg|jpg)$')
                self.assertTrue((ICON_ROOT / presentation['icon_url'].rsplit('/', 1)[1]).is_file())
                for field in ('documentation_url', 'download_url', 'homepage_url'):
                    url = presentation[field]
                    if url is not None:
                        parsed = urlparse(url)
                        self.assertEqual(parsed.scheme, 'https')
                        self.assertTrue(parsed.hostname)
                        self.assertFalse(parsed.username or parsed.password or parsed.fragment)
                        self.assertLess(len(url), 300)
                        self.assertNotRegex(url, r'(google\.com/search|shodan\.io|zoomeye|netlas|file:)')

    def test_branded_database_and_protocol_references_are_distinct(self):
        postgres = catalog_entry('postgresql').get('presentation', {})
        self.assertEqual(postgres.get('icon_kind'), 'brand')
        self.assertEqual(postgres.get('documentation_url'), 'https://www.postgresql.org/docs/')
        self.assertEqual(postgres.get('download_url'), 'https://www.postgresql.org/download/')
        for key in ('nfs', 'smb', 'modbus', 'ftp', 'plc', 'dirs', 'keys', 'amazons3be', 'amazonbe'):
            with self.subTest(key=key):
                presentation = catalog_entry(key).get('presentation', {})
                self.assertEqual(presentation.get('icon_kind'), 'protocol')
                self.assertIsNone(presentation.get('download_url'))
        # Screenshot-only clues cannot identify an official project or download.
        for key in ('rockware_magicimm', 'pump_status_hmi'):
            presentation = catalog_entry(key).get('presentation', {})
            self.assertEqual(presentation.get('icon_kind'), 'generic')
            self.assertIsNone(presentation.get('documentation_url'))
            self.assertIsNone(presentation.get('download_url'))

    def test_common_products_use_recognizable_upstream_logos(self):
        for key in ('qdrant', 'weaviate', 'questdb', 'memcached'):
            with self.subTest(key=key):
                self.assertEqual(catalog_entry(key)['presentation']['icon_kind'], 'brand')

    def test_vendor_profiles_use_vendor_presentation_and_return_fresh_values(self):
        delta = catalog_entry('delta_as228t_a').get('presentation', {})
        self.assertTrue(delta.get('homepage_url', '').startswith('https://www.deltaww.com/'))
        self.assertNotEqual(delta, catalog_entry('modbus').get('presentation'))
        row = catalog_entry('postgresql')
        self.assertIn('presentation', row)
        row['presentation']['documentation_url'] = 'https://example.invalid/'
        self.assertEqual(catalog_entry('postgres')['presentation']['documentation_url'], 'https://www.postgresql.org/docs/')

    def test_artwork_has_provenance_and_no_active_or_remote_content(self):
        rows = catalog()
        self.assertTrue((ICON_ROOT / 'manifest.json').is_file())
        manifest = json.loads((ICON_ROOT / 'manifest.json').read_text())
        self.assertEqual(set(manifest['catalog_keys']), {row['key'] for row in rows})
        self.assertTrue((ICON_ROOT / 'LICENSE-SIMPLE-ICONS.txt').is_file())
        for row in rows:
            name = row['presentation']['icon_url'].rsplit('/', 1)[1]
            with self.subTest(key=row['key'], asset=name):
                entry = manifest['assets'][name]
                self.assertIn('source', entry)
                self.assertIn('license', entry)
                self.assertEqual(entry['kind'], row['presentation']['icon_kind'])
                data = (ICON_ROOT / name).read_bytes()
                self.assertLess(len(data), 100000)
                if name.endswith('.jpg'):
                    self.assertTrue(data.startswith(bytes.fromhex('ffd8ff')))
                else:
                    source = data.decode()
                    root = ElementTree.fromstring(source)
                    self.assertTrue(root.tag.endswith('svg'))
                    self.assertNotRegex(source.lower(), r'<(?:script|foreignobject|image)\b|\bon\w+\s*=')
                    # Local clipping/gradient references are inert; remote resources are forbidden.
                    for element in root.iter():
                        for attribute, value in element.attrib.items():
                            if attribute.endswith('href'):
                                self.assertTrue(value.startswith('#'))
                            for reference in re.findall(r'url\(([^)]+)\)', value):
                                self.assertTrue(reference.strip('\"\'').startswith('#'))
                    for reference in re.findall(r'url\(([^)]+)\)', source):
                        self.assertTrue(reference.strip('\"\'').startswith('#'))
                self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
