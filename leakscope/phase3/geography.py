"""Local MMDB lookup and source-bound location estimates; no HTTP or DNS."""
from datetime import datetime, timezone
import ipaddress
import json
import math
from pathlib import Path
import re
from threading import RLock
import unicodedata

from django.conf import settings

PROVIDERS = ('shodan', 'zoomeye', 'netlas')
# ISO3166 alpha2 identifiers plus the explicit GeoIP Kosovo extension XK.
COUNTRY_CODES = frozenset("AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM ZW".split())
CONTINENTS = {'AF':'Africa','AN':'Antarctica','AS':'Asia','EU':'Europe','NA':'North America','OC':'Oceania','SA':'South America'}
_CONTINENT_ALIASES = {'africa':'AF','antarctica':'AN','asia':'AS','europe':'EU','northamerica':'NA','oceania':'OC','australia':'OC','southamerica':'SA',
    '非洲':'AF','南极洲':'AN','亚洲':'AS','欧洲':'EU','北美洲':'NA','大洋洲':'OC','南美洲':'SA'}
_CACHE = {}
_CACHE_LOCK = RLock()
MAX_ENDPOINTS = 5000


def _text(value, maximum=120):
    if not isinstance(value,str):return None
    value=''.join(c for c in value.strip() if ord(c)>=32 and ord(c)!=127)[:maximum]
    return value or None


def _name(value):
    if isinstance(value,str):return _text(value)
    if not isinstance(value,dict):return None
    names=value.get('names')
    if isinstance(names,dict):
        for key in ('en','zh-CN','fr','de','es'):
            if _text(names.get(key)):return _text(names[key])
    return _text(value.get('name'))


def _fold(value):
    value=unicodedata.normalize('NFKD',value or '').casefold()
    return ''.join(c for c in value if c.isalnum())


def canonical_ip(value, *, public_only=False):
    if isinstance(value,list):
        if not value or len(value)>32 or any(isinstance(item,list) for item in value):return None
        candidates={canonical_ip(item) for item in value}
        if None in candidates or len(candidates)!=1:return None
        return canonical_ip(next(iter(candidates)),public_only=public_only)
    if isinstance(value,bool) or not isinstance(value,(str,int)):return None
    if isinstance(value,str) and (len(value)>80 or '%' in value or value!=value.strip()):return None
    try:address=ipaddress.ip_address(value)
    except ValueError:return None
    if public_only and not address.is_global:return None
    return str(address)


def _raw_ip(raw):
    for name in ('ip_str','ip','resolved_ip','host'):
        value=raw.get(name)
        if value is not None:
            result=canonical_ip(value)
            if result or isinstance(value,list):return result
    return None


def observed_ip(endpoint_address, observation_data, public_only=True):
    """Only the saved IP address/metadata, with no current DNS resolution."""
    direct=canonical_ip(endpoint_address)
    if direct:return canonical_ip(direct,public_only=public_only)
    data=observation_data if isinstance(observation_data,dict) else {}
    metadata=data.get('metadata') if isinstance(data.get('metadata'),dict) else data
    for value in (metadata.get('resolved_ip'),(metadata.get('_geo') or {}).get('ip') if isinstance(metadata.get('_geo'),dict) else None,metadata.get('ip_str'),metadata.get('ip')):
        result=canonical_ip(value,public_only=public_only)
        if result:return result
    return None


def _coordinate_pair(latitude,longitude):
    try:
        if isinstance(latitude,bool) or isinstance(longitude,bool) or latitude is None or longitude is None:raise ValueError
        latitude=float(latitude);longitude=float(longitude)
        if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90<=latitude<=90 or not -180<=longitude<=180:raise ValueError
    except (TypeError,ValueError,OverflowError):return None,None
    return latitude,longitude


def _radius(value, divisor=1):
    if isinstance(value,bool):return None
    try:
        value=float(value)/divisor
        return value if math.isfinite(value) and 0<=value<=40075 else None
    except (TypeError,ValueError,OverflowError):return None


def _date(value):
    if isinstance(value,datetime):parsed=value
    elif isinstance(value,str) and len(value)<=80:
        try:parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:return None
    else:return None
    return parsed.isoformat() if parsed.tzinfo is not None else None


def _country_code(value):
    if not isinstance(value,str):return None
    value=value.strip()
    return value.upper() if value.upper() in COUNTRY_CODES else None


