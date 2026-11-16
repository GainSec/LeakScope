import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[1]

class PrivateSearchSettingsTests(unittest.TestCase):
    def test_key_rendering_is_private_and_enables_only_the_official_api(self):
        spec = importlib.util.spec_from_file_location('search_settings', ROOT/'deploy/firecrawl/configure_search.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder)/'key';key.write_text('fixture-private-key')
            output = Path(folder)/'settings.yml'
            captured = io.StringIO()
            with redirect_stdout(captured):module.render_settings(key, output)
            value = json.loads(output.read_text())
            self.assertEqual(value['engines'][0]['api_key'], 'fixture-private-key')
            self.assertEqual(value['use_default_settings']['engines']['keep_only'], ['braveapi'])
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('fixture-private-key', captured.getvalue())

    def test_invalid_key_does_not_create_a_config(self):
        spec = importlib.util.spec_from_file_location('search_settings', ROOT/'deploy/firecrawl/configure_search.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder)/'key';key.write_text('bad\nkey')
            output = Path(folder)/'settings.yml'
            with self.assertRaises(ValueError):module.render_settings(key, output)
            self.assertFalse(output.exists())
