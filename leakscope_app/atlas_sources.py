"""Local atlas assets/configuration and explicit Panoramax vicinity requests."""
from io import BytesIO
import json,math,os,tempfile,threading,time
from pathlib import Path
from urllib.parse import urlsplit,unquote
from uuid import UUID
import requests
from django.conf import settings
from django.http import JsonResponse,HttpResponse
from django.templatetags.static import static
from .workbench import endpoint,_body

IMAGE_LIMIT=12*1024*1024
CATALOG_LIMIT=2*1024*1024
_ASSETS={};_LOCK=threading.Lock()


def _config_path():return Path(settings.DATA_DIR)/'atlas-settings.json'


def _url(value):
    if not isinstance(value,str) or len(value)>1024 or any(ord(c)<33 for c in value):raise ValueError('Enter an HTTP(S) Panoramax API URL')
    try:parts=urlsplit(value);port=parts.port
    except ValueError:raise ValueError('Invalid imagery URL') from None
    if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:raise ValueError('Use an HTTP(S) API URL without credentials, query or fragment')
    return value.rstrip('/')


def _config():
    default={'enabled':True,'mode':'local','api_url':'http://panoramax:5000/api'}
    try:default['api_url']=_url(getattr(settings,'LEAKSCOPE_PANORAMAX_API_URL',default['api_url']))
    except (ValueError,TypeError):pass
    try:
        path=_config_path()
        if path.stat().st_size>4096:return default
        saved=json.loads(path.read_text())
        if isinstance(saved,dict) and set(saved)<={'enabled','mode','api_url'}:
            candidate={**default,**saved}
            if candidate['mode'] not in {'local','external'} or not isinstance(candidate['enabled'],bool):return default
            candidate['api_url']=_url(candidate['api_url'])
            return candidate
    except (OSError,ValueError,TypeError):pass
    return default


@endpoint(('GET','POST'))
def configuration(request):
    if request.method=='POST':
        value=_body(request)
        if not isinstance(value,dict) or value.keys()-{'api_url','mode','enabled'}:raise ValueError('Unsupported atlas configuration field')
        config=_config()
        if 'api_url' in value:config['api_url']=_url(value['api_url'])
        if 'mode' in value:
            if value['mode'] not in {'local','external'}:raise ValueError('Choose local or external imagery')
            config['mode']=value['mode']
        if 'enabled' in value:
            if not isinstance(value['enabled'],bool):raise ValueError('Enabled must be a boolean')
            config['enabled']=value['enabled']
        path=_config_path();path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as stream:
            temporary=Path(stream.name);json.dump(config,stream);stream.flush();os.fsync(stream.fileno())
        temporary.chmod(0o600);os.replace(temporary,path)
    return JsonResponse({'imagery':_config(),'basis':'Maps and IP geolocation are local. Imagery is requested only by an explicit vicinity action; external mode contacts the configured source.'})


def _manifest():
    return json.loads((Path(__file__).parent/'static/maps/manifest.json').read_text())


@endpoint()
def map_status(request):
    data=_manifest()
    return JsonResponse({'installed':True,'source':data['source'],'status':'ready','coverage':data['coverage'],
        'archive':{'name':'Country/continent boundaries','version':data['version'],'maxzoom':8,'minzoom':0,'bytes':(Path(__file__).parent/'static/maps/countries.geojson').stat().st_size},
        'style_url':'/api/atlas/maps/style','jobs':[],'attribution':'Made with Natural Earth. IP Geolocation by DB-IP.',
        'basis':'Compact locally bundled overview geometry; large street-detail datasets intentionally omitted.'})


