from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from leakscope_app.models import EvidenceArtifact, ObservationChange, ServiceObservation


class AnalysisTests(TestCase):
    def row(self, title='A', **metadata):
        return {'address':'192.0.2.11','port':443,'scheme':'https','title':title,'metadata':metadata}

    def test_volatile_provider_timestamps_and_collection_provenance_are_not_changes(self):
        from leakscope.phase3.state import ingest_observation
        ingest_observation('netlas',self.row(timestamp='2026-01-01',lookup_id='one',collection_scope={'country':'US'},http={'headers':{'date':'Monday','server':'fixture'}}))
        ingest_observation('netlas',self.row(timestamp='2026-02-01',lookup_id='two',collection_scope={'country':'CA'},http={'headers':{'date':'Tuesday','server':'fixture'}}))
        self.assertEqual(ServiceObservation.objects.count(),1)
        self.assertEqual(ObservationChange.objects.count(),0)
        self.assertEqual(ServiceObservation.objects.get().data['metadata']['lookup_id'],'one')

    def test_a_b_a_is_two_changes_with_only_two_unique_versions(self):
        from leakscope.phase3.state import ingest_observation
        for title in ['A','B','A']:ingest_observation('netlas',self.row(title))
        self.assertEqual(ServiceObservation.objects.count(),2)
        self.assertEqual(ObservationChange.objects.count(),2)
        self.assertEqual(list(ObservationChange.objects.order_by('collected_at').values_list('provider',flat=True)),['netlas','netlas'])

    def test_two_providers_are_not_temporal_changes(self):
        from leakscope.phase3.state import ingest_observation
        ingest_observation('netlas',self.row('A'))
        ingest_observation('shodan',self.row('B'))
        self.assertEqual(ObservationChange.objects.count(),0)

    def test_older_provider_snapshot_does_not_rewind_current_state(self):
        from leakscope.phase3.state import ingest_observation
        now=timezone.now()
        ingest_observation('netlas',dict(self.row('B'),observed_at=now))
        ingest_observation('netlas',dict(self.row('A'),observed_at=now-timedelta(days=1)))
        ingest_observation('netlas',dict(self.row('B'),observed_at=now+timedelta(seconds=1)))
        self.assertEqual(ObservationChange.objects.count(),0)
        self.assertEqual(ServiceObservation.objects.count(),2)

    def test_secret_configuration_is_loaded_once_per_ingest_not_per_string(self):
        from leakscope.phase3.state import ingest_observation
        with patch('leakscope.phase3.state.get_config',return_value={'config':{'NETLAS_API_KEY':'fixture-private'}}) as config:
            ingest_observation('netlas',self.row('fixture-private',array=['fixture-private']*100))
        self.assertEqual(config.call_count,1)
        self.assertNotIn('fixture-private',str(ServiceObservation.objects.get().data))

    def test_response_content_change_is_retained_but_cookie_and_date_are_not(self):
        from leakscope.phase3.state import ingest_observation
        ingest_observation('fetch:http',self.row(http={'headers':{'Date':'one','Set-Cookie':'a'},'body':'real content'}))
        ingest_observation('fetch:http',self.row(http={'headers':{'Date':'two','Set-Cookie':'b'},'body':'real content'}))
        ingest_observation('fetch:http',self.row(http={'headers':{'Date':'two','Set-Cookie':'b'},'body':'new content'}))
        self.assertEqual(ObservationChange.objects.count(),1)

    def test_analysis_masks_diff_values_and_counts_real_transitions(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope.phase3.analysis import build_analysis
        ingest_observation('netlas',self.row(credentials={'password':'first-private'}))
        ingest_observation('netlas',self.row(credentials={'password':'second-private'}))
        report=build_analysis()
        self.assertEqual(report['counts']['changes'],1)
        self.assertEqual(len(report['recent_changes']),1)
        self.assertNotIn('first-private',str(report))
        self.assertNotIn('second-private',str(report))

    def test_algorithm_specific_certificate_groups_do_not_conflate_hashes(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope.phase3.analysis import build_analysis
        fingerprint='ab'*32
        ingest_observation('netlas',self.row(certificate={'sha256':fingerprint}))
        ingest_observation('netlas',dict(self.row(certificate={'sha256':fingerprint}),address='192.0.2.12'))
        ingest_observation('netlas',dict(self.row(favicon={'sha256':fingerprint}),address='192.0.2.13'))
        relations=build_analysis()['relations']
        certificate=next(r for r in relations if r['kind']=='certificate.sha256')
        self.assertEqual(certificate['count'],2)
        self.assertEqual(len(certificate['endpoints']),2)
        self.assertFalse(any(r['kind']=='favicon.sha256' for r in relations))

    def test_artifact_relations_require_real_nonempty_hashed_artifacts(self):
        from leakscope.phase3.state import ingest_observation
        from leakscope.phase3.analysis import build_analysis
        for address in ('192.0.2.11','192.0.2.12'):
            endpoint=ingest_observation('fixture',dict(self.row(),address=address))
            EvidenceArtifact.objects.create(endpoint=endpoint,kind='response',relative_path='fixture',size=10,sha256='12'*32)
        relation=next(r for r in build_analysis()['relations'] if r['kind']=='artifact.response.sha256')
        self.assertEqual(relation['count'],2)

    def test_jsonb_unsafe_values_are_normalized_without_poisoning_other_rows(self):
        from leakscope.phase3.state import ingest_observation
        endpoint=ingest_observation('fixture',self.row('A\x00B',unsafe='bad\ud800',nan=float('nan'),inf=float('inf')))
        data=ServiceObservation.objects.get(endpoint=endpoint).data
        self.assertEqual(data['metadata']['nan'],None)
        self.assertEqual(data['metadata']['inf'],None)
        self.assertNotIn('\x00',data['title'])
        data['metadata']['unsafe'].encode('utf-8')

    def test_nested_business_timestamp_remains_meaningful(self):
        from leakscope.phase3.state import ingest_observation
        ingest_observation('fixture',self.row(records=[{'timestamp':'one'}]))
        ingest_observation('fixture',self.row(records=[{'timestamp':'two'}]))
        self.assertEqual(ObservationChange.objects.count(),1)

    def test_report_uses_bounded_index_queries_not_observation_json(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from leakscope.phase3.state import ingest_observation
        from leakscope.phase3.analysis import build_analysis
        ingest_observation('fixture',self.row(payload='x'*100000))
        with CaptureQueriesContext(connection) as queries:build_analysis()
        self.assertLessEqual(len(queries),20)
        for query in queries:
            self.assertNotIn('"leakscope_app_serviceobservation"."data"',query['sql'])

    def test_equivalent_hash_formats_are_not_a_change(self):
        from leakscope.phase3.state import ingest_observation
        ingest_observation('fixture',self.row(certificate={'sha256':'AB:'*31+'AB'}))
        ingest_observation('fixture',self.row(certificate={'sha256':'ab'*32}))
        self.assertEqual(ServiceObservation.objects.count(),1)
        self.assertEqual(ObservationChange.objects.count(),0)
