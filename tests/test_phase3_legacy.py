import io

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from leakscope_app.models import Elastic, Javascript, Search, ServiceEndpoint, ServiceObservation, ObservationChange


class LegacyBridgeTests(TestCase):
    def setUp(self):
        self.search=Search.objects.create(type='elastic',keyword='fixture',network='192.0.2.0/24',country='',provider='zoomeye')

    def make(self):
        return Elastic.objects.create(search=self.search,ip='192.0.2.8',port='9200',name='Fixture',indicator='private-indicator',indices='[]')

    def test_baseline_test_settings_disable_bridge(self):
        self.make()
        self.assertFalse(ServiceEndpoint.objects.exists())

    @override_settings(LEAKSCOPE_NORMALIZE_LEGACY=True)
    def test_legacy_save_normalizes_and_preserves_provenance_without_sensitive_fields(self):
        row=self.make()
        endpoint=ServiceEndpoint.objects.get()
        self.assertEqual((endpoint.address,endpoint.port,endpoint.service_type),('192.0.2.8',9200,'elastic'))
        observation=ServiceObservation.objects.get()
        self.assertEqual(observation.provider,'zoomeye')
        self.assertEqual(observation.data['metadata']['legacy_reference'],f'elastic:{row.pk}')
        self.assertNotIn('private-indicator',str(observation.data))

    @override_settings(LEAKSCOPE_NORMALIZE_LEGACY=True)
    def test_review_changes_are_not_service_changes_and_repeated_save_is_idempotent(self):
        row=self.make();row.save();row.confirmed=True;row.save()
        self.assertEqual(ServiceObservation.objects.count(),1)
        self.assertEqual(ObservationChange.objects.count(),0)
        row.name='Changed';row.save()
        self.assertEqual(ObservationChange.objects.count(),1)

    @override_settings(LEAKSCOPE_NORMALIZE_LEGACY=True)
    def test_non_network_direct_finding_does_not_invent_an_endpoint(self):
        Javascript.objects.create(path='/private/file.js',context='private-context',secrets='private-secret')
        self.assertFalse(ServiceEndpoint.objects.exists())

    def test_backfill_is_dry_run_by_default_and_apply_is_idempotent(self):
        self.make();output=io.StringIO();call_command('backfill_observations',stdout=output)
        self.assertFalse(ServiceEndpoint.objects.exists())
        self.assertIn('dry-run',output.getvalue())
        call_command('backfill_observations',apply=True,stdout=io.StringIO())
        call_command('backfill_observations',apply=True,stdout=io.StringIO())
        self.assertEqual(ServiceObservation.objects.count(),1)

    @override_settings(LEAKSCOPE_NORMALIZE_LEGACY=True)
    def test_bridge_failure_does_not_abort_original_save(self):
        from unittest.mock import patch
        with patch('leakscope_app.signals.bridge_legacy',side_effect=ValueError('fixture failure')):
            row=self.make()
        self.assertTrue(Elastic.objects.filter(pk=row.pk).exists())

    @override_settings(LEAKSCOPE_NORMALIZE_LEGACY=True)
    def test_duplicate_rows_keep_both_legacy_links_and_tag_projection(self):
        from leakscope.phase3.state import set_tags,tag_names
        first=self.make();second=self.make()
        self.assertEqual(ServiceEndpoint.objects.count(),1)
        self.assertEqual(ServiceObservation.objects.count(),2)
        self.assertEqual(ObservationChange.objects.count(),0)
        endpoint=ServiceEndpoint.objects.get()
        set_tags('endpoint',endpoint.pk,['manual'])
        set_tags('finding',f'elastic:{second.pk}',['second'])
        self.assertEqual(tag_names('endpoint',endpoint.pk),['manual','second'])
        set_tags('finding',f'elastic:{second.pk}',[])
        self.assertEqual(tag_names('endpoint',endpoint.pk),['manual'])

    @override_settings(LEAKSCOPE_NORMALIZE_LEGACY=True)
    def test_corrected_provider_lineage_is_not_a_service_transition(self):
        row=self.make();row.provider='shodan';row.save()
        self.assertEqual(ServiceObservation.objects.count(),2)
        self.assertEqual(ObservationChange.objects.count(),0)


