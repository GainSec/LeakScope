"""Install checksum-pinned capture tools for the container's native architecture."""
import hashlib
import io
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ASSETS = {
    'amd64': {
        'httpx': ('https://github.com/projectdiscovery/httpx/releases/download/v1.12.0/httpx_1.12.0_linux_amd64.zip', '9d8439e8b6c9aa7d1e2314817a392e00d5178da3af5652f7475f88868f418f76'),
        'gowitness': ('https://github.com/sensepost/gowitness/releases/download/3.2.2/gowitness-3.2.2-linux-amd64', '063ac21063ec96992a9bd7216bf05b3e96afa01a05e1a908490a2b42f573a12a'),
    },
    'arm64': {
        'httpx': ('https://github.com/projectdiscovery/httpx/releases/download/v1.12.0/httpx_1.12.0_linux_arm64.zip', 'fd7b123c1dfbc3d69f19f524e4eebcd6ec06b9a6cbd56813c76f11645197331e'),
        'gowitness': ('https://github.com/sensepost/gowitness/releases/download/3.2.2/gowitness-3.2.2-linux-arm64', '207f4abd2e6129f601346e44a3768e0549dd39a4498ee8602b1774b1a100c477'),
    },
}


def assets_for(architecture):
    if architecture not in ASSETS:
        raise ValueError(f'Unsupported capture architecture: {architecture}')
    return ASSETS[architecture]


def install_asset(name, data, expected, target):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'Pinned {name} checksum mismatch')
    if name == 'httpx':
        data = zipfile.ZipFile(io.BytesIO(data)).read('httpx')
    target = Path(target)
    target.write_bytes(data)
    target.chmod(0o755)


def main():
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    for name, (url, expected) in assets_for(architecture).items():
        request = urllib.request.Request(url, headers={'User-Agent': 'LeakScope-pinned-capture-build'})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        install_asset(name, data, expected, Path('/usr/local/bin') / name)


if __name__ == '__main__':
    main()
