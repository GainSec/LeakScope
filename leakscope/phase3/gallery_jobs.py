"""Explicit screenshot-thumbnail maintenance with cooperative per-file fencing."""
import json
import time
from celery import shared_task
from django.db import transaction
from leakscope_app.models import EvidenceArtifact,Run
from leakscope.services.runs import enqueue,write_state,_parse
from .local_correlations import visible_artifacts,_ids,_integer,FILE_LIMIT
from .gallery import render_thumbnail,store_thumbnail,capture_metadata
from .capture_status import IMAGE_KINDS


def queue_index(spec,*,endpoints=None):
    if not isinstance(spec,dict) or spec.keys()-{'limit','artifact_ids'}:raise ValueError('Thumbnail indexing supports limit and saved artifact IDs only')
    limit=_integer(spec.get('limit',50),1,1000,'Limit')
    rows=visible_artifacts(endpoints).filter(kind__in=IMAGE_KINDS,size__gt=0,size__lte=FILE_LIMIT)
    if 'artifact_ids' in spec:rows=rows.filter(pk__in=_ids(spec['artifact_ids']))
    ids=list(rows.order_by('-created_at','-pk').values_list('pk',flat=True)[:limit+1])
    if not ids:raise ValueError('No visible eligible screenshots matched this selection')
    selected=[str(value) for value in ids[:limit]]
    return enqueue(run_gallery_index,kind='gallery_index',provider='local',type='screenshot_thumbnails',
        parameters={'limit':limit,'planned':len(selected),'selection_truncated':len(ids)>limit,'network_contact':False},kwargs={'artifact_ids':selected})


def _current(run_id,task_id):return Run.objects.filter(pk=run_id,task_id=task_id,state__in=['queued','running']).exists()


def execute_index(run_id,task_id,artifact_ids):
    artifact_ids=_ids(artifact_ids);run=Run.objects.get(pk=run_id)
    if not _current(run_id,task_id):return _parse(run.result) or {'cancelled':True,'processed':0,'indexed':0,'network_contact':False}
    write_state(task_id,'STARTED',{});started=time.monotonic()
    result={'planned':len(artifact_ids),'processed':0,'indexed':0,'errors':0,'skipped':0,'cancelled':False,'partial':False,'network_contact':False}
    def progress():write_state(task_id,'PROGRESS',{'current':result['processed'],'total':result['planned'],**result})
    progress()
    for identifier in artifact_ids:
        if not _current(run_id,task_id):result['cancelled']=True;break
        if time.monotonic()-started>120:result['partial']=True;break
        row=visible_artifacts().filter(pk=identifier,kind__in=IMAGE_KINDS).first()
        if row is None:result['processed']+=1;result['skipped']+=1;progress();continue
        try:rendered=render_thumbnail(row)
        except (OSError,ValueError,Warning):result['processed']+=1;result['errors']+=1;progress();continue
        with transaction.atomic():
            locked=Run.objects.select_for_update().get(pk=run_id)
            if locked.task_id!=task_id or locked.state not in {'queued','running'}:result['cancelled']=True;break
            latest=EvidenceArtifact.objects.select_for_update().filter(pk=row.pk,kind__in=IMAGE_KINDS).first()
            if latest is None or latest.sha256!=row.sha256 or not visible_artifacts().filter(pk=row.pk).exists():
                result['processed']+=1;result['skipped']+=1;progress();continue
            try:metadata=store_thumbnail(latest,rendered)
            except (OSError,ValueError):result['processed']+=1;result['errors']+=1;progress();continue
            latest.metadata={**capture_metadata(latest),'gallery':metadata};latest.save(update_fields=['metadata'])
        result['processed']+=1;result['indexed']+=1;progress()
    if not _current(run_id,task_id):result['cancelled']=True
    result['partial']=result['partial'] or result['cancelled'] or bool(result['errors'] or result['skipped'])
    if result['cancelled']:Run.objects.filter(pk=run_id,task_id=task_id,state='cancelled').update(result=json.dumps(result))
    else:write_state(task_id,'SUCCESS',result)
    return result


@shared_task(bind=True,name='leakscope.phase3.gallery_index',soft_time_limit=150,time_limit=180)
def run_gallery_index(self,artifact_ids):
    from .jobs import _claim_execution
    _claim_execution(self,'gallery-index')
    run=Run.objects.get(task_id=self.request.id,kind='gallery_index')
    return execute_index(run.pk,self.request.id,artifact_ids)