@override_settings(LEAKSCOPE_NORMALIZE_LEGACY=True)
class LegacyVisibilityTests(TestCase):
    def make(self,port='9200',name='Private legacy'):
        search=Search.objects.create(type='elastic',keyword='fixture',network='',country='',provider='zoomeye')
        return Elastic.objects.create(search=search,ip='192.0.2.8',port=port,name=name,indices='[]')

    def test_hidden_legacy_versions_are_not_visible_assets_or_analysis(self):
        from leakscope_app.models import FindingReview
        from leakscope.phase3.state import visible_endpoints
        from leakscope.phase3.analysis import build_analysis
        row=self.make();row.name='Changed private';row.save()
        FindingReview.objects.create(type='elastic',object_id=row.pk,hidden=True)
        self.assertFalse(visible_endpoints().exists())
        self.assertTrue(all(ServiceObservation.objects.values_list('hidden',flat=True)))
        result=build_analysis()
        self.assertEqual(result['counts']['endpoints'],0)
        self.assertEqual(result['counts']['observations'],0)
        self.assertEqual(result['counts']['changes'],0)
        self.assertEqual(result['recent_changes'],[])

    def test_unhide_restores_without_rewriting_history(self):
        from leakscope_app.models import FindingReview
        from leakscope.phase3.state import visible_endpoints
        row=self.make();review=FindingReview.objects.create(type='elastic',object_id=row.pk,hidden=True)
        count=ServiceObservation.objects.count()
        review.hidden=False;review.save()
        self.assertEqual(visible_endpoints().count(),1)
        self.assertEqual(ServiceObservation.objects.count(),count)
        self.assertFalse(ServiceObservation.objects.get().hidden)

    def test_an_independent_source_keeps_asset_but_not_hidden_legacy_details(self):
        from leakscope_app.models import FindingReview
        from leakscope.phase3.state import ingest_observation,visible_endpoints
        from leakscope.phase3.analysis import build_analysis
        row=self.make()
        ingest_observation('netlas',{'address':'192.0.2.8','port':9200,'service_type':'elastic','title':'Public source','metadata':{}})
        row.name='Changed private';row.save()
        FindingReview.objects.create(type='elastic',object_id=row.pk,hidden=True)
        endpoint=visible_endpoints().get()
        self.assertEqual(endpoint.title,'Public source')
        result=build_analysis()
        self.assertEqual(result['counts']['endpoints'],1)
        self.assertEqual(result['counts']['observations'],1)
        self.assertNotIn('Private legacy',str(result))
        self.assertNotIn('Changed private',str(result))

    def test_artifacts_alone_do_not_resurrect_a_hidden_source(self):
        from leakscope_app.models import FindingReview,EvidenceArtifact
        from leakscope.phase3.state import visible_endpoints
        from leakscope.phase3.analysis import build_analysis
        row=self.make();endpoint=ServiceEndpoint.objects.get()
        FindingReview.objects.create(type='elastic',object_id=row.pk,hidden=True)
        EvidenceArtifact.objects.create(endpoint=endpoint,kind='response',relative_path='fixture',size=10,sha256='ab'*32)
        self.assertFalse(visible_endpoints().exists())
        self.assertEqual(build_analysis()['counts']['artifacts'],0)

    def test_legacy_fingerprint_alias_and_port_hide_is_projected_and_reversible(self):
        from leakscope_app.models import FingerprintLog
        from leakscope.services.identity import fingerprint
        from leakscope.phase3.state import visible_endpoints
        self.make();self.make(port='9300',name='Different port')
        log=FingerprintLog.objects.create(fingerprint=fingerprint('elastic','192.0.2.8','9200'),type='elasticsearch',ip='192.0.2.8',port='9200',status='hidden')
        self.assertEqual(list(visible_endpoints().values_list('port',flat=True)),[9300])
        log.delete()
        self.assertEqual(visible_endpoints().count(),2)

    def test_one_duplicate_row_hide_does_not_hide_another_source_row(self):
        from leakscope_app.models import FindingReview
        from leakscope.phase3.state import visible_endpoints
        row=self.make();other=self.make()
        FindingReview.objects.create(type='elastic',object_id=row.pk,hidden=True)
        self.assertEqual(visible_endpoints().count(),1)
        self.assertTrue(ServiceObservation.objects.get(source_id=f'legacy:elastic:{row.pk}').hidden)
        self.assertFalse(ServiceObservation.objects.get(source_id=f'legacy:elastic:{other.pk}').hidden)

    def test_shared_raw_correlation_proof_survives_hiding_legacy_pointer(self):
        from leakscope_app.models import FindingReview
        from leakscope.phase3.state import ingest_observation
        from leakscope.phase3.analysis import build_analysis
        row=self.make();fingerprint='ab'*32
        source={'address':'192.0.2.8','port':9200,'title':'Raw title','metadata':{'certificate':{'sha256':fingerprint}}}
        ingest_observation('netlas',source)
        ingest_observation('zoomeye',dict(source,source_id=f'legacy:elastic:{row.pk}',metadata={'legacy_reference':f'elastic:{row.pk}','certificate':{'sha256':fingerprint}}))
        ingest_observation('netlas',dict(source,address='192.0.2.9'))
        FindingReview.objects.create(type='elastic',object_id=row.pk,hidden=True)
        relation=next(item for item in build_analysis()['relations'] if item['kind']=='certificate.sha256')
        self.assertEqual(relation['count'],2)
        for endpoint in relation['endpoints']:
            self.assertFalse(ServiceObservation.objects.get(pk=endpoint['observation_id']).hidden)

    def test_source_tags_hide_and_restore_without_removing_manual_asset_tags(self):
        from leakscope_app.models import FindingReview
        from leakscope.phase3.state import set_tags,tag_names,ingest_observation
        row=self.make();endpoint=ServiceEndpoint.objects.get()
        ingest_observation('netlas',{'address':'192.0.2.8','port':9200,'metadata':{}})
        set_tags('endpoint',endpoint.pk,['manual'])
        set_tags('finding',f'elastic:{row.pk}',['source'])
        review=FindingReview.objects.create(type='elastic',object_id=row.pk,hidden=True)
        self.assertEqual(tag_names('endpoint',endpoint.pk),['manual'])
        set_tags('finding',f'elastic:{row.pk}',['edited'])
        self.assertEqual(tag_names('endpoint',endpoint.pk),['manual'])
        review.hidden=False;review.save()
        self.assertEqual(tag_names('endpoint',endpoint.pk),['edited','manual'])

    def test_fingerprint_seen_counters_do_not_reindex_unchanged_visibility(self):
        from unittest.mock import patch
        from leakscope_app.models import FingerprintLog
        from leakscope.services.identity import fingerprint
        self.make()
        log=FingerprintLog.objects.create(fingerprint=fingerprint('elastic','192.0.2.8','9200'),type='elastic',ip='192.0.2.8',port='9200',status='hidden')
        with patch('leakscope_app.signals.sync_legacy_visibility') as sync:
            log.seen_count+=1;log.save(update_fields=['seen_count'])
        sync.assert_not_called()


