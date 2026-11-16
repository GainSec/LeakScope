import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CaptureToolsDeploymentTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('capture_tools', ROOT / 'deploy/capture_tools.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_both_supported_architectures_use_matching_pinned_assets(self):
        module = self.module()
        for architecture in ['amd64', 'arm64']:
            assets = module.assets_for(architecture)
            self.assertEqual(set(assets), {'httpx', 'gowitness'})
            for url, digest in assets.values():
                self.assertIn(architecture, url)
                self.assertEqual(len(digest), 64)
        self.assertNotEqual(module.assets_for('amd64'), module.assets_for('arm64'))

    def test_unsupported_architecture_is_rejected_before_download(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.module().assets_for('riscv64')

    def test_bad_checksum_does_not_write_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'httpx'
            with self.assertRaisesRegex(ValueError, 'checksum'):
                self.module().install_asset('httpx', b'wrong download', '0' * 64, target)
            self.assertFalse(target.exists())

    def test_verified_binary_is_executable_and_zip_only_extracts_named_tool(self):
        import io
        import zipfile
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'gowitness'
            data = b'fixture binary'
            module.install_asset('gowitness', data, hashlib.sha256(data).hexdigest(), target)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('httpx', data)
                archive.writestr('../escape', b'bad')
            packed = stream.getvalue()
            module.install_asset('httpx', packed, hashlib.sha256(packed).hexdigest(), Path(folder) / 'httpx')
            self.assertEqual((Path(folder) / 'httpx').read_bytes(), data)
            self.assertFalse((Path(folder).parent / 'escape').exists())


class CaptureEntrypointTests(unittest.TestCase):
    """The fetch worker must start again after an unclean stop."""
    def test_stale_display_lock_is_removed_before_the_display_starts(self):
        import os, shutil, subprocess, tempfile
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'deploy' / 'browser-capture-entrypoint.sh').read_text()
        self.assertLess(source.index('rm -f "/tmp/.X${display_number}-lock"'), source.index('Xvfb "$DISPLAY"'))
        if not shutil.which('sh'):
            self.skipTest('no POSIX shell')
        # Run only the lock-clearing lines against a scratch directory.
        start = source.index('display_number="${DISPLAY#*:}"'); end = source.index('Xvfb "$DISPLAY"')
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / '.X99-lock'; other = Path(folder) / '.X7-lock'
            lock.write_text('1\n'); other.write_text('1\n')
            script = source[start:end].replace('/tmp/', folder + '/')
            for display, expected in ((':99', (False, True)), (':99.0', (False, True)), ('bad;rm', (True, True))):
                lock.write_text('1\n')
                subprocess.run(['sh', '-eu', '-c', script], check=True, env=dict(os.environ, DISPLAY=display))
                self.assertEqual((lock.exists(), other.exists()), expected, display)

