from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from leakscope_app.ui_bundles import BUNDLES, build_bundles


class WorkspaceBundlesTests(SimpleTestCase):
    def test_ordered_sources_preserve_script_boundaries_and_css_precedence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundles = {'js/test.js': ('js/a.js', 'js/b.js'), 'css/test.css': ('css/a.css', 'css/b.css')}
            for filename, text in [('js/a.js', '(function(){})()'), ('js/b.js', '(function(){})()'),
                                   ('css/a.css', 'a{color:red}'), ('css/b.css', 'a{color:blue}')]:
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            build_bundles(root, bundles)
            self.assertIn('(function(){})()\n;\n', (root / 'js/test.js').read_text())
            self.assertLess((root / 'css/test.css').read_text().index('color:red'),
                            (root / 'css/test.css').read_text().index('color:blue'))
            before = (root / 'js/test.js').stat().st_mtime_ns
            build_bundles(root, bundles)
            self.assertEqual(before, (root / 'js/test.js').stat().st_mtime_ns)

    def test_real_sources_exist_and_component_overrides_follow_the_shared_theme(self):
        root = Path(__file__).resolve().parents[1] / 'leakscope_app/static'
        for paths in BUNDLES.values():
            for path in paths:
                self.assertTrue((root / path).is_file(), path)
        self.assertIn('css/experience.css',BUNDLES['css/workspace_bundle.css'])
        self.assertEqual(BUNDLES['css/workspace_bundle.css'][-1], 'css/public_signals.css')
        self.assertTrue(BUNDLES['js/leakscope/workspace_bundle.js'][-1].endswith('/app.js'))
        scripts = BUNDLES['js/leakscope/workspace_bundle.js']
        self.assertLess(scripts.index('js/leakscope/preview.js'), scripts.index('js/leakscope/app.js'))

    def test_collectstatic_builds_bundles_before_manifest_processing(self):
        from unittest.mock import patch
        from leakscope_app.management.commands.collectstatic import Command
        calls = []
        with patch('leakscope_app.management.commands.collectstatic.build_bundles', side_effect=lambda: calls.append('build')) as build, \
             patch('django.contrib.staticfiles.management.commands.collectstatic.Command.handle', side_effect=lambda: calls.append('collect')) as collect:
            Command().handle()
        build.assert_called_once()
        collect.assert_called_once()
        self.assertEqual(calls, ['build', 'collect'])

    def test_django_discovers_building_command(self):
        from django.core.management import get_commands
        self.assertEqual(get_commands()['collectstatic'], 'leakscope_app')

    def test_debug_uses_source_files_and_production_uses_bundles(self):
        from leakscope_app.templatetags.workspace_assets import workspace_assets
        with self.settings(DEBUG=True):
            self.assertEqual(workspace_assets('css'), BUNDLES['css/workspace_bundle.css'])
        with self.settings(DEBUG=False):
            self.assertEqual(workspace_assets('js'), ('js/leakscope/workspace_bundle.js',))

    def test_actual_shell_selects_assets_and_preserves_geography_vendors(self):
        import re
        from django.template.loader import render_to_string
        for debug in (False, True):
            for page in ('findings', 'geography'):
                with self.settings(DEBUG=debug):
                    html = render_to_string('workbench/app.html', {'page': page})
                scripts = re.findall(r'<script defer src="([^"]+)"', html)
                styles = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
                self.assertEqual(len(scripts), (len(BUNDLES['js/leakscope/workspace_bundle.js']) if debug else 1) + (2 if page == 'geography' else 0))
                self.assertEqual(len(styles), (len(BUNDLES['css/workspace_bundle.css']) if debug else 1) + (2 if page == 'geography' else 0))
                self.assertIn('app.js' if debug else 'workspace_bundle.js', scripts[-1])
                self.assertIn('public_signals.css' if debug else 'workspace_bundle.css', styles[len(BUNDLES['css/workspace_bundle.css']) - 1 if debug else 0])
                if page == 'geography':
                    self.assertIn('maplibre-gl.js', scripts[0])
                    self.assertIn('viewer.js', scripts[1])
