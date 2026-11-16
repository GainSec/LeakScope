"""Safe local screenshot derivatives and retained-capture protection."""
from io import BytesIO
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import warnings
from django.db.models import Q
from leakscope_app.models import EvidenceArtifact
from .local_correlations import _read_artifact, PIXEL_LIMIT

THUMBNAIL_BYTES=2*1024*1024


def protected_artifacts():
    return EvidenceArtifact.objects.filter(Q(curation__pinned=True)|Q(collection_memberships__isnull=False)).distinct()


def thumbnail_path(row):
    return 'gallery/'+row.pk.hex+'-'+str(row.sha256).lower()+'.png'


def render_thumbnail(row):
    from PIL import Image,ImageOps
    data=_read_artifact(row)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(data),formats=('PNG','JPEG','WEBP')) as image:
                if image.width<1 or image.height<1 or image.width*image.height>PIXEL_LIMIT:raise ValueError('pixel_limit')
                image.load();image=ImageOps.exif_transpose(image)
                width,height=image.size
                if row.kind=='evidence-render':
                    # A drawn document is tall; preview its heading, not a shrunken page.
                    from .evidence_render import POSTER_HEIGHT
                    image=image.crop((0,0,width,min(height,POSTER_HEIGHT)))
                image.thumbnail((480,300),Image.Resampling.LANCZOS)
                image=image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
                out=BytesIO();image.save(out,format='PNG');data=out.getvalue()
                if len(data)>THUMBNAIL_BYTES:raise ValueError('thumbnail_limit')
                return {'bytes':data,'width':width,'height':height,'thumbnail_width':image.width,'thumbnail_height':image.height}
    except (Image.DecompressionBombWarning,Image.DecompressionBombError):
        raise ValueError('pixel_limit') from None


def store_thumbnail(row,rendered):
    from .jobs import capture_root
    data=rendered['bytes']
    if not isinstance(data,bytes) or not 0<len(data)<=THUMBNAIL_BYTES:raise ValueError('thumbnail_limit')
    parent=os.open(capture_root(),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    folder=None;temporary=None
    try:
        try:os.mkdir('gallery',mode=0o750,dir_fd=parent)
        except FileExistsError:pass
        folder=os.open('gallery',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        temporary='tmp-'+uuid4().hex
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640,dir_fd=folder)
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        filename=Path(thumbnail_path(row)).name
        os.replace(temporary,filename,src_dir_fd=folder,dst_dir_fd=folder);temporary=None
    finally:
        if folder is not None:
            if temporary is not None:
                try:os.unlink(temporary,dir_fd=folder)
                except OSError:pass
            os.close(folder)
        os.close(parent)
    return {'path':thumbnail_path(row),'source_sha256':row.sha256,'thumbnail_sha256':hashlib.sha256(data).hexdigest(),
        'bytes':len(data),'width':rendered['width'],'height':rendered['height'],
        'thumbnail_width':rendered['thumbnail_width'],'thumbnail_height':rendered['thumbnail_height'],'state':'ready'}


def capture_metadata(row):
    return row.metadata if isinstance(row.metadata,dict) else {}


def thumbnail_metadata(row):
    value=capture_metadata(row).get('gallery')
    if not isinstance(value,dict) or value.get('state')!='ready' or value.get('source_sha256')!=row.sha256 or value.get('path')!=thumbnail_path(row):return None
    if any(type(value.get(key)) is not int or not 0<value[key]<=PIXEL_LIMIT for key in ['width','height','thumbnail_width','thumbnail_height']):return None
    if not isinstance(value.get('thumbnail_sha256'),str) or len(value['thumbnail_sha256'])!=64:return None
    if type(value.get('bytes')) is not int or not 0<value['bytes']<=THUMBNAIL_BYTES:return None
    return value


def read_thumbnail(row):
    value=thumbnail_metadata(row)
    if value is None:raise ValueError('thumbnail_not_indexed')
    data=_read_artifact(SimpleNamespace(relative_path=value['path'],sha256=value['thumbnail_sha256']))
    if len(data)>THUMBNAIL_BYTES or len(data)!=value['bytes'] or not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('invalid_thumbnail')
    return data


def delete_derivative(row):
    """Called only after housekeeping has locked/rechecked the source artifact."""
    from .jobs import capture_root
    parent=os.open(capture_root(),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    folder=None
    try:
        try:folder=os.open('gallery',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        except FileNotFoundError:return
        try:os.unlink(Path(thumbnail_path(row)).name,dir_fd=folder)
        except FileNotFoundError:pass
    finally:
        if folder is not None:os.close(folder)
        os.close(parent)
