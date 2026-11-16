"""Render a private SearXNG configuration for the official Brave Search API."""
import argparse
import json
import os
from pathlib import Path
import tempfile


def render_settings(key_file, output):
    key = Path(key_file).read_text().strip()
    if not key or len(key) > 4096 or any(ord(char) < 33 for char in key):
        raise ValueError('Brave API key file must contain one valid key')
    value = {
        'use_default_settings': {'engines': {'keep_only': ['braveapi']}},
        'general': {'debug': False, 'instance_name': 'LeakScope research'},
        'search': {'safe_search': 0, 'autocomplete': '', 'formats': ['html', 'json']},
        'server': {'secret_key': 'set-through-SEARXNG_SECRET', 'bind_address': '0.0.0.0',
                   'port': 8080, 'limiter': False, 'public_instance': False, 'image_proxy': False},
        'outgoing': {'request_timeout': 8.0, 'max_request_timeout': 12.0},
        'engines': [{'name': 'braveapi', 'engine': 'braveapi', 'api_key': key,
                     'disabled': False, 'inactive': False, 'results_per_page': 10}],
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=output.parent, prefix='.search-', delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary: temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try: render_settings(args.key_file, args.output)
    except (OSError, ValueError): raise SystemExit('Search configuration failed; check private input paths/key format.') from None
    print('Private SearXNG settings written; key contents omitted.')
