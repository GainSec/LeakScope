#!/usr/bin/env python3
"""Install a public DB-IP City Lite release locally; no IP addresses leave the host."""
import argparse,gzip,hashlib,json,os,re,tempfile,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,urlopen

MAX_COMPRESSED=200*1024*1024
MAX_DECODED=512*1024*1024


def install(directory,release,*,opener=urlopen):
    if not re.fullmatch(r'20[2-9][0-9]-(?:0[1-9]|1[0-2])',release):raise ValueError('Use a YYYY-MM release')
    import maxminddb
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    source=f'https://download.db-ip.com/free/dbip-city-lite-{release}.mmdb.gz'
    start=time.monotonic();download=None;decoded=None
    try:
        with tempfile.NamedTemporaryFile(dir=directory,suffix='.gz.part',delete=False) as stream:
            download=Path(stream.name);received=0;compressed_hash=hashlib.sha256()
            # Ordinary GET: the download server rejects HEAD requests.
            with opener(Request(source,headers={'User-Agent':'LeakScope local geolocation installer'}),timeout=30) as response:
                if response.status!=200:raise ValueError('Dataset download did not return a complete archive')
                while chunk:=response.read(1024*1024):
                    received+=len(chunk)
                    if received>MAX_COMPRESSED or time.monotonic()-start>900:raise ValueError('Dataset download limit exceeded')
                    stream.write(chunk);compressed_hash.update(chunk)
            stream.flush();os.fsync(stream.fileno())
        with tempfile.NamedTemporaryFile(dir=directory,suffix='.mmdb.part',delete=False) as stream:
            decoded=Path(stream.name);size=0;digest=hashlib.sha256()
            with gzip.open(download,'rb') as zipped:
                while chunk:=zipped.read(1024*1024):
                    size+=len(chunk)
                    if size>MAX_DECODED:raise ValueError('Decoded dataset limit exceeded')
                    stream.write(chunk);digest.update(chunk)
            stream.flush();os.fsync(stream.fileno())
        with maxminddb.open_database(str(decoded)) as reader:
            info=reader.metadata()
            if info.ip_version not in (4,6) or info.node_count<100:raise ValueError('Invalid local geolocation database')
            database_type=info.database_type
        decoded.chmod(0o644);os.replace(decoded,directory/'city.mmdb');decoded=None
        metadata={'source':'DB-IP City Lite','version':release,'installed_at':datetime.now(timezone.utc).isoformat(),
            'source_url':source,'sha256':digest.hexdigest(),'archive_sha256':compressed_hash.hexdigest(),
            'bytes':size,'download_bytes':received,'database_type':database_type,'attribution':'IP Geolocation by DB-IP','attribution_url':'https://db-ip.com'}
        sidecar=directory/'city.mmdb.json';tmp=sidecar.with_suffix('.json.part');tmp.write_text(json.dumps(metadata,indent=2)+'\n');tmp.chmod(0o644);os.replace(tmp,sidecar)
        return metadata
    finally:
        if download:download.unlink(missing_ok=True)
        if decoded:decoded.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',required=True);parser.add_argument('--release',default=datetime.now(timezone.utc).strftime('%Y-%m'));args=parser.parse_args()
    print(json.dumps(install(args.directory,args.release),indent=2))
