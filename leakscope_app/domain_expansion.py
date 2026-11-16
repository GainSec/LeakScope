"""Domain research HTTP surface. Reads never initiate collection."""
from uuid import UUID
from django.db import transaction
from django.http import JsonResponse
from leakscope_app.models import DomainExpansion
from leakscope.phase3 import domain_expansion as engine
from leakscope.phase3 import organization_lifecycle as lifecycle
from .workbench import endpoint, _body
from leakscope.services.config import get_config
from django.utils import timezone
from datetime import timedelta


@endpoint(('GET','POST'))
def netlas_indices(request):
    from leakscope.phase3.netlas_dns_history import load_indices
    from leakscope.phase3.domain_expansion import digest
    from leakscope_app.models import DomainSourceCache
    config=get_config()['config']
    key=digest(['netlas-dns-snapshots-v1',config.get('NETLAS_API_KEY','')])
    cached=DomainSourceCache.objects.filter(pk=key,expires_at__gt=timezone.now()).first()
    if request.method=='GET' or cached:
        return JsonResponse({'indices':cached.data['indices'] if cached else [],'cached':bool(cached),'requests':0})
    indices=load_indices(config)
    DomainSourceCache.objects.update_or_create(pk=key,defaults={'data':{'indices':indices},'expires_at':timezone.now()+timedelta(minutes=15)})
    return JsonResponse({'indices':indices,'cached':False,'requests':1})


def row(value):
    return DomainExpansion.objects.select_related('run','organization').get(pk=UUID(str(value)))


@endpoint(('GET','POST'))
def expansions(request, expansion_id=None):
    if request.method=='POST':
        if expansion_id:raise ValueError('Start research from the expansion collection')
        return JsonResponse(engine.detail(engine.start(_body(request))))
    if expansion_id:return JsonResponse(engine.detail(row(expansion_id),request.GET))
    rows=DomainExpansion.objects.select_related('run','organization')
    if request.GET.get('organization_id'):
        org=lifecycle.canonical(lifecycle.organization(request.GET['organization_id']))
        rows=rows.filter(organization=org)
    def number(name,default,maximum):
        value=request.GET.get(name,str(default))
        if not value.isdigit():raise ValueError('Invalid '+name)
        return engine.integer(int(value),1,maximum,name)
    size=number('page_size',100,100);page=number('page',1,10000000)
    count=rows.count();pages=max(1,(count+size-1)//size);page=min(page,pages)
    rows=rows.order_by('-created_at','-pk')[(page-1)*size:page*size]
    return JsonResponse({'results':[engine.serialize(exp) for exp in rows],'count':count,'page':page,'page_size':size,'pages':pages})


@endpoint(('POST',))
def review(request, expansion_id):
    return JsonResponse(engine.review(expansion_id,_body(request)))


@endpoint(('POST',))
def cancel(request, expansion_id):
    exp=row(expansion_id)
    engine.finish(exp.pk,'cancelled','Cancelled by user; completed results retained')
    exp.refresh_from_db()
    return JsonResponse(engine.detail(exp))


@endpoint(('POST',))
def expand(request, expansion_id):
    return JsonResponse(engine.detail(engine.expand(expansion_id,_body(request))))


@endpoint(('POST',))
def resume(request, expansion_id):
    return JsonResponse(engine.detail(engine.resume(expansion_id,_body(request))))