def _continent(code=None,name=None):
    code=code.strip().upper() if isinstance(code,str) else None
    if code not in CONTINENTS:code=_CONTINENT_ALIASES.get(_fold(name))
    return (code,CONTINENTS[code]) if code in CONTINENTS else (None,None)


def _empty(source,ip=None,status='not_collected'):
    return {'source':source,'status':status,'ip':ip,'country_code':None,'country_name':None,
        'continent_code':None,'continent_name':None,'region':None,'city':None,'latitude':None,'longitude':None,
        'accuracy_radius_km':None,'source_date':None}


def _normalized(value,provider):
    result=_empty(provider,canonical_ip(value.get('ip')))
    result['country_code']=_country_code(value.get('country_code'));result['country_name']=_text(value.get('country_name'))
    result['continent_code'],result['continent_name']=_continent(value.get('continent_code'),value.get('continent_name'))
    for name in ('region','city'):result[name]=_text(value.get(name))
    result['latitude'],result['longitude']=_coordinate_pair(value.get('latitude'),value.get('longitude'))
    result['accuracy_radius_km']=_radius(value.get('accuracy_radius_km'));result['source_date']=_date(value.get('source_date'))
    result['status']='located' if result['latitude'] is not None else 'country_only' if result['country_code'] or result['country_name'] else 'not_collected'
    return result


def extract_provider_geo(raw,provider):
    """Compact allowlisted IP geolocation, deliberately excluding contacts.geo."""
    if provider not in PROVIDERS or not isinstance(raw,dict):return None
    for name in ('_source','data'):
        if isinstance(raw.get(name),dict) and any(key in raw[name] for key in ('geo','geoinfo','location','ip','ip_str')):raw=raw[name];break
    if isinstance(raw.get('_geo'),dict):
        estimate=_normalized(raw['_geo'],provider)
        binding=_raw_ip(raw)
        ambiguous=any(isinstance(raw.get(key),list) and canonical_ip(raw[key]) is None for key in ('ip_str','ip','resolved_ip'))
        if ambiguous or (binding is not None and estimate['ip']!=binding):return None
        return estimate
    value={'ip':_raw_ip(raw),'source_date':next((_date(raw.get(key)) for key in ('@timestamp','timestamp','update_time','last_updated') if _date(raw.get(key))),None)}
    if provider=='shodan':
        location=raw.get('location')
        if not isinstance(location,dict):return None
        value.update(country_code=location.get('country_code'),country_name=location.get('country_name'),city=location.get('city'),region=location.get('region_code'),
            latitude=location.get('latitude'),longitude=location.get('longitude'))
    elif provider=='netlas':
        geo=raw.get('geo')
        if not isinstance(geo,dict):return None
        location=geo.get('location') if isinstance(geo.get('location'),dict) else {}
        country=geo.get('country');subdivisions=geo.get('subdivisions')
        value.update(country_code=country if isinstance(country,str) else country.get('iso_code') if isinstance(country,dict) else None,
            country_name=geo.get('country_name') or _name(country) if isinstance(country,dict) else geo.get('country_name'),
            continent_name=_name(geo.get('continent')),city=_name(geo.get('city')),region=_name(subdivisions[0]) if isinstance(subdivisions,list) and subdivisions else _name(subdivisions),
            latitude=location.get('lat'),longitude=location.get('lon'),accuracy_radius_km=_radius(geo.get('accuracy'),1000))
    else:
        geo=raw.get('geoinfo')
        if isinstance(geo,dict):
            location=geo.get('location') if isinstance(geo.get('location'),dict) else {};country=geo.get('country') or {};continent=geo.get('continent') or {}
            value.update(country_code=country.get('code') if isinstance(country,dict) else None,country_name=_name(country),continent_code=continent.get('code') if isinstance(continent,dict) else None,
                continent_name=_name(continent),city=_name(geo.get('city')),region=_name(geo.get('subdivisions')),latitude=location.get('lat'),longitude=location.get('lon'))
        elif any(key in raw for key in ('country.name','continent.name','city.name','lat','lon')):
            value.update(country_code=raw.get('country.code'),country_name=_name(raw.get('country.name')),continent_code=raw.get('continent.code'),continent_name=_name(raw.get('continent.name')),
                city=_name(raw.get('city.name')),region=_name(raw.get('province.name')) or _name(raw.get('subdivisions.name')),latitude=raw.get('lat'),longitude=raw.get('lon'))
        else:return None
    return _normalized(value,provider)


