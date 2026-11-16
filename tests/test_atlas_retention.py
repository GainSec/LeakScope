import tempfile
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.utils import timezone
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import EvidenceArtifact,ScreenshotCuration,ScreenshotCollection,ScreenshotMembership

class AtlasRetentionTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.settings=override_settings(LEAKSCOPE_CAPTURE_DIR=self.root,LEAKSCOPE_GALLERY_AUTO_THUMBNAILS=False);self.settings.enable();self.addCleanup(self.settings.disable)
        self.endpoint=ingest_observation('shodan',{'address':'192.0.2.1','port':443,'metadata':{}})
    def artifact(self,name,metadata=None):
        (self.root/name).write_bytes(b'fixture')
        return EvidenceArtifact.objects.create(endpoint=self.endpoint,kind='screenshot',relative_path=name,mime='image/png',metadata=metadata or {})
    def test_cleanup_keeps_pinned_and_collected_but_removes_expired_unprotected(self):
        from leakscope.phase3.jobs import cleanup_captures
        keep=self.artifact('keep.png');member=self.artifact('member.png');expired=self.artifact('expired.png')
        ScreenshotCuration.objects.create(artifact=keep,pinned=True)
        group=ScreenshotCollection.objects.create(name='Keep collection');ScreenshotMembership.objects.create(collection=group,artifact=member)
        EvidenceArtifact.objects.update(created_at=timezone.now()-timedelta(days=60))
        cleanup_captures()
        self.assertTrue(EvidenceArtifact.objects.filter(pk=keep.pk).exists());self.assertTrue((self.root/'keep.png').exists())
        self.assertTrue(EvidenceArtifact.objects.filter(pk=member.pk).exists());self.assertFalse(EvidenceArtifact.objects.filter(pk=expired.pk).exists())
    def test_original_file_route_enforces_same_visible_source_proof(self):
        other=ingest_observation('shodan',{'address':'192.0.2.2','port':443,'metadata':{}})
        artifact=self.artifact('cross.png',{'observation_pk':str(other.observations.get().pk)})
        self.assertEqual(self.client.get('/api/operations/artifacts/'+str(artifact.pk)).status_code,404)
    def test_derivative_cleanup_failure_cannot_leave_deleted_original_row(self):
        from leakscope.phase3.jobs import cleanup_captures
        expired=self.artifact('expired.png');next_item=self.artifact('next.png')
        EvidenceArtifact.objects.update(created_at=timezone.now()-timedelta(days=60))
        with patch('leakscope.phase3.gallery.delete_derivative',side_effect=OSError('unreadable derivative directory')):
            result=cleanup_captures()
        self.assertEqual(result['artifacts_removed'],2)
        self.assertFalse(EvidenceArtifact.objects.filter(pk__in=[expired.pk,next_item.pk]).exists())
        self.assertFalse((self.root/'expired.png').exists())
    @override_settings(LEAKSCOPE_GALLERY_AUTO_THUMBNAILS=True)
    def test_new_screenshot_queues_one_local_derivative_after_commit(self):
        with patch('leakscope.phase3.gallery_jobs.queue_index') as queue:
            with self.captureOnCommitCallbacks(execute=True):artifact=self.artifact('new.png')
        queue.assert_called_once_with({'limit':1,'artifact_ids':[str(artifact.pk)]})
    def test_derivative_metadata_update_does_not_reobserve_original_content(self):
        from leakscope_app.models import EvidenceCorrelation
        artifact=self.artifact('unchanged.png');artifact.size=7;artifact.sha256='a'*64;artifact.save(update_fields=['size','sha256'])
        previous=timezone.now()-timedelta(days=3)
        signal=EvidenceCorrelation.objects.get(artifact=artifact,kind='artifact.screenshot.sha256');EvidenceCorrelation.objects.filter(pk=signal.pk).update(last_seen=previous)
        artifact.metadata={'gallery':{'thumbnail':'local-derivative'}};artifact.save(update_fields=['metadata'])
        signal.refresh_from_db();self.assertEqual(signal.last_seen,previous)