@endpoint()
def map_style(request):
    return JsonResponse({'version':8,'name':'LeakScope local world overview','glyphs':'/static/maps/fonts/{fontstack}/{range}.pbf',
        'sources':{'countries':{'type':'geojson','data':static('maps/countries.geojson'),'attribution':'Made with Natural Earth'}},
        'layers':[{'id':'ocean','type':'background','paint':{'background-color':'#071321'}},
            {'id':'countries-fill','type':'fill','source':'countries','paint':{'fill-color':'#173747','fill-opacity':0.95}},
            {'id':'countries-borders','type':'line','source':'countries','paint':{'line-color':'#41757e','line-width':0.65,'line-opacity':0.8}},
            {'id':'country-labels','type':'symbol','source':'countries','minzoom':1,'layout':{'text-field':['get','name'],'text-font':['Noto Sans Regular'],'text-size':11,'text-max-width':9},'paint':{'text-color':'#accad2','text-halo-color':'#0c202b','text-halo-width':1.5}}]})


def _number(value,low,high,label):
    try:number=float(value)
    except (ValueError,TypeError):raise ValueError('Invalid '+label) from None
    if not math.isfinite(number) or not low<=number<=high:raise ValueError('Invalid '+label)
    return number


def _bytes(response,limit):
    result=bytearray();started=time.monotonic()
    from urllib3.exceptions import HTTPError
    try:
        if response.status_code!=200:raise ValueError('Imagery source did not return a successful response')
        # read1 avoids waiting for a full chunk while a peer trickles bytes.
        from urllib3.response import HTTPResponse
        raw=getattr(response,'raw',None)
        chunks=iter(lambda:raw.read1(65536,decode_content=True),b'') if isinstance(raw,HTTPResponse) else response.iter_content(chunk_size=65536)
        for chunk in chunks:
            if time.monotonic()-started>15:raise ValueError('Imagery response exceeded the total read deadline')
            if chunk:result.extend(chunk)
            if len(result)>limit:raise ValueError('Imagery response exceeds the local size limit')
    except HTTPError as exc:
        raise requests.RequestException('Imagery stream failed within local read limits') from exc
    finally:response.close()
    return bytes(result)


def _asset_url(value,base,identifier):
    if not isinstance(value,str) or len(value)>2048:return None
    try:
        a=urlsplit(value);b=urlsplit(base)
        origin=lambda part:(part.scheme,part.hostname,part.port or (443 if part.scheme=='https' else 80))
        if a.scheme not in {'http','https'} or a.username or a.password or a.query or a.fragment or origin(a)!=origin(b):return None
    except ValueError:return None
    parts=unquote(a.path).split('/')
    if identifier not in parts or '..' in parts:return None
    return value


def _distance(lat,lon,other_lat,other_lon):
    a,b=map(math.radians,[lat,other_lat]);delta=math.radians(other_lat-lat);lng=math.radians(other_lon-lon)
    return 6371000*2*math.asin(min(1,math.sqrt(math.sin(delta/2)**2+math.cos(a)*math.cos(b)*math.sin(lng/2)**2)))


