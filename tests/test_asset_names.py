from django.test import SimpleTestCase


class AssetNameTests(SimpleTestCase):
    def test_domain_normalization_and_wildcards_are_explicit(self):
        from leakscope.phase3.asset_names import normalize_domain
        self.assertEqual(normalize_domain('BÜCHER.Example.'), 'xn--bcher-kva.example')
        self.assertEqual(normalize_domain('*.GainSec.com', allow_wildcard=True), '*.gainsec.com')
        for value in ['*.gainsec.com', 'https://gainsec.com/', 'gainsec.com:443', '../example.com', 'a..example.com', '192.0.2.1']:
            with self.subTest(value=value), self.assertRaises(ValueError): normalize_domain(value)

    def test_registered_domain_uses_offline_public_suffix_list(self):
        from leakscope.phase3.asset_names import registrable_domain
        self.assertEqual(registrable_domain('api.example.co.uk'), 'example.co.uk')
        self.assertEqual(registrable_domain('*.api.example.com.au'), 'example.com.au')
        self.assertEqual(registrable_domain('tenant.appspot.com'), 'tenant.appspot.com')

    def test_ip_normalization_rejects_scopes_urls_and_ports(self):
        from leakscope.phase3.asset_names import normalize_ip
        self.assertEqual(normalize_ip('[2001:db8::1]'), '2001:db8::1')
        for value in ['192.0.2.1:443', 'fe80::1%en0', True, 'http://192.0.2.1']:
            with self.subTest(value=value), self.assertRaises(ValueError): normalize_ip(value)
