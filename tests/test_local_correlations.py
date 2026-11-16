"""Local image/response indexing never contacts targets or provider services."""
from io import BytesIO
import hashlib
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings
from leakscope_app.models import EvidenceArtifact, EvidenceCorrelation, Run, ServiceEndpoint, ServiceObservation


class LocalCorrelationTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.override = override_settings(LEAKSCOPE_CAPTURE_DIR=self.root)
        self.override.enable(); self.addCleanup(self.override.disable)
        self.endpoint = ServiceEndpoint.objects.create(key='first', address='192.0.2.1', port=443)
        self.proof = ServiceObservation.objects.create(endpoint=self.endpoint, provider='fetch:screenshot', fingerprint='first')

    def image(self, shift=0):
        from PIL import Image
        image = Image.new('L', (90, 80))
        image.putdata([((x//10 + y//10 + shift) % 2)*240 for y in range(80) for x in range(90)])
        output = BytesIO(); image.save(output, format='PNG'); return output.getvalue()

    def artifact(self, data=None, kind='screenshot', endpoint=None, metadata=None, filename='capture.png'):
        data = self.image() if data is None else data
        (self.root/filename).write_bytes(data)
        return EvidenceArtifact.objects.create(endpoint=endpoint or self.endpoint,kind=kind,relative_path=filename,
            size=len(data),sha256=hashlib.sha256(data).hexdigest(),mime='image/png' if kind=='screenshot' else 'text/plain',
            tool='fixture',metadata=metadata if metadata is not None else {'observation_pk':str(self.proof.pk)})

    def test_actual_image_dhash_is_stable_and_blank_images_are_excluded(self):
        from PIL import Image
        from leakscope.phase3.local_correlations import screenshot_dhash
        value = screenshot_dhash(self.image())
        self.assertRegex(value, '^[0-9a-f]{16}$')
        self.assertEqual(value, screenshot_dhash(self.image()))
        output = BytesIO(); Image.new('RGB',(50,50),'white').save(output,format='PNG')
        self.assertIsNone(screenshot_dhash(output.getvalue()))

    def test_real_png_small_change_is_near_and_inverted_pattern_is_different(self):
        from PIL import Image
        from leakscope.phase3.local_correlations import screenshot_dhash
        base=Image.new('L',(9,8));base.putdata([(x%2)*240 for y in range(8) for x in range(9)])
        near=base.copy();near.putpixel((4,4),255)
        different=Image.eval(base,lambda pixel:240-pixel)
        hashes=[]
        for image in [base,near,different]:
            output=BytesIO();image.save(output,format='PNG');hashes.append(int(screenshot_dhash(output.getvalue()),16))
        self.assertTrue(1 <= (hashes[0]^hashes[1]).bit_count() <= 2)
        self.assertGreater((hashes[0]^hashes[2]).bit_count(),8)

    def test_analytics_extracts_only_bounded_public_identifiers(self):
        from leakscope.phase3.local_correlations import analytics_ids
        values = analytics_ids(b"<script>gtag('config','G-ABC1234567'); 'UA-12345-6'; 'GTM-ABC12';</script> token=private")
        self.assertEqual(values, [('tracking.google_analytics','G-ABC1234567'),('tracking.google_analytics','UA-12345-6'),('tracking.google_tag_manager','GTM-ABC12')])
        self.assertEqual(analytics_ids(b'G-ABC1234567 '+b'x'*1048576+b' GTM-LATE12'), [('tracking.google_analytics','G-ABC1234567')])
        self.assertEqual(analytics_ids(b'password=private API-key=G-nope lowercase g-abc1234567 XG-ABC1234567 GTM-X UA-1234'),[])

    def test_byte_and_decoded_pixel_limits_apply_before_indexing(self):
        from leakscope.phase3.local_correlations import artifact_signals, screenshot_dhash
        artifact=self.artifact()
        with patch('leakscope.phase3.local_correlations.FILE_LIMIT',16):
            with self.assertRaises(ValueError):artifact_signals(artifact)
        with patch('leakscope.phase3.local_correlations.PIXEL_LIMIT',100):
            with self.assertRaises(ValueError):screenshot_dhash(self.image())

    def test_bomb_header_error_is_counted_without_stopping_the_whole_batch(self):
        from PIL import Image
        from leakscope.phase3.local_correlations import execute_index
        artifact=self.artifact();run=Run.objects.create(kind='correlation_index',task_id='bomb-image')
        with patch('PIL.Image.open',side_effect=Image.DecompressionBombError('oversized header')):
            result=execute_index(run.pk,run.task_id,[str(artifact.pk)])
        self.assertEqual(result['errors'],1);self.assertTrue(result['partial'])
        run.refresh_from_db();self.assertEqual(run.state,'succeeded')

    def test_worker_indexes_saved_artifact_and_duplicate_execution_is_idempotent(self):
        from leakscope.phase3.local_correlations import execute_index
        artifact = self.artifact()
        run = Run.objects.create(kind='correlation_index',task_id='task-one')
        result = execute_index(run.pk,'task-one',[str(artifact.pk)])
        self.assertEqual(result['indexed'],1)
        signal = EvidenceCorrelation.objects.get(kind='screenshot.dhash64')
        self.assertEqual(signal.artifact_id,artifact.pk)
        execute_index(run.pk,'task-one',[str(artifact.pk)])
        self.assertEqual(EvidenceCorrelation.objects.filter(kind='screenshot.dhash64').count(),1)

    def test_response_indexing_is_local_and_links_the_exact_artifact(self):
        from leakscope.phase3.local_correlations import execute_index
        artifact = self.artifact(b"HTTP/1.1 200 OK\r\n\r\ngtag('config','G-ABC1234567'); fbq('init','123456789012345');",kind='http',filename='response.txt')
        run = Run.objects.create(kind='correlation_index',task_id='response-task')
        with patch('requests.get',side_effect=AssertionError('No target requests')):
            execute_index(run.pk,run.task_id,[str(artifact.pk)])
        signal = EvidenceCorrelation.objects.get(kind='tracking.google_analytics')
        self.assertEqual(signal.artifact_id,artifact.pk)
        self.assertEqual(signal.value,'G-ABC1234567')
        marketing = EvidenceCorrelation.objects.get(kind='tracking.meta_pixel')
        self.assertEqual(marketing.artifact_id, artifact.pk)
        self.assertEqual(marketing.value, '123456789012345')

    def test_duplicate_hashes_preserve_newest_proof_and_capture_time_order(self):
        from datetime import timedelta
        from leakscope.phase3.local_correlations import execute_index
        newer=self.artifact(filename='new.png');older=self.artifact(filename='old.png')
        older.created_at=newer.created_at-timedelta(days=2)
        EvidenceArtifact.objects.filter(pk=older.pk).update(created_at=older.created_at)
        run=Run.objects.create(kind='correlation_index',task_id='capture-order')
        execute_index(run.pk,run.task_id,[str(newer.pk),str(older.pk)])
        signal=EvidenceCorrelation.objects.get(kind='screenshot.dhash64')
        self.assertEqual(signal.artifact_id,newer.pk)
        self.assertEqual(signal.first_seen,older.created_at)
        self.assertEqual(signal.last_seen,newer.created_at)

    def test_hidden_source_empty_legacy_source_invalid_and_uppercase_source_visibility(self):
        from leakscope.phase3.local_correlations import visible_artifacts
        artifact = self.artifact()
        self.assertTrue(visible_artifacts().filter(pk=artifact.pk).exists())
        artifact.metadata={'observation_pk':str(self.proof.pk).upper()};artifact.save()
        self.assertTrue(visible_artifacts().filter(pk=artifact.pk).exists())
        self.proof.hidden=True;self.proof.save()
        ServiceObservation.objects.create(endpoint=self.endpoint,provider='shodan',fingerprint='visible')
        self.assertFalse(visible_artifacts().filter(pk=artifact.pk).exists())
        for value in ['',None,'invalid']:
            artifact.metadata={'observation_pk':value};artifact.save()
            self.assertFalse(visible_artifacts().filter(pk=artifact.pk).exists())

    def test_path_escape_symlinks_and_hash_mismatch_are_skipped(self):
        from leakscope.phase3.local_correlations import execute_index
        artifact = self.artifact()
        (self.root/'link.png').symlink_to(self.root/'capture.png')
        for index,path in enumerate(['../capture.png','link.png']):
            artifact.relative_path=path;artifact.save()
            run=Run.objects.create(kind='correlation_index',task_id=f'path-{index}')
            self.assertEqual(execute_index(run.pk,run.task_id,[str(artifact.pk)])['indexed'],0)
        artifact.relative_path='capture.png';artifact.sha256='0'*64;artifact.save()
        run=Run.objects.create(kind='correlation_index',task_id='hash')
        self.assertEqual(execute_index(run.pk,run.task_id,[str(artifact.pk)])['indexed'],0)
        self.assertFalse(EvidenceCorrelation.objects.filter(kind='screenshot.dhash64').exists())

    def test_cancel_or_replaced_task_during_file_processing_does_not_persist(self):
        from leakscope.phase3.local_correlations import execute_index
        artifact = self.artifact()
        for replaced in [False,True]:
            run = Run.objects.create(kind='correlation_index',task_id='old-'+str(replaced))
            def process(row):
                Run.objects.filter(pk=run.pk).update(**({'task_id':'replacement'} if replaced else {'state':'cancelled'}))
                return [('screenshot.dhash64','1234567890abcdef')]
            with patch('leakscope.phase3.local_correlations.artifact_signals',side_effect=process):
                execute_index(run.pk,run.task_id,[str(artifact.pk)])
        self.assertFalse(EvidenceCorrelation.objects.filter(kind='screenshot.dhash64').exists())

    def test_already_cancelled_job_never_opens_a_file(self):
        from leakscope.phase3.local_correlations import execute_index
        artifact=self.artifact();run=Run.objects.create(kind='correlation_index',task_id='cancelled',state='cancelled')
        with patch('leakscope.phase3.local_correlations.artifact_signals',side_effect=AssertionError('must not read')):
            result=execute_index(run.pk,run.task_id,[str(artifact.pk)])
        self.assertTrue(result['cancelled'])

    def test_malformed_image_is_an_honest_partial_result_without_a_signal(self):
        from leakscope.phase3.local_correlations import execute_index
        artifact=self.artifact(b'not an image');run=Run.objects.create(kind='correlation_index',task_id='bad-image')
        result=execute_index(run.pk,run.task_id,[str(artifact.pk)])
        self.assertTrue(result['partial']);self.assertEqual(result['errors'],1)
        self.assertFalse(EvidenceCorrelation.objects.filter(kind='screenshot.dhash64').exists())

    def test_skipped_and_failed_files_publish_initial_and_incremental_progress(self):
        import uuid
        from leakscope.phase3.local_correlations import execute_index
        run=Run.objects.create(kind='correlation_index',task_id='progress')
        with patch('leakscope.phase3.local_correlations.write_state') as states:
            execute_index(run.pk,run.task_id,[str(uuid.uuid4()),str(uuid.uuid4())])
        progress=[call.args[2] for call in states.call_args_list if call.args[1]=='PROGRESS']
        self.assertEqual([row['current'] for row in progress],[0,1,2])
        self.assertTrue(all(row['total']==2 for row in progress))

    def test_queue_snapshots_bounded_scoped_files_without_reading_them(self):
        from leakscope.phase3.local_correlations import queue_reindex
        artifact=self.artifact()
        with patch('leakscope.phase3.local_correlations.enqueue') as enqueue,patch('leakscope.phase3.local_correlations.artifact_signals',side_effect=AssertionError('queue does not read files')):
            queue_reindex({'limit':1},endpoints=ServiceEndpoint.objects.filter(pk=self.endpoint.pk))
        self.assertEqual(enqueue.call_args.kwargs['kind'],'correlation_index')
        self.assertEqual(enqueue.call_args.kwargs['kwargs']['artifact_ids'],[str(artifact.pk)])
        for value in [0,1001,True,1.5]:
            with self.assertRaises(ValueError):queue_reindex({'limit':value})

    def test_similarity_is_distance_bounded_not_transitive_and_reports_sampling(self):
        from leakscope.phase3.local_correlations import hamming_groups
        rows=[{'id':'a','value':'1234567890abcdef','artifact_id':'one'},
              {'id':'b','value':'1234567890abcdee','artifact_id':'two'},
              {'id':'c','value':'fedcba0987654321','artifact_id':'three'}]
        result=hamming_groups(rows,threshold=1,limit=2)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['results'][0]['endpoint_count'],2)
        self.assertEqual([m['distance'] for m in result['results'][0]['members']],[0,1])
        self.assertIn('ownership',result['basis'])
        with self.assertRaises(ValueError):hamming_groups(rows,threshold=9)

    def test_similarity_reads_only_persisted_visible_signals_without_opening_files(self):
        from leakscope.phase3.local_correlations import screenshot_similarity
        first=self.artifact()
        other=ServiceEndpoint.objects.create(key='other',address='192.0.2.2',port=443)
        proof=ServiceObservation.objects.create(endpoint=other,provider='fetch:screenshot',fingerprint='other')
        second=self.artifact(endpoint=other,metadata={'observation_pk':str(proof.pk)},filename='second.png')
        for artifact,value in [(first,'1234567890abcdef'),(second,'1234567890abcdee')]:
            EvidenceCorrelation.objects.create(endpoint=artifact.endpoint,artifact=artifact,kind='screenshot.dhash64',value=value)
        with patch('leakscope.phase3.local_correlations._read_artifact',side_effect=AssertionError('read-only browse')):
            result=screenshot_similarity(EvidenceCorrelation.objects.all(),threshold=1)
        self.assertEqual(result['results'][0]['endpoint_count'],2)
        proof.hidden=True;proof.save()
        ServiceObservation.objects.create(endpoint=other,provider='shodan',fingerprint='visible')
        self.assertEqual(screenshot_similarity(EvidenceCorrelation.objects.all())['results'],[])


class PublicMarketingIdentifierTests(SimpleTestCase):
    def test_vendor_initializations_identify_public_projects(self):
        from leakscope.phase3.local_correlations import analytics_ids
        body = b"""
            fbq('init', '123456789012345');
            _linkedin_partner_id = "987654";
            window.uetq = new UET({ti:"12345678", enableAutoSpaTracking:true});
            ttq.load('C9AB12CD34EF56GH78JK');
            window._hjSettings={hjid:1234567,hjsv:6};
            gtag('config', 'AW-123456789');
            <script data-ad-client="ca-pub-1234567890123456"></script>
            <script src="https://js-na1.hs-scripts.com/98765432.js"></script>
            <script src="https://www.clarity.ms/tag/abc123def4"></script>
        """
        expected = {
            ('tracking.meta_pixel', '123456789012345'),
            ('tracking.linkedin_partner', '987654'),
            ('tracking.microsoft_uet', '12345678'),
            ('tracking.tiktok_pixel', 'C9AB12CD34EF56GH78JK'),
            ('tracking.hotjar_site', '1234567'),
            ('tracking.google_ads', 'AW-123456789'),
            ('tracking.google_adsense', 'ca-pub-1234567890123456'),
            ('tracking.hubspot_portal', '98765432'),
            ('tracking.microsoft_clarity', 'abc123def4'),
        }
        self.assertEqual(set(analytics_ids(body)), expected)

    def test_known_pixel_urls_parse_html_entities_and_vendor_scoped_parameters(self):
        from leakscope.phase3.local_correlations import analytics_ids
        body = b"""
            <img src="https://www.facebook.com/tr?ev=PageView&amp;id=123456789012345">
            <img src='//px.ads.linkedin.com/collect/?fmt=gif&amp;pid=987654'>
            <img src="https://bat.bing.com/action/0?ver=2&amp;ti=12345678">
            <script src="https://static.hotjar.com/c/hotjar-1234567.js?sv=6"></script>
            <script src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1234567890123456"></script>
        """
        self.assertEqual(set(analytics_ids(body)), {
            ('tracking.meta_pixel', '123456789012345'),
            ('tracking.linkedin_partner', '987654'),
            ('tracking.microsoft_uet', '12345678'),
            ('tracking.hotjar_site', '1234567'),
            ('tracking.google_adsense', 'ca-pub-1234567890123456'),
        })

    def test_arbitrary_ids_cookies_credentials_and_lookalike_hosts_are_ignored(self):
        from leakscope.phase3.local_correlations import analytics_ids
        body = b"""
            pixel_id=123456789012345; partner_id=987654; ti=12345678;
            {hjid:1234567}; project='abc123def4'; portalId=98765432;
            customer_id='123456789012345'; cookie='_fbp.123456789012345';
            access_token='123456789012345'; api_key='C9AB12CD34EF56GH78JK';
            fbq('track', '123456789012345'); myfbq('init', '123456789012345');
            ttq.load('YOUR_PIXEL_ID'); fbq('init', '0');
            <script src="https://js.hs-scripts.com.evil.test/98765432.js"></script>
            <img src="https://facebook.com@evil.test/tr?id=123456789012345">
            <img src="https://evil.test/?next=https://www.facebook.com/tr?id=123456789012345">
            <script src="https://evil.test/clarity.ms/tag/abc123def4"></script>
            <script src="https://js.hs-scripts.com/98765432.js/other"></script>
        """
        self.assertEqual(analytics_ids(body), [])

    def test_public_identifier_output_and_scan_bytes_are_bounded(self):
        from leakscope.phase3.local_correlations import analytics_ids, RESPONSE_LIMIT
        many = '\n'.join("fbq('init', '%s');" % value for value in range(100000, 100100)).encode()
        values = analytics_ids(many)
        self.assertEqual(len(values), 64)
        self.assertEqual(values, sorted(set(values)))
        self.assertEqual(analytics_ids(b'x' * RESPONSE_LIMIT + many), [])


    def test_equal_numeric_ids_from_different_vendors_keep_their_namespaces(self):
        from leakscope.phase3.local_correlations import analytics_ids
        self.assertEqual(analytics_ids(b"fbq('init','123456'); _linkedin_partner_id='123456';"), [
            ('tracking.linkedin_partner', '123456'),
            ('tracking.meta_pixel', '123456'),
        ])

    def test_malformed_literals_and_ambiguous_pixel_urls_are_not_indexed(self):
        from leakscope.phase3.local_correlations import analytics_ids
        body = b"""
            fbq('init", '123456789012345');
            _linkedin_partner_id = '987654";
            <script not-data-ad-client="ca-pub-1234567890123456"></script>
            <img src="https://www.facebook.com/tr?id=123456789012345&amp;id=987654321098765">
            <img src="https://www.facebook.com/tr?id=&amp;id=123456789012345">
        """
        self.assertEqual(analytics_ids(body), [])


    def test_uet_base_snippet_requires_the_same_configuration_variable(self):
        from leakscope.phase3.local_correlations import analytics_ids
        body = b'var o={ti:"12345678", enableAutoSpaTracking:true};o.q=window.uetq;window.uetq=new UET(o);'
        self.assertEqual(analytics_ids(body), [('tracking.microsoft_uet', '12345678')])
        self.assertEqual(analytics_ids(body.replace(b'new UET(o)', b'new UET(other)')), [])