def _open_reader(path):
    import maxminddb
    return maxminddb.open_database(str(path))


def _database_unlocked():
    configured=getattr(settings,'LEAKSCOPE_GEOIP_FILE','')
    source=_text(getattr(settings,'LEAKSCOPE_GEOIP_SOURCE','DB-IP City Lite')) or 'Local GeoIP'
    status={'status':'not_installed','source':source,'version':None,'updated_at':None,'installed_at':None,'database_type':None,'sha256':None,'network_contact':False}
    if not isinstance(configured,(str,Path)) or not str(configured):return None,status
    path=Path(configured)
    try:
        file_stat=path.stat()
        if not path.is_file() or file_stat.st_size<1:return None,status
        sidecar=Path(str(path)+'.json')
        try:side_stat=sidecar.stat();side_identity=(side_stat.st_mtime_ns,side_stat.st_size)
        except OSError:side_identity=None
        key=(str(path.resolve()),file_stat.st_ino,file_stat.st_mtime_ns,file_stat.st_size,side_identity)
    except OSError:return None,status
    cached=_CACHE.get(key)
    if cached:return cached
    try:
        reader=_open_reader(path);metadata=reader.metadata();epoch=getattr(metadata,'build_epoch',None)
        built=datetime.fromtimestamp(epoch,timezone.utc).isoformat() if isinstance(epoch,(int,float)) and not isinstance(epoch,bool) and 0<=epoch<4102444800 else None
        status.update(status='ready',database_type=_text(getattr(metadata,'database_type',None)),updated_at=built,version=built)
        if status['database_type'] and ('GeoLite' in status['database_type'] or 'GeoIP' in status['database_type']):status['source']=status['database_type']
        try:
            if side_identity and side_identity[1]<=16384:
                data=json.loads(sidecar.read_text())
                if isinstance(data,dict):
                    status.update(source=_text(data.get('source')) or status['source'],version=_text(data.get('version')) or status['version'],installed_at=_date(data.get('installed_at')))
                    digest=data.get('sha256')
                    if isinstance(digest,str) and re.fullmatch('[a-fA-F0-9]{64}',digest):status['sha256']=digest.lower()
        except (OSError,ValueError,UnicodeError):pass
    except ImportError:return None,dict(status,status='dependency_missing')
    except (OSError,ValueError,TypeError,RuntimeError,AttributeError,OverflowError):return None,dict(status,status='unavailable')
    # Lookups and replacement use the same lock; no active lookup loses its mmap.
    for old in list(_CACHE):
        if old[0]==key[0] or len(_CACHE)>=4:
            old_reader,_=_CACHE.pop(old)
            try:old_reader.close()
            except (OSError,ValueError):pass
    _CACHE[key]=(reader,status)
    return reader,status


def database_status():
    with _CACHE_LOCK:return dict(_database_unlocked()[1])


def _local_record(ip,reader,database):
    address=canonical_ip(ip)
    result=_empty(database['source'],address)
    if address is None:return dict(result,status='no_ip')
    if not canonical_ip(address,public_only=True):return dict(result,status='not_public')
    if reader is None:return dict(result,status=database['status'])
    try:record=reader.get(address)
    except (OSError,ValueError,TypeError,RuntimeError):return dict(result,status='unavailable')
    if not isinstance(record,dict):return dict(result,status='not_found')
    country=record.get('country') if isinstance(record.get('country'),dict) else {}
    continent=record.get('continent') if isinstance(record.get('continent'),dict) else {}
    location=record.get('location') if isinstance(record.get('location'),dict) else {}
    subdivisions=record.get('subdivisions')
    value={'ip':address,'country_code':country.get('iso_code'),'country_name':_name(country),'continent_code':continent.get('code'),'continent_name':_name(continent),
        'city':_name(record.get('city')),'region':_name(subdivisions[0]) if isinstance(subdivisions,list) and subdivisions else None,
        'latitude':location.get('latitude'),'longitude':location.get('longitude'),'accuracy_radius_km':location.get('accuracy_radius'),'source_date':database['updated_at']}
    result=_normalized(value,database['source'])
    names=country.get('names')
    if isinstance(names,dict):result['country_aliases']=list(dict.fromkeys(item for item in (_text(name) for name in names.values()) if item))[:16]
    return result