class LegacyVisibilityMigrationTests(TransactionTestCase):
    def test_existing_hidden_projection_is_migrated_and_proof_memberships_retained(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor=MigrationExecutor(connection)
        latest=executor.loader.graph.leaf_nodes()
        before=[('leakscope_app','0021_lookup_custom_query_lineage')]
        executor.migrate(before)
        try:
            apps=executor.loader.project_state(before).apps
            SearchOld=apps.get_model('leakscope_app','Search')
            ElasticOld=apps.get_model('leakscope_app','Elastic')
            EndpointOld=apps.get_model('leakscope_app','ServiceEndpoint')
            ObservationOld=apps.get_model('leakscope_app','ServiceObservation')
            ReviewOld=apps.get_model('leakscope_app','FindingReview')
            CorrelationOld=apps.get_model('leakscope_app','EvidenceCorrelation')
            search=SearchOld.objects.create(type='elastic',keyword='fixture',network='',country='',provider='zoomeye')
            finding=ElasticOld.objects.create(search=search,ip='192.0.2.8',port='9200',name='Private',provider='zoomeye')
            reference=f'elastic:{finding.pk}'
            endpoint=EndpointOld.objects.create(key='1'*64,address='192.0.2.8',port=9200,title='Private',service_type='elastic')
            observation=ObservationOld.objects.create(endpoint=endpoint,provider='zoomeye',source_id='legacy:'+reference,fingerprint='2'*64,
                data={'title':'Private','service_type':'elastic','metadata':{'legacy_reference':reference}})
            ReviewOld.objects.create(type='elastic',object_id=finding.pk,hidden=True)
            CorrelationOld.objects.create(endpoint=endpoint,observation=observation,kind='certificate.sha256',value='ab'*32)
            executor=MigrationExecutor(connection);executor.migrate([('leakscope_app','0022_legacy_observation_visibility')])
            after=executor.loader.project_state([('leakscope_app','0022_legacy_observation_visibility')]).apps
            self.assertTrue(after.get_model('leakscope_app','ServiceObservation').objects.get(pk=observation.pk).hidden)
            self.assertEqual(after.get_model('leakscope_app','ServiceEndpoint').objects.get(pk=endpoint.pk).title,'')
            self.assertEqual(after.get_model('leakscope_app','EvidenceCorrelation').objects.get().observations.count(),1)
        finally:
            MigrationExecutor(connection).migrate(latest)