@endpoint()
def imagery_nearby(request):
    lat=_number(request.GET.get('lat'),-90,90,'latitude');lon=_number(request.GET.get('lon'),-180,180,'longitude')
    radius=_number(request.GET.get('radius_m',3000),50,10000,'radius')
    config=_config();output={'source':'Panoramax','mode':config['mode'],'results':[],'count':0,'available':False,'coverage':'No supported imagery has been returned in this limited selection.',
        'basis':'Street-level imagery near an approximate IP estimate. This does not identify the actual server, building or owner.'}
    if not config['enabled']:return JsonResponse(dict(output,coverage='Street-level imagery is disabled in Atlas settings.'))
    base=_url(config['api_url']);dy=radius/111320;dx=min(180,dy/max(0.01,math.cos(math.radians(lat))))
    bbox=','.join(str(round(value,6)) for value in [max(-180,lon-dx),max(-90,lat-dy),min(180,lon+dx),min(90,lat+dy)])
    try:
        response=requests.get(base+'/search',params={'bbox':bbox,'limit':8},timeout=(3,8),allow_redirects=False,stream=True,headers={'Accept':'application/geo+json','User-Agent':'LeakScope local atlas'})
        payload=json.loads(_bytes(response,CATALOG_LIMIT))
        if not isinstance(payload,dict):raise ValueError('Malformed imagery catalog')
        features=payload.get('features',[])
        if not isinstance(features,list):raise ValueError('Malformed imagery catalog')
        result=[]
        for feature in features[:8]:
            try:
                identifier=str(UUID(str(feature['id'])));geometry=feature.get('geometry') or {};coords=geometry.get('coordinates')
                if geometry.get('type')!='Point' or not isinstance(coords,list) or len(coords)<2:continue
                lng=_number(coords[0],-180,180,'photo longitude');latitude=_number(coords[1],-90,90,'photo latitude')
                distance=_distance(lat,lon,latitude,lng)
                if distance>radius:continue
                assets=feature.get('assets') or {};props=feature.get('properties') or {}
                images={}
                for variant,names in [('thumb',['thumb','thumbnail']),('image',['sd','hd'])]:
                    for name in names:
                        asset=assets.get(name)
                        url=_asset_url(asset.get('href') if isinstance(asset,dict) else None,base,identifier)
                        if url:images[variant]=url;break
                if not images.get('image'):continue
                with _LOCK:
                    if len(_ASSETS)>=256:_ASSETS.pop(next(iter(_ASSETS)))
                    _ASSETS[(base,identifier)]=images
                orientation=props.get('pers:interior_orientation') or {};panotype=orientation.get('camera_type') if isinstance(orientation,dict) else None
                is_pano=props.get('view:type')=='equirectangular' or props.get('geovisio:image')=='equirectangular' or panotype in {'spherical','equirectangular'} or bool(props.get('panoramax:horizontal_pixel_density'))
                result.append({'id':identifier,'latitude':latitude,'longitude':lng,'distance_m':round(distance),'captured_at':str(props.get('datetime') or '')[:80],
                    'thumbnail_url':'/api/atlas/imagery/images/'+identifier+'?variant=thumb','image_url':'/api/atlas/imagery/images/'+identifier+'?variant=image','is_panorama':bool(is_pano),
                    'license':str(props.get('license') or 'Source license unspecified')[:100]})
            except (ValueError,TypeError,KeyError,AttributeError):continue
        result.sort(key=lambda row:row['distance_m']);output.update(available=True,results=result,count=len(result),coverage='Showing nearby pictures returned by the configured imagery source (up to eight catalog entries checked).' if result else 'No supported pictures returned in this limited selection; this does not establish complete coverage of the area.')
    except (requests.RequestException,ValueError,TypeError,RecursionError):output['coverage']='The configured Panoramax source is unavailable or returned unsupported imagery. Maps and local IP geolocation remain available.'
    return JsonResponse(output)


@endpoint()
def imagery_image(request,picture_id):
    from PIL import Image
    identifier=str(UUID(str(picture_id)));variant=request.GET.get('variant','image')
    if variant not in {'thumb','image'}:raise ValueError('Invalid image variant')
    config=_config();base=_url(config['api_url'])
    if not config['enabled']:return JsonResponse({'error':'Imagery is disabled'},status=404)
    with _LOCK:url=(_ASSETS.get((base,identifier)) or {}).get(variant)
    if not url:return JsonResponse({'error':'Load the nearby imagery selection again before opening this picture'},status=404)
    try:
        response=requests.get(url,timeout=(3,10),allow_redirects=False,stream=True,headers={'User-Agent':'LeakScope local atlas'})
        data=_bytes(response,IMAGE_LIMIT)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as picture:
                if picture.format not in {'JPEG','PNG','WEBP'} or picture.width*picture.height>16_000_000:raise ValueError('Unsupported image')
                picture.verify();mime={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}[picture.format]
        result=HttpResponse(data,content_type=mime);result['Cache-Control']='private, max-age=300';result['X-Content-Type-Options']='nosniff';return result
    except (requests.RequestException,OSError,ValueError,Warning,Image.DecompressionBombError):return JsonResponse({'error':'The selected imagery could not be loaded within local image limits'},status=502)
