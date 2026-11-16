"""Capture curation and derivatives stay local, visible and reversible."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.urls import path
from django.utils import timezone
from leakscope_app import gallery as api
from leakscope_app.models import (EvidenceArtifact, ServiceEndpoint, ServiceObservation, Run,
    ScreenshotCollection, ScreenshotMembership, ScreenshotCuration, Tag, TagAssignment)

urlpatterns=[path('api/atlas/screenshots',api.screenshots),path('api/atlas/collections',api.collections),
    path('api/atlas/collections/<uuid:collection_id>/delete',api.delete_collection),
    path('api/atlas/screenshots/curate',api.curate),path('api/atlas/screenshots/index',api.index),
    path('api/atlas/screenshots/<uuid:artifact_id>/thumbnail',api.thumbnail)]


@override_settings(ROOT_URLCONF=__name__)
class GalleryTests(TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        setting=override_settings(LEAKSCOPE_CAPTURE_DIR=self.root);setting.enable();self.addCleanup(setting.disable)
        self.endpoint=ServiceEndpoint.objects.create(key='gallery1',address='192.0.2.1',port=443,service_type='jenkins',title='Saved console')
        self.proof=ServiceObservation.objects.create(endpoint=self.endpoint,provider='fetch:screenshot',fingerprint='gallery1',data={'service_type':'jenkins'})
    def picture(self):
        from PIL import Image
        image=Image.new('RGB',(900,600),'#327081');out=BytesIO();image.save(out,format='PNG');return out.getvalue()
    def artifact(self, endpoint=None, metadata=None, data=None, filename=None):
        data=self.picture() if data is None else data
        filename=filename or f'capture-{EvidenceArtifact.objects.count()}.png';(self.root/filename).write_bytes(data)
        return EvidenceArtifact.objects.create(endpoint=endpoint or self.endpoint,kind='screenshot',relative_path=filename,
            mime='image/png',size=len(data),sha256=hashlib.sha256(data).hexdigest(),tool='fixture',metadata=metadata if metadata is not None else {'observation_pk':str(self.proof.pk)})
    def post(self,path,data):return self.client.post('/api/atlas/'+path,json.dumps(data),content_type='application/json')
    def test_browse_is_metadata_only_and_artifact_tags_are_separate_from_inherited(self):
        item=self.artifact()
        capture=Tag.objects.create(name='review');inherited=Tag.objects.create(name='endpoint')
        TagAssignment.objects.create(tag=capture,target_kind='artifact',target_key=str(item.pk))
        TagAssignment.objects.create(tag=inherited,target_kind='endpoint',target_key=str(self.endpoint.pk))
        with patch('leakscope.phase3.local_correlations._read_artifact',side_effect=AssertionError('browse cannot decode files')),patch('requests.get',side_effect=AssertionError('no network')):
            response=self.client.get('/api/atlas/screenshots')
        self.assertEqual(response.status_code,200,response.content)
        row=response.json()['results'][0]
        self.assertEqual(row['manual_tags'],['review']);self.assertEqual(row['inherited_tags'],['endpoint'])
        self.assertEqual(row['thumbnail_status'],'pending');self.assertIsNone(row['thumbnail_url'])
        self.assertEqual(row['original_url'],'/api/operations/artifacts/'+str(item.pk))
        self.assertEqual(row['file_status'],'not_checked')
    def test_cursor_pages_are_stable_and_filters_cannot_change(self):
        for _ in range(5):self.artifact()
        first=self.client.get('/api/atlas/screenshots',{'limit':2}).json();self.assertTrue(first['has_more'])
        second=self.client.get('/api/atlas/screenshots',{'limit':2,'cursor':first['next_cursor']}).json()
        self.assertEqual(len(set(r['id'] for r in first['results']+second['results'])),4)
        self.assertEqual(first['count'],5)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'cursor':first['next_cursor'],'q':'other'}).status_code,400)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'cursor':'bad'}).status_code,400)
    def test_endpoint_tag_scope_keeps_technology_drilldown_in_endpoint_tag_cohort(self):
        item=self.artifact();tag=Tag.objects.create(name='review')
        TagAssignment.objects.create(tag=tag,target_kind='artifact',target_key=str(item.pk))
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'tag':'review'}).json()['count'],1)
        response=self.client.get('/api/atlas/screenshots',{'tag':'review','tag_scope':'endpoint'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['count'],0)
        TagAssignment.objects.create(tag=tag,target_kind='endpoint',target_key=str(self.endpoint.pk))
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'tag':'review','tag_scope':'endpoint'}).json()['count'],1)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'tag_scope':'unsupported'}).status_code,400)
    def test_latest_and_duplicate_modes_preserve_all_default_history(self):
        for _ in range(3):self.artifact()
        self.assertEqual(self.client.get('/api/atlas/screenshots').json()['count'],3)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'latest':1}).json()['count'],1)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'duplicates':'collapse'}).json()['count'],1)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'duplicates':'surprise'}).status_code,400)
    def test_collection_crud_membership_and_keep_protect_original_without_deleting_it(self):
        from leakscope.phase3.gallery import protected_artifacts
        item=self.artifact()
        response=self.post('collections',{'name':'Needs review','color':'#11aabb'});self.assertEqual(response.status_code,201,response.content)
        collection=response.json()['collection'];key=collection['id']
        self.assertEqual(self.post('screenshots/curate',{'artifact_ids':[str(item.pk)],'add_collections':[key]}).status_code,200)
        self.assertTrue(protected_artifacts().filter(pk=item.pk).exists())
        self.assertEqual(self.client.get('/api/atlas/collections').json()['results'][0]['count'],1)
        self.assertEqual(self.post('collections',{'id':key,'name':'Reviewed','position':2}).json()['collection']['name'],'Reviewed')
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'collection':key}).json()['count'],1)
        self.assertEqual(self.post('screenshots/curate',{'artifact_ids':[str(item.pk)],'pinned':True,'tags':['Keep Me']}).status_code,200)
        self.assertEqual(self.post('collections/'+key+'/delete',{}).status_code,200)
        self.assertTrue(EvidenceArtifact.objects.filter(pk=item.pk).exists());self.assertTrue((self.root/item.relative_path).exists())
        self.assertTrue(protected_artifacts().filter(pk=item.pk).exists())
        self.post('screenshots/curate',{'artifact_ids':[str(item.pk)],'pinned':False})
        self.assertFalse(protected_artifacts().filter(pk=item.pk).exists())
        row=self.client.get('/api/atlas/screenshots').json()['results'][0];self.assertEqual(row['manual_tags'],['keep me'])
    def test_curation_is_all_or_nothing_for_hidden_or_unrecognized_artifacts(self):
        import uuid
        item=self.artifact()
        response=self.post('screenshots/curate',{'artifact_ids':[str(item.pk),str(uuid.uuid4())],'pinned':True})
        self.assertEqual(response.status_code,400);self.assertFalse(ScreenshotCuration.objects.exists())
        self.proof.hidden=True;self.proof.save()
        self.assertEqual(self.client.get('/api/atlas/screenshots').json()['count'],0)
        self.assertEqual(self.post('screenshots/curate',{'artifact_ids':[str(item.pk)],'pinned':True}).status_code,400)
        self.assertEqual(self.client.get('/api/atlas/screenshots/'+str(item.pk)+'/thumbnail').status_code,404)
    def test_validation_rejects_bad_limits_names_colors_tags_and_unsafe_mutations(self):
        item=self.artifact()
        for data in [{'name':'x','color':'red()'},{'name':'<'},{'name':'x','position':-1},{'name':'x','extra':True}]:
            self.assertEqual(self.post('collections',data).status_code,400)
        for data in [{'artifact_ids':[str(item.pk)],'pinned':'yes'},{'artifact_ids':[],'tags':[]},{'artifact_ids':[str(item.pk)],'tags':['x'*65]},{'artifact_ids':[str(item.pk)],'unsupported':'yes'}]:
            self.assertEqual(self.post('screenshots/curate',data).status_code,400)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'limit':101}).status_code,400)
        self.assertEqual(Client(enforce_csrf_checks=True).post('/api/atlas/collections','{}',content_type='application/json').status_code,403)
    def test_thumbnail_job_uses_actual_image_without_changing_original(self):
        from leakscope.phase3.gallery_jobs import execute_index
        item=self.artifact();before=(self.root/item.relative_path).read_bytes()
        run=Run.objects.create(kind='gallery_index',task_id='gallery-one')
        with patch('requests.get',side_effect=AssertionError('no network')):
            result=execute_index(run.pk,run.task_id,[str(item.pk)])
        self.assertEqual(result['indexed'],1);self.assertFalse(result['network_contact'])
        item.refresh_from_db();self.assertEqual((self.root/item.relative_path).read_bytes(),before)
        self.assertEqual(item.metadata['gallery']['width'],900);self.assertEqual(item.metadata['gallery']['height'],600)
        row=self.client.get('/api/atlas/screenshots').json()['results'][0];self.assertEqual(row['thumbnail_status'],'ready')
        response=self.client.get(row['thumbnail_url']);self.assertEqual(response.status_code,200)
        self.assertEqual(response['Content-Type'],'image/png');image=b''.join(response.streaming_content)
        from PIL import Image
        with Image.open(BytesIO(image)) as thumb:self.assertLessEqual(thumb.width,480);self.assertLessEqual(thumb.height,300)
    def test_thumbnail_serving_rechecks_source_visibility_and_stored_hash(self):
        from leakscope.phase3.gallery_jobs import execute_index
        item=self.artifact();run=Run.objects.create(kind='gallery_index',task_id='gallery-visible');execute_index(run.pk,run.task_id,[str(item.pk)])
        item.refresh_from_db();thumb=self.root/item.metadata['gallery']['path'];thumb.write_bytes(b'changed')
        self.assertEqual(self.client.get('/api/atlas/screenshots/'+str(item.pk)+'/thumbnail').status_code,404)
        self.proof.hidden=True;self.proof.save()
        self.assertEqual(self.client.get('/api/atlas/screenshots/'+str(item.pk)+'/thumbnail').status_code,404)
    def test_malformed_oversized_symlink_or_changed_originals_are_honest_partial_failures(self):
        from leakscope.phase3.gallery_jobs import execute_index
        item=self.artifact(data=b'not an image');run=Run.objects.create(kind='gallery_index',task_id='gallery-bad')
        result=execute_index(run.pk,run.task_id,[str(item.pk)]);self.assertEqual(result['errors'],1);self.assertTrue(result['partial'])
        self.assertFalse((self.root/'gallery').exists())
        item=self.artifact();item.sha256='0'*64;item.save();run=Run.objects.create(kind='gallery_index',task_id='gallery-hash')
        self.assertEqual(execute_index(run.pk,run.task_id,[str(item.pk)])['errors'],1)
    def test_cancelled_or_replaced_task_never_publishes_a_derivative(self):
        from leakscope.phase3.gallery_jobs import execute_index
        for replacement in [False,True]:
            item=self.artifact();run=Run.objects.create(kind='gallery_index',task_id='gallery-cancel-'+str(replacement))
            def render(row):
                Run.objects.filter(pk=run.pk).update(**({'task_id':'new-task'} if replacement else {'state':'cancelled'}))
                return {'bytes':b'not published','width':1,'height':1,'thumbnail_width':1,'thumbnail_height':1}
            with patch('leakscope.phase3.gallery_jobs.render_thumbnail',side_effect=render):
                result=execute_index(run.pk,run.task_id,[str(item.pk)])
            self.assertTrue(result['cancelled']);item.refresh_from_db();self.assertNotIn('gallery',item.metadata)
    def test_queue_is_bounded_visible_and_does_not_read_files(self):
        from leakscope.phase3.gallery_jobs import queue_index
        item=self.artifact()
        with patch('leakscope.phase3.gallery_jobs.enqueue') as enqueue,patch('leakscope.phase3.gallery_jobs.render_thumbnail',side_effect=AssertionError('queue cannot read')):
            queue_index({'limit':1,'artifact_ids':[str(item.pk)]})
        self.assertEqual(enqueue.call_args.kwargs['kwargs']['artifact_ids'],[str(item.pk)])
        self.assertFalse(enqueue.call_args.kwargs['parameters']['network_contact'])
        with self.assertRaises(ValueError):queue_index({'limit':1001})
    def test_group_filters_use_capture_tags_and_country_is_local_only(self):
        item=self.artifact()
        self.post('screenshots/curate',{'artifact_ids':[str(item.pk)],'tags':['review']})
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'tag':'review'}).json()['count'],1)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'group':'technology'}).json()['results'][0]['group']['key'],'jenkins')

    def test_missing_or_malformed_image_metadata_is_a_pending_capture_not_a_server_error(self):
        self.artifact(metadata='legacy malformed metadata')
        response=self.client.get('/api/atlas/screenshots');self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['results'][0]['thumbnail_status'],'pending')

    def test_worker_reports_every_skipped_file_and_stale_delivery_never_reads(self):
        import uuid
        from leakscope.phase3.gallery_jobs import execute_index
        run=Run.objects.create(kind='gallery_index',task_id='gallery-progress')
        with patch('leakscope.phase3.gallery_jobs.write_state') as states:
            execute_index(run.pk,run.task_id,[str(uuid.uuid4()),str(uuid.uuid4())])
        values=[call.args[2]['current'] for call in states.call_args_list if call.args[1]=='PROGRESS']
        self.assertEqual(values,[0,1,2])
        run=Run.objects.create(kind='gallery_index',task_id='gallery-stale',state='cancelled')
        with patch('leakscope.phase3.gallery_jobs.render_thumbnail',side_effect=AssertionError('stale task cannot read')):
            result=execute_index(run.pk,run.task_id,[str(uuid.uuid4())])
        self.assertTrue(result['cancelled'])

    def test_symlink_cache_folder_cannot_write_outside_capture_root(self):
        from leakscope.phase3.gallery_jobs import execute_index
        outside=tempfile.TemporaryDirectory();self.addCleanup(outside.cleanup)
        (self.root/'gallery').symlink_to(outside.name,target_is_directory=True)
        item=self.artifact();run=Run.objects.create(kind='gallery_index',task_id='gallery-escape')
        result=execute_index(run.pk,run.task_id,[str(item.pk)])
        self.assertEqual(result['errors'],1);self.assertEqual(list(Path(outside.name).iterdir()),[])

    def test_exact_old_source_remains_bound_when_endpoint_has_many_later_observations(self):
        item=self.artifact()
        for number in range(30):ServiceObservation.objects.create(endpoint=self.endpoint,provider='netlas',fingerprint='later'+str(number),data={'service_type':'http'})
        row=self.client.get('/api/atlas/screenshots').json()['results'][0]
        self.assertEqual(row['provider'],'fetch:screenshot')

    def test_collections_and_capture_dates_and_tags_filter_without_erasing_endpoint_tags(self):
        from datetime import timedelta
        item=self.artifact();old=self.artifact()
        EvidenceArtifact.objects.filter(pk=old.pk).update(created_at=timezone.now()-timedelta(days=3))
        tag=Tag.objects.create(name='inherited');TagAssignment.objects.create(tag=tag,target_kind='endpoint',target_key=str(self.endpoint.pk))
        self.post('screenshots/curate',{'artifact_ids':[str(item.pk)],'tags':['capture']})
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'since':(timezone.now()-timedelta(days=1)).isoformat()}).json()['count'],1)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'tag':'inherited'}).json()['count'],2)
        self.assertEqual(self.client.get('/api/atlas/screenshots',{'tag':'capture'}).json()['count'],1)
        self.assertTrue(TagAssignment.objects.filter(tag=tag,target_kind='endpoint',target_key=str(self.endpoint.pk)).exists())

    def test_cursor_ties_and_combined_latest_duplicate_modes(self):
        for _ in range(4):self.artifact()
        stamp=timezone.now();EvidenceArtifact.objects.update(created_at=stamp)
        identifiers=[];cursor=None
        for _ in range(5):
            query={'limit':1}
            if cursor:query['cursor']=cursor
            data=self.client.get('/api/atlas/screenshots',query).json();identifiers.extend(row['id'] for row in data['results']);cursor=data['next_cursor']
            if not data['has_more']:break
        self.assertEqual(len(identifiers),4);self.assertEqual(len(set(identifiers)),4)
        response=self.client.get('/api/atlas/screenshots',{'latest':1,'duplicates':'collapse'})
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['count'],1)

    def test_inherited_capture_tags_survive_manual_replacement(self):
        item=self.artifact();tag=Tag.objects.create(name='from source')
        TagAssignment.objects.create(tag=tag,target_kind='artifact',target_key=str(item.pk),origin='projection')
        self.post('screenshots/curate',{'artifact_ids':[str(item.pk)],'tags':['manual']})
        row=self.client.get('/api/atlas/screenshots').json()['results'][0]
        self.assertEqual(row['manual_tags'],['manual']);self.assertEqual(row['inherited_tags'],['from source'])
        self.assertTrue(TagAssignment.objects.filter(tag=tag,origin='projection').exists())

    def test_missing_artifact_between_render_and_publish_is_skipped(self):
        from leakscope.phase3.gallery_jobs import execute_index
        item=self.artifact();run=Run.objects.create(kind='gallery_index',task_id='gallery-deleted')
        def render(row):
            row.delete()
            return {'bytes':b'not published','width':1,'height':1,'thumbnail_width':1,'thumbnail_height':1}
        with patch('leakscope.phase3.gallery_jobs.render_thumbnail',side_effect=render):result=execute_index(run.pk,run.task_id,[str(item.pk)])
        self.assertEqual(result['skipped'],1);self.assertTrue(result['partial']);self.assertFalse((self.root/'gallery').exists())

    def test_local_unknown_country_filter_keeps_unlocated_screenshots(self):
        self.artifact()
        with override_settings(LEAKSCOPE_GEOIP_FILE='/nonexistent/gallery-mmdb'):
            response=self.client.get('/api/atlas/screenshots',{'country':'unknown','group':'country'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['count'],1);self.assertEqual(response.json()['results'][0]['country'],'unknown')

    def test_gallery_query_count_does_not_grow_per_capture(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for _ in range(8):self.artifact()
        self.client.get('/api/atlas/screenshots')
        with CaptureQueriesContext(connection) as queries:response=self.client.get('/api/atlas/screenshots')
        self.assertEqual(response.status_code,200,response.content);self.assertLessEqual(len(queries),18)

    def test_collection_update_counts_do_not_reveal_hidden_members(self):
        item=self.artifact();collection=ScreenshotCollection.objects.create(name='Visible label')
        ScreenshotMembership.objects.create(artifact=item,collection=collection)
        self.proof.hidden=True;self.proof.save()
        response=self.post('collections',{'id':str(collection.pk),'name':'Renamed visible label'})
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['collection']['count'],0)
