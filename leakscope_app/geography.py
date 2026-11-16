"""Bounded local geography over visible retained endpoints and source estimates."""
from collections import Counter
from functools import lru_cache
import re
from uuid import UUID
from django.http import JsonResponse
from leakscope.phase3 import geography as geo
from leakscope.phase3.catalog import CATEGORY_LABELS, catalog
from leakscope.services.run_events import sanitize
from .atlas_shared import filtered_endpoints
from .workbench import endpoint
from .workspace import _integer

BASIS=('Counts and points describe a bounded saved-endpoint sample, not an exhaustive internet inventory. '
       'Local GeoIP is primary; choosing a provider changes only the displayed retained estimate. '
       'Locations are approximate and can identify a network, hosting region or population center rather than a facility. '
       'Source dates and collection dates remain separate. Date filters scope retained endpoint evidence; positions use the latest retained known IP and the current local database. Historical position snapshots are not reconstructed. No provider searches, target requests or DNS resolution occur on browse.')


@lru_cache(maxsize=1)
def _filter_choices():
    families = {**CATEGORY_LABELS, 'other': 'Other technologies', 'unknown': 'Unknown / unverified'}
    technologies = sorted(catalog(), key=lambda row: row['label'].casefold())
    return {
        'families': [{'key': key, 'label': label} for key, label in families.items()],
        'technologies': [
            {field: row[field] for field in ('key', 'label', 'category')}
            for row in technologies
        ],
    }


def _options(params):
    source=params.get('source','local');mode=params.get('count_mode','ips')
    if source not in {'local',*geo.PROVIDERS}:raise ValueError('Unknown geography source')
    if mode not in {'ips','endpoints'}:raise ValueError('Choose ips or endpoints for counts')
    country=params.get('country','').strip();continent=params.get('continent','').strip()
    if country and (len(country)>120 or not re.fullmatch(r"[\w .,'’()-]+",country,re.UNICODE)):raise ValueError('Invalid country filter')
    if continent and continent.casefold()!='unknown':
        code=continent.upper() if continent.upper() in geo.CONTINENTS else next((code for code,name in geo.CONTINENTS.items() if name.casefold()==continent.casefold()),None)
        if code is None:raise ValueError('Invalid continent filter')
        continent=code
    return source,mode,country,continent


def _endpoint(row):
    return {'id':str(row.pk),'address':row.address,'port':row.port,'transport':row.transport,'title':row.title,'technology':row.service_type or 'unknown'}


def _point(location):
    return bool(location and location.get('status')=='located' and geo.canonical_ip(location.get('ip'),public_only=True) and location.get('latitude') is not None and location.get('longitude') is not None)


def _summary(row,value):
    location=value['location'];estimate=location or {}
    comparisons=value['comparisons'];states={item['status'] for item in comparisons.values()}
    return {'id':str(row.pk),'endpoint_id':str(row.pk),**{key:val for key,val in _endpoint(row).items() if key!='id'},'ip':value['ip'],
        'location':location,'source':estimate.get('source'),'source_date':estimate.get('source_date'),'country_code':estimate.get('country_code'),
        'country_name':estimate.get('country_name'),'continent_code':estimate.get('continent_code'),'continent_name':estimate.get('continent_name'),
        'city':estimate.get('city'),'region':estimate.get('region'),'accuracy_radius_km':estimate.get('accuracy_radius_km'),
        'endpoint_count':1,'endpoint_ids':[str(row.pk)],'comparison_status':'country_differs' if 'country_differs' in states else 'country_agrees' if 'country_agrees' in states else 'not_comparable',
        'reference':{'type':'endpoint','id':str(row.pk)},'detail_url':'/api/atlas/geography/'+str(row.pk)}