def lookup_ips(ips):
    addresses=list(dict.fromkeys(ips))
    if len(addresses)>MAX_ENDPOINTS:raise ValueError('At most5000 retained IPs may be looked up per request')
    with _CACHE_LOCK:
        reader,status=_database_unlocked()
        return {ip:_local_record(ip,reader,status) for ip in addresses}


def lookup_ip(ip):return lookup_ips([ip])[ip]


def compare_estimates(local,provider):
    result={'status':'not_comparable','country_agreement':None,'distance_km':None,'within_local_accuracy':None,'within_combined_accuracy':None,'exact_location_confirmed':False}
    if not isinstance(local,dict) or not isinstance(provider,dict) or not local.get('ip') or local.get('ip')!=provider.get('ip') or provider.get('status') in {'not_collected','ip_mismatch'}:return result
    a,b=local.get('country_code'),provider.get('country_code')
    if a and b:result['country_agreement']=a==b
    elif provider.get('country_name') and _fold(provider['country_name']) in {_fold(name) for name in [local.get('country_name'),*local.get('country_aliases',[])] if name}:result['country_agreement']=True
    lat1,lon1=_coordinate_pair(local.get('latitude'),local.get('longitude'));lat2,lon2=_coordinate_pair(provider.get('latitude'),provider.get('longitude'))
    if lat1 is not None and lat2 is not None:
        first,second=math.radians(lat1),math.radians(lat2);delta=math.radians(lon2-lon1)
        hav=math.sin((second-first)/2)**2+math.cos(first)*math.cos(second)*math.sin(delta/2)**2
        distance=6371.0088*2*math.asin(math.sqrt(min(1,max(0,hav))))
        result['distance_km']=round(distance,3)
        left=_radius(local.get('accuracy_radius_km'));right=_radius(provider.get('accuracy_radius_km'))
        if left is not None:result['within_local_accuracy']=distance<=left
        if left is not None and right is not None:result['within_combined_accuracy']=distance<=left+right
    if result['country_agreement'] is True:result['status']='country_agrees'
    elif result['country_agreement'] is False:result['status']='country_differs'
    elif result['distance_km'] is not None:result['status']='distance_only'
    return result


_PROJECTION_PATHS = [(key,) for key in ('resolved_ip','ip','ip_str','host','country.name','continent.name','subdivisions.name','province.name','city.name','lat','lon','country.code','continent.code','timestamp','@timestamp','update_time','last_updated')]
_PROJECTION_PATHS += [('_geo',key) for key in ('ip','country_code','country_name','continent_code','continent_name','region','city','latitude','longitude','accuracy_radius_km','source_date')]
_PROJECTION_PATHS += [('location',key) for key in ('country_code','country_name','region_code','city','latitude','longitude')]
_PROJECTION_PATHS += [('geo',key) for key in ('country','country_name','continent','city','subdivisions','accuracy')]
_PROJECTION_PATHS += [('geo','location',key) for key in ('lat','lon')]
_PROJECTION_PATHS += [('geoinfo',key,'code') for key in ('country','continent')]
_PROJECTION_PATHS += [('geoinfo',key,'names',language) for key in ('country','continent','city','subdivisions') for language in ('en','zh-CN')]
_PROJECTION_PATHS += [('geoinfo','location',key) for key in ('lat','lon')]


def _saved_rows(endpoints):
    from django.db.models import F, Window
    from django.db.models.functions import RowNumber, Substr
    from django.db.models.fields.json import KeyTextTransform
    from leakscope_app.models import ServiceObservation
    ids=[row.pk for row in endpoints]
    columns=['id','endpoint_id','provider','observed_at','last_observed_at','first_collected_at','last_collected_at']
    projections={f'geo_{index}':Substr(KeyTextTransform.from_lookup('__'.join(('data','metadata',*path))),1,256) for index,path in enumerate(_PROJECTION_PATHS)}
    columns.extend(projections)
    rows=ServiceObservation.objects.filter(endpoint_id__in=ids,hidden=False).exclude(provider='certstream').annotate(**projections)
    latest=list(rows.annotate(position=Window(expression=RowNumber(),partition_by=[F('endpoint_id')],order_by=[F('last_collected_at').desc(),F('pk').desc()])).filter(position=1).values(*columns))
    providers=list(rows.filter(provider__in=PROVIDERS).annotate(position=Window(expression=RowNumber(),partition_by=[F('endpoint_id'),F('provider')],order_by=[F('last_collected_at').desc(),F('pk').desc()])).filter(position=1).values(*columns))
    return latest,providers


