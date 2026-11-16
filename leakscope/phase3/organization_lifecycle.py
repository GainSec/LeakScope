"""Local, transactional organization lifecycle; never collect or erase host evidence."""
from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from leakscope_app.models import AssetNode, AssetEvidence, AssetRelationship


UNCHANGED = ['Findings and service observations','Screenshot and wire artifacts','Domains, networks and certificates','CertStream watches and saved lookups']


def organization(value, *, active=False, lock=False):
    try: key=UUID(str(value))
    except (ValueError,TypeError,AttributeError): raise ValueError('Invalid organization ID') from None
    rows=AssetNode.objects.select_for_update() if lock else AssetNode.objects
    row=rows.get(pk=key,kind='organization')
    if active and row.organization_status!='active': raise ValueError('Organization is '+row.organization_status+'; use an active organization')
    return row


def canonical(row, *, lock=False):
    seen=set()
    while row.organization_status=='merged':
        if row.pk in seen or len(seen)>=128: raise ValueError('Invalid organization merge chain')
        seen.add(row.pk)
        row=organization(row.merged_into_id,lock=lock)
    return row


def aliases(*groups, excluding=''):
    result=[];seen={excluding.casefold()}
    for group in groups:
        if not isinstance(group,list): continue
        for value in group:
            if isinstance(value,str) and value.strip() and value.casefold() not in seen:
                result.append(value);seen.add(value.casefold())
    return result


def counts(row):
    outgoing=row.outgoing.all()
    scopes=outgoing.filter(relation='user_scope')
    return {'domains':scopes.filter(target__kind='domain').values('target_id').distinct().count(),
            'members':outgoing.filter((Q(relation='user_scope') & ~Q(target__kind='domain')) | Q(relation__in=['uses_service','references_domain','tracks_lookalike'])).values('target_id').distinct().count(),
            'evidence':row.evidence.count(),'incoming':row.incoming.count(),'outgoing':outgoing.count()}


def preview(source_id,target_id=None):
    source=organization(source_id)
    if source.organization_status not in {'active','archived'}: raise ValueError('This organization is retired')
    result={'organization':source,'counts':counts(source),'unchanged':UNCHANGED}
    if target_id:
        target=organization(target_id,active=True)
        if target.pk==source.pk: raise ValueError('Choose a different destination organization')
        result.update(target=target,target_counts=counts(target))
    return result


def _audit(row,action,data):
    from . import relationships as store
    return store.evidence(row,'organization_lifecycle',dict(data,action=action,event_id=str(uuid4()),at=timezone.now().isoformat()))


@transaction.atomic
def set_status(source_id,action,confirmation=None):
    source=organization(source_id,lock=True)
    states={'archive':'archived','restore':'active','delete':'deleted'}
    if action not in states: raise ValueError('Unknown organization action')
    if action=='delete' and confirmation!=source.value: raise ValueError('Type the organization name to confirm deletion')
    if source.organization_status=='merged': raise ValueError('A merged organization is managed at its destination')
    if source.organization_status=='deleted':
        if action=='delete': return source,{'already_applied':True}
        raise ValueError('Deleted organizations cannot be restored')
    desired=states[action]
    if source.organization_status==desired:return source,{'already_applied':True}
    previous=source.organization_status
    summary=counts(source)
    if action=='delete':
        # The original user-scope evidence is retained; only workspace associations are detached.
        source.outgoing.filter(relation__in=['user_scope','uses_service','references_domain','tracks_lookalike']).delete()
    source.organization_status=desired;source.organization_status_changed_at=timezone.now()
    source.save(update_fields=['organization_status','organization_status_changed_at'])
    _audit(source,action,{'organization_id':str(source.pk),'name':source.value,'previous_status':previous,'summary':summary,'unchanged':UNCHANGED})
    return source,summary


@transaction.atomic
def merge(source_id,target_id,confirmation):
    source=organization(source_id);target=organization(target_id)
    if source.pk==target.pk: raise ValueError('Choose a different destination organization')
    # Stable lock order prevents opposing UI merges from taking the roots in reverse order.
    locked={row.pk:row for row in AssetNode.objects.select_for_update().filter(pk__in=[source.pk,target.pk]).order_by('pk')}
    source,target=locked[source.pk],locked[target.pk]
    if source.organization_status!='active' or target.organization_status!='active': raise ValueError('Both source and destination must be active; restore archived organizations first')
    if confirmation!=source.value: raise ValueError('Type the source organization name to confirm the merge')
    summary=counts(source)
    attrs=dict(source.attributes,**target.attributes)
    attrs['aliases']=aliases(target.attributes.get('aliases',[]),[source.value],source.attributes.get('aliases',[]),excluding=target.value)
    target.attributes=attrs;target.first_seen=min(source.first_seen,target.first_seen);target.last_seen=timezone.now()
    target.save(update_fields=['attributes','first_seen','last_seen'])
    from . import relationships as store
    # Keep proof UUIDs, run references and historical timestamps, even when scopes overlap.
    for proof in source.evidence.select_for_update().order_by('pk').iterator(chunk_size=200):
        data=dict(proof.data) if isinstance(proof.data,dict) else {'original_data':proof.data}
        origins=data.get('organization_origins',[])
        if not isinstance(origins,list): data['previous_organization_origins']=origins;origins=[]
        origin={'id':str(source.pk),'name':source.value}
        data['organization_origins']=origins+[origin] if origin not in origins else origins
        proof.subject=target;proof.data=data
        proof.key=store._hash([str(target.pk),proof.source,store._stable(data)])
        proof.save(update_fields=['subject','data','key'])
    retired=[];duplicates=0
    edges=list(AssetRelationship.objects.select_for_update().filter(Q(source=source)|Q(target=source)).order_by('pk'))
    for edge in edges:
        new_source=target.pk if edge.source_id==source.pk else edge.source_id
        new_target=target.pk if edge.target_id==source.pk else edge.target_id
        if new_source==new_target:
            retired.append({'id':str(edge.pk),'source_id':str(edge.source_id),'target_id':str(edge.target_id),'relation':edge.relation,'evidence_id':str(edge.evidence_id),'confidence':edge.confidence,'first_seen':edge.first_seen.isoformat(),'last_seen':edge.last_seen.isoformat()})
            edge.delete();continue
        existing=AssetRelationship.objects.filter(source_id=new_source,target_id=new_target,relation=edge.relation,evidence_id=edge.evidence_id).exclude(pk=edge.pk).first()
        if existing:
            existing.confidence=max(existing.confidence,edge.confidence)
            existing.first_seen=min(existing.first_seen,edge.first_seen);existing.last_seen=max(existing.last_seen,edge.last_seen)
            existing.save(update_fields=['confidence','first_seen','last_seen']);edge.delete();duplicates+=1
        else:
            edge.source_id=new_source;edge.target_id=new_target;edge.save(update_fields=['source','target'])
    from .domain_expansion import merge_organizations
    merge_organizations(source,target)
    source.organization_status='merged';source.merged_into=target;source.organization_status_changed_at=timezone.now()
    source.save(update_fields=['organization_status','merged_into','organization_status_changed_at'])
    summary.update(duplicate_relationships=duplicates,retired_self_relationships=len(retired))
    _audit(target,'merge',{'source_id':str(source.pk),'source_name':source.value,'source_attributes':source.attributes,
        'target_id':str(target.pk),'target_name':target.value,'summary':summary,'retired_self_relationships':retired,'unchanged':UNCHANGED})
    return target,source,summary
