from datetime import timedelta
from io import StringIO
from django.test import TestCase
from django.core.management import call_command
from django.utils import timezone


class AssetBackfillTests(TestCase):
    def test_backfill_uses_retained_match_proof_without_assuming_scopes_matched(self):
        import hashlib
        from leakscope.phase3.state import ingest_observation,acquire_lease,visible_endpoints
        from leakscope.phase3.certificate_store import backfill_certificates
        from leakscope_app.models import WatchRule, CertificateWatchMatch, ServiceObservation
        watch=WatchRule.objects.create(name='Original',domains=['example.com'],auto_fetch=False)
        WatchRule.objects.create(name='Unproven',domains=['example.com'],auto_fetch=False)
        key='aa'*32;domain='api.example.com'
        ingest_observation('certstream',{'address':domain,'port':443,'source_id':key,
            'metadata':{'certificate':{'domains':[domain]}}})
        lease='certstream:'+hashlib.sha256(f'{key}:{domain}:{watch.pk}'.encode()).hexdigest()
        acquire_lease(lease,604800,{'watch':str(watch.pk)})
        backfill_certificates();backfill_certificates()
        self.assertEqual(CertificateWatchMatch.objects.count(),1)
        self.assertEqual(CertificateWatchMatch.objects.get().watch_id,watch.pk)
        self.assertEqual(ServiceObservation.objects.count(),1)
        self.assertFalse(visible_endpoints().exists())
    def test_backfill_is_local_idempotent_and_retains_source_records(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope_app.models import AssetNode, AssetEvidence, AssetRelationship, ServiceObservation
        ingest_observation('shodan', {'address':'192.0.2.4','port':8443,'service_type':'https',
            'metadata':{'hostnames':['api.example.com']}})
        call_command('backfill_asset_relationships',stdout=StringIO())
        before=(AssetNode.objects.count(),AssetEvidence.objects.count(),AssetRelationship.objects.count())
        call_command('backfill_asset_relationships',stdout=StringIO())
        self.assertEqual(before,(AssetNode.objects.count(),AssetEvidence.objects.count(),AssetRelationship.objects.count()))
        self.assertEqual(ServiceObservation.objects.count(),1)

    def test_certificate_historical_replay_preserves_newer_times_and_known_metadata(self):
        from leakscope.phase3.certificate_store import store_certificate
        now=timezone.now(); old=now-timedelta(days=7)
        key='ab'*32
        store_certificate({'certificate':{'domains':['example.com'],'sha256':key,'issuer':{'CN':'CA'}}},key,
            observed_at=now,collected_at=now)
        record=store_certificate({'certificate':{'domains':['api.example.com']}},key,observed_at=old,collected_at=old)
        self.assertEqual(record.first_seen,old)
        self.assertEqual(record.last_seen,now)
        self.assertEqual(record.observed_at,now)
        self.assertEqual(record.issuer,{'CN':'CA'})
        self.assertEqual(set(record.domains),{'example.com','api.example.com'})