def _metadata(row):
    result={}
    for index,path in enumerate(_PROJECTION_PATHS):
        value=row.get(f'geo_{index}')
        if value is None or value=='null':continue
        if isinstance(value,str) and value[:1] in ('[','{'):
            try:value=json.loads(value)
            except ValueError:continue
        elif path[-1]=='ip' and isinstance(value,str) and value.isdecimal():
            try:value=int(value)
            except ValueError:continue
        target=result
        for part in path[:-1]:
            if not isinstance(target.get(part),dict):target[part]={}
            target=target[part]
        target[path[-1]]=value
    return result


def _provider_record(row,ip):
    provider=row['provider'];value=extract_provider_geo(_metadata(row),provider) or _empty(provider)
    value.update(observation_id=str(row['id']),source_date=_date(row.get('last_observed_at') or row.get('observed_at')) or value.get('source_date'),collected_at=_date(row['last_collected_at']))
    if value['ip'] is None:value.update(status='not_collected',reason='Provider IP/location binding was not retained')
    elif ip is not None and value['ip']!=ip:value.update(status='ip_mismatch',reason='Provider estimate describes a different retained IP')
    return value


def endpoint_locations(endpoints, *, source='local'):
    if source not in {'local',*PROVIDERS}:raise ValueError('Unknown location source')
    endpoints=list(endpoints)
    if len(endpoints)>MAX_ENDPOINTS:raise ValueError('At most5000 endpoints may be geolocated per request')
    if not endpoints:return {}
    latest,provider_rows=_saved_rows(endpoints);last={row['endpoint_id']:row for row in latest};by_endpoint={}
    for row in provider_rows:by_endpoint.setdefault(row['endpoint_id'],{})[row['provider']]=row
    ips={}
    for row in endpoints:
        candidates=[(by_endpoint.get(row.pk,{}) or {}).get(source,{})] if source in PROVIDERS else [last.get(row.pk,{}),*by_endpoint.get(row.pk,{}).values()]
        candidates=sorted(candidates,key=lambda value:(_date(value.get('last_collected_at')) or '',str(value.get('id',''))),reverse=True)
        ips[row.pk]=next((ip for item in candidates if (ip:=observed_ip(row.address,{'metadata':_metadata(item)},public_only=False))),None)
        if not candidates:ips[row.pk]=canonical_ip(row.address)
    local_values=lookup_ips([ip for ip in dict.fromkeys(ips.values()) if ip is not None])
    output={}
    for endpoint in endpoints:
        ip=ips[endpoint.pk];local=local_values.get(ip) or _empty('Local GeoIP',None,'no_ip')
        providers={provider:_provider_record(by_endpoint[endpoint.pk][provider],ip) if provider in by_endpoint.get(endpoint.pk,{}) else _empty(provider,ip) for provider in PROVIDERS}
        value=local if source=='local' else providers[source]
        location=value if value['status'] in {'located','country_only'} and value.get('ip')==ip else None
        output[str(endpoint.pk)]={'ip':ip,'location':location,'local':local,'providers':providers,'comparisons':{provider:compare_estimates(local,providers[provider]) for provider in PROVIDERS}}
    return output


def matches_area(location,*,country='',continent=''):
    location=location or {}
    def matches(value,code,name):
        if not value:return True
        present={str(location.get(code) or '').casefold(),str(location.get(name) or '').casefold()}-{''}
        if value.casefold()=='unknown':return not present
        return value.casefold() in present
    return matches(country,'country_code','country_name') and matches(continent,'continent_code','continent_name')


def filter_local_area(endpoints,*,country='',continent='',limit=5000):
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=MAX_ENDPOINTS:raise ValueError('Invalid geographic sample limit')
    rows=list(endpoints.only('id','address','port','transport','service_type','title').order_by('-last_seen','-pk')[:limit+1]);truncated=len(rows)>limit;rows=rows[:limit]
    values=endpoint_locations(rows);selected=[row.pk for row in rows if matches_area(values[str(row.pk)]['location'],country=country,continent=continent)]
    return endpoints.filter(pk__in=selected),{'geography_sampled_endpoints':len(rows),'geography_sample_limit':limit,'geography_truncated':truncated,
        'geography_basis':'Local GeoIP over a bounded saved-endpoint sample; no provider fallback or DNS/HTTP lookup.'}
