from django.test import SimpleTestCase
from leakscope.phase3.catalog import catalog, catalog_entry
from leakscope.phase3.native import IDENTITY_TYPES


class ProtocolCatalogTests(SimpleTestCase):
    def test_catalog_prioritizes_database_storage_and_ot(self):
        rows=catalog()
        self.assertGreaterEqual(len(rows),60)
        self.assertEqual(len(rows),len({r['key'] for r in rows}))
        for category in ('database','storage','ot'):
            self.assertGreaterEqual(sum(r['category']==category for r in rows),10)
        for key in ('postgresql','redis','qdrant','nfs','smb','modbus','bacnet','s7','opcua','dnp3'):
            self.assertEqual(catalog_entry(key)['key'],key)

    def test_presets_are_explicitly_candidates_not_native_support(self):
        for row in catalog():
            self.assertEqual(row['detection_confidence'],'candidate')
            self.assertEqual(row['identity'],row.get('base_type',row['key']) in IDENTITY_TYPES)
            self.assertFalse(row['inspection'])
            self.assertFalse(row['export'])
            self.assertIn('Port-only',row['detection_notes'])
            self.assertIn(row['transport'],('tcp','udp'))
            self.assertTrue(all(isinstance(p,int) and 1<=p<=65535 for p in row['ports']))
            self.assertEqual(set(row['queries']), {'shodan','zoomeye','netlas'} if row['ports'] else set())

    def test_provider_presets_use_actual_port_grammar(self):
        row=catalog_entry('postgresql')
        self.assertEqual(row['queries']['shodan'],'port:5432')
        self.assertEqual(row['queries']['zoomeye'],'port="5432"')
        self.assertEqual(row['queries']['netlas'],'port:5432')
        multi=catalog_entry('mysql')['queries']
        self.assertIn(',',multi['shodan'])
        self.assertIn(' || ',multi['zoomeye'])
        self.assertIn(' OR ',multi['netlas'])

    def test_aliases_and_returned_data_are_isolated(self):
        self.assertEqual(catalog_entry('postgres')['key'],'postgresql')
        self.assertEqual(catalog_entry('mongodb')['key'],'mongo')
        row=catalog_entry('postgresql');row['ports'].append(123)
        row['queries']['netlas']='broken'
        self.assertNotIn(123,catalog_entry('postgresql')['ports'])
        self.assertEqual(catalog_entry('postgresql')['queries']['netlas'],'port:5432')
        with self.assertRaises(ValueError):catalog_entry('unknown')