@endpoint()
def geography(request):
    source,mode,country,continent=_options(request.GET)
    limit=_integer(request.GET.get('limit'),500,2000);sample_limit=_integer(request.GET.get('sample_limit'),5000,5000)
    scoped,coverage=filtered_endpoints(request.GET,include_geography=False)
    total=scoped.count();rows=list(scoped.only('id','address','port','transport','service_type','title','last_seen').order_by('-last_seen','-pk')[:sample_limit])
    values=geo.endpoint_locations(rows,source=source)
    matched=[_summary(row,values[str(row.pk)]) for row in rows if (not country and not continent) or geo.matches_area(values[str(row.pk)]['location'],country=country,continent=continent)]
    grouped={}
    for row in matched:
        key=row['ip'] if mode=='ips' and row['ip'] else row['endpoint_id']
        if key in grouped:
            grouped[key]['endpoint_count']+=1
            if len(grouped[key]['endpoint_ids'])<20:grouped[key]['endpoint_ids'].append(row['endpoint_id'])
        else:grouped[key]=row
    records=list(grouped.values());countries=Counter();country_labels={};continents=Counter();features=[]
    for row in records:
        code=row['country_code'];name=row['country_name']
        if code or name:
            country_key=code or 'name:'+geo._fold(name)
            countries[country_key]+=1
            label=country_labels.setdefault(country_key,{'code':code or '', 'name':name or code,'continent_code':row['continent_code'] or ''})
            if (not label['name'] or label['name']==code) and name:label['name']=name
            if not label['continent_code'] and row['continent_code']:label['continent_code']=row['continent_code']
        if row['continent_code'] or row['continent_name']:continents[(row['continent_code'] or '',row['continent_name'] or row['continent_code'] or '')]+=1
        if _point(row['location']):
            properties={key:value for key,value in row.items() if key!='location'}
            features.append({'type':'Feature','id':row['endpoint_id'],'geometry':{'type':'Point','coordinates':[row['location']['longitude'],row['location']['latitude']]},'properties':properties})
    sampled=len(rows)<total;limited=len(features)>limit or len(records)>limit
    output={'type':'FeatureCollection','features':features[:limit],'results':records[:limit],
        'continents':[{'code':code,'name':name,'count':count} for (code,name),count in sorted(continents.items(),key=lambda item:(-item[1],item[0]))],
        'countries':[{**country_labels[key],'count':count} for key,count in sorted(countries.items(),key=lambda item:(-item[1],item[0]))],
        'counts':{'scoped_endpoints':total,'sampled_endpoints':len(rows),'matched_endpoints':len(matched),'unique_ips':len({row['ip'] for row in matched if row['ip']}),
                  'located':len(features),'unlocated':len(records)-len(features),'country_only':sum(bool(row['location']) and not _point(row['location']) for row in records),'country_unknown':sum(not (row['country_code'] or row['country_name']) for row in records),'continent_unknown':sum(not (row['continent_code'] or row['continent_name']) for row in records),'returned_features':min(len(features),limit)},
        'source':source,'count_mode':mode,'sample_limit':sample_limit,'limit':limit,'local_database':geo.database_status(),'scope':coverage,
        'truncated':bool(sampled or limited or coverage.get('scope_truncated') or coverage.get('technology_truncated')),'sample_truncated':sampled,'display_truncated':limited,
        'filter_choices':_filter_choices(),'network_contact':False,'basis':BASIS}
    return JsonResponse(sanitize(output,preserve_metrics=True))


@endpoint()
def detail(request,endpoint_id):
    source,_,_,_=_options(request.GET)
    endpoints,coverage=filtered_endpoints(request.GET,include_geography=False)
    row=endpoints.only('id','address','port','transport','service_type','title').get(pk=UUID(str(endpoint_id)))
    value=geo.endpoint_locations([row],source=source)[str(row.pk)]
    result={'endpoint':_endpoint(row),**value,'source':source,'local_database':geo.database_status(),'scope':coverage,'network_contact':False,'basis':BASIS}
    return JsonResponse(sanitize(result,preserve_metrics=True))
