#!/usr/bin/env python3
"""Create the private browser-controller credential before starting Compose."""
import argparse
import os
from pathlib import Path
import secrets


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',default=os.environ.get('LEAKSCOPE_DATA_ROOT','.runtime/docker'))
    parser.add_argument('--token-file',default=os.environ.get('LEAKSCOPE_BROWSER_TOKEN_SOURCE'))
    args=parser.parse_args()
    path=Path(args.token_file) if args.token_file else Path(args.data_root)/'app'/'browser-control-token'
    path=path.expanduser().resolve()
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        value=path.read_text().strip()
        if not 32<=len(value)<=256 or any(ord(c)<33 or ord(c)>126 for c in value):
            raise SystemExit('Existing browser credential is invalid; preserve or replace it deliberately before deployment')
        path.chmod(0o600)
        print('Existing browser credential retained.')
    else:
        with os.fdopen(fd,'w') as handle:
            handle.write(secrets.token_urlsafe(48)+'\n');handle.flush();os.fsync(handle.fileno())
        print('Private browser credential created. Keep it with deployment backups.')


if __name__=='__main__':main()
