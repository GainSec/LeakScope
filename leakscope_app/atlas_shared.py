"""Read-only endpoint scope shared by saved-data atlas views."""
from uuid import UUID
from django.db.models import Exists,OuterRef,Subquery,Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from leakscope.phase3.state import visible_endpoints
from .models import ServiceObservation,TagAssignment


def time_filter(value,name):
    if not value:return None
    if not isinstance(value,str) or len(value)>80:raise ValueError('Invalid '+name+' timestamp')
    try:stamp=parse_datetime(value)
    except ValueError:stamp=None
    if stamp is None or timezone.is_naive(stamp):raise ValueError('Use an ISO8601 '+name+' timestamp with timezone')
    return stamp


def filtered_endpoints(params,*,include_geography=True):
    endpoints=visible_endpoints()
    observation=ServiceObservation.objects.filter(endpoint_id=OuterRef('pk'),hidden=False).exclude(provider='certstream')
    q=str(params.get('q','')).strip()
    if len(q)>200:raise ValueError('Search text too long')
    if q:
        matching=ServiceObservation.objects.filter(endpoint_id=OuterRef('pk'),hidden=False).filter(Q(data__service_type__icontains=q)|Q(data__metadata__product__icontains=q))
        endpoints=endpoints.annotate(atlas_search=Exists(matching)).filter(Q(address__icontains=q)|Q(title__icontains=q)|Q(atlas_search=True))
    for name in ['provider']:
        value=str(params.get(name,'')).strip()
        if len(value)>100:raise ValueError('Filter too long')
        if value:
            if name=='provider':observation=observation.filter(Q(provider__startswith='fetch:')|Q(provider__startswith='native:')) if value=='direct' else observation.filter(provider=value)
            else:observation=observation.filter(Q(data__service_type=value)|Q(data__metadata__product__iexact=value))
    since=time_filter(params.get('since'),'since');until=time_filter(params.get('until'),'until')
    if since and until and since>until:raise ValueError('Since must precede until')
    if since:observation=observation.filter(last_collected_at__gte=since)
    if until:observation=observation.filter(first_collected_at__lte=until)
    endpoints=endpoints.annotate(atlas_matching=Exists(observation)).filter(atlas_matching=True)
    coverage={'scope_truncated':False,'basis':'Visible retained endpoints with explicit saved organization membership. No collection.'}
    org=params.get('organization_id') or params.get('organization')
    if org:
        from .workspace import _scope,_member_records
        endpoints=_member_records(endpoints,_scope(str(UUID(str(org)))),'service','endpoint_id')
    tag=str(params.get('tag','')).strip().casefold()
    if len(tag)>64:raise ValueError('Tag too long')
    if tag:
        from django.db.models.functions import Cast
        from django.db.models import CharField
        endpoints=endpoints.annotate(atlas_key=Cast('pk',CharField()))
        # UUID text formats differ across SQLite/PostgreSQL; normalize both sides.
        from django.db.models.functions import Replace
        from django.db.models import Value
        tags=TagAssignment.objects.filter(target_kind='endpoint',tag__name=tag).annotate(
            atlas_key=Replace('target_key',Value('-'),Value(''),output_field=CharField())).values('atlas_key')
        endpoints=endpoints.annotate(atlas_tag_key=Replace('atlas_key',Value('-'),Value(''))).filter(atlas_tag_key__in=Subquery(tags))
    technology=str(params.get('technology','')).strip();category=str(params.get('category','')).strip()
    if len(technology)>100 or len(category)>40:raise ValueError('Identity filter too long')
    from .atlas import attribution_filter
    level=attribution_filter(params.get('attribution'))
    if technology or category or level:
        from .atlas import classify_levels,identity_rows
        from leakscope.phase3.attribution import RANK
        rows=ServiceObservation.objects.filter(endpoint_id__in=Subquery(endpoints.values('pk')),hidden=False).exclude(provider='certstream')
        if params.get('provider'):rows=rows.filter(Q(provider__startswith='fetch:')|Q(provider__startswith='native:')) if params['provider']=='direct' else rows.filter(provider=params['provider'])
        if since:rows=rows.filter(last_collected_at__gte=since)
        if until:rows=rows.filter(first_collected_at__lte=until)
        sampled=list(identity_rows(rows,limit=10001));highest={}
        for row in sampled[:10000]:
            for item in classify_levels(row['data'],row['provider']):
                if (technology and item['key']!=technology) or (category and item['category']!=category):continue
                # An evidence-level filter compares the highest level saved for that technology.
                key=(row['endpoint_id'],item['key'])
                if key not in highest or RANK.get(item['level'],-1)>RANK.get(highest[key],-1):highest[key]=item['level']
        matching={identifier for (identifier,_),value in highest.items() if not level or value==level}
        endpoints=endpoints.filter(pk__in=matching)
        coverage.update(sampled_observations=min(len(sampled),10000),scope_truncated=len(sampled)>10000)
    if include_geography and (params.get('country') or params.get('continent')):
        from leakscope.phase3.geography import filter_local_area
        endpoints,geo=filter_local_area(endpoints,country=params.get('country',''),continent=params.get('continent',''))
        previous=coverage.get('scope_truncated',False)
        coverage.update(geo)
        coverage['scope_truncated']=previous or bool(geo.get('geography_truncated'))
    return endpoints,coverage
