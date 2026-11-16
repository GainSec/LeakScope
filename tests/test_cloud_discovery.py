import json
from unittest.mock import patch

from django.test import Client, SimpleTestCase, TestCase


class CloudPlannerTests(SimpleTestCase):
    def plan(self, **values):
        from leakscope.phase3.cloud import plan
        data = {'names': ['acme'], 'providers': ['aws'], **values}
        if data['providers'] == ['aws'] and 'regions' not in data: data['regions'] = {'aws': ['global']}
        return plan(data)

    def test_preview_generates_exact_names_without_network_or_keys(self):
        with patch('socket.getaddrinfo', side_effect=AssertionError('network forbidden')):
            result = self.plan()
        self.assertEqual(result['candidates'][0]['url'], 'https://acme.s3.amazonaws.com/')
        self.assertFalse(result['network_contact'])
        self.assertEqual(result['spec']['permutations'], False)

    def test_regions_are_provider_specific_and_limits_are_fair(self):
        result = self.plan(providers=['aws', 'alibaba', 'scaleway'], max_requests=3)
        self.assertEqual({x['provider'] for x in result['candidates']}, {'aws', 'alibaba', 'scaleway'})
        self.assertTrue(result['limited'])
        result = self.plan(providers=['scaleway'], regions={'scaleway': ['pl-waw']})
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['url'], 'https://acme.s3.pl-waw.scw.cloud/')

    def test_mutations_are_opt_in_and_names_are_deduplicated(self):
        exact = self.plan(names=['ACME', 'acme'])
        self.assertEqual(len(exact['candidates']), 1)
        expanded = self.plan(permutations=True)
        self.assertIn('acme-dev', {c['name'] for c in expanded['candidates']})

    def test_rejects_arbitrary_destinations_unknown_fields_and_unbounded_input(self):
        for values in [{'names': ['https://localhost/']}, {'names': ['127.0.0.1']},
                       {'providers': ['invented']}, {'max_requests': True},
                       {'max_requests': 501}, {'rate_per_second': 10},
                       {'regions': {'aws': ['internal.example']}}, {'url': 'http://localhost'},
                       {'permutations': 'false'}, {'names': 'acme'}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.plan(**values)

    def test_hosted_app_patterns_and_additional_cloud_providers(self):
        result = self.plan(providers=['google', 'azure', 'digitalocean', 'alibaba', 'vultr', 'linode', 'hetzner', 'dreamhost', 'scaleway', 'firebase'], kinds=['storage', 'applications'], max_requests=500)
        urls = [c['url'] for c in result['candidates']]
        self.assertIn('https://acme.appspot.com/', urls)
        self.assertIn('https://acme.azurewebsites.net/', urls)
        self.assertIn('https://acme.s3.us-east-005.dream.io/', urls)
        self.assertTrue(any('aliyuncs.com' in u for u in urls))
        self.assertTrue(any('your-objectstorage.com' in u for u in urls))


class CloudResponseTests(SimpleTestCase):
    def classify(self, status, body=b'', headers=None, **candidate):
        from leakscope.phase3.cloud import classify
        return classify({'provider': 'aws', 'name': 'acme', 'kind': 'bucket', 'url': 'https://acme.s3.amazonaws.com/', **candidate},
                        {'status': status, 'headers': headers or {}, 'body': body, 'truncated': False})

    def test_public_requires_valid_listing_and_matching_bucket_identity(self):
        result = self.classify(200, b'<ListBucketResult><Name>acme</Name><Contents><Key>a.pdf</Key></Contents></ListBucketResult>')
        self.assertEqual(result['state'], 'public')
        self.assertEqual(result['sample_names'], ['a.pdf'])
        self.assertTrue(result['identity_verified'])
        for body in [b'<html>Welcome</html>', b'<ListBucketResult><Name>another-bucket</Name></ListBucketResult>']:
            self.assertEqual(self.classify(200, body)['state'], 'unverified')

    def test_denial_does_not_claim_public_access_or_verified_identity(self):
        result = self.classify(403, b'<Error><Code>AccessDenied</Code></Error>')
        self.assertEqual(result['state'], 'access_denied')
        self.assertFalse(result['identity_verified'])
        self.assertFalse(result['public_access'])
        self.assertEqual(self.classify(403, b'<html>Forbidden</html>')['state'], 'unverified')

    def test_missing_redirect_throttle_and_truncated_responses(self):
        self.assertEqual(self.classify(404, b'<Error><Code>NoSuchBucket</Code></Error>')['state'], 'not_found')
        self.assertEqual(self.classify(301, headers={'location': 'http://127.0.0.1/'})['state'], 'redirect')
        self.assertEqual(self.classify(429)['state'], 'rate_limited')
        from leakscope.phase3.cloud import classify
        result = classify({'provider': 'aws', 'name': 'acme', 'kind': 'bucket'},
                          {'status': 200, 'headers': {}, 'body': b'<ListBucketResult>', 'truncated': True})
        self.assertEqual(result['state'], 'unverified')

    def test_azure_account_and_firebase_are_not_mislabelled_as_s3_buckets(self):
        result = self.classify(200, b'<EnumerationResults><Containers><Container><Name>media</Name></Container></Containers></EnumerationResults>', provider='azure', kind='account')
        self.assertEqual(result['sample_names'], ['media'])
        result = self.classify(200, b'{"public-node":true}', provider='firebase', kind='database')
        self.assertEqual(result['state'], 'public')
        self.assertEqual(result['sample_names'], ['public-node'])

    def test_head_confirms_only_provider_response_not_public_access(self):
        result = self.classify(200, headers={'server': 'AmazonS3'}, kind='bucket')
        self.assertEqual(result['state'], 'service_response')
        self.assertFalse(result['public_access'])
        self.assertFalse(result['identity_verified'])


class CloudTransportTests(SimpleTestCase):
    @patch('leakscope.phase3.cloud._resolve')
    def test_private_or_blacklisted_resolution_is_rejected_before_connection(self, resolve):
        import socket
        from leakscope.phase3.cloud import request_candidate
        for address, cfg in [('127.0.0.1', {}), ('169.254.169.254', {}), ('8.8.8.8', {'blacklist': ['8.8.8.0/24']})]:
            resolve.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]
            with self.subTest(address=address), patch('socket.socket') as sock, self.assertRaises(ValueError):
                request_candidate({'url': 'https://acme.s3.amazonaws.com/', 'kind': 'bucket', 'provider': 'aws'}, cfg, verify=True)
            sock.assert_not_called()


class CloudAPITests(TestCase):
    def post(self, action, body):
        if body.get('providers') == ['aws'] and 'regions' not in body:
            body = dict(body, regions={'aws': ['global']})
        return self.client.post('/api/operations/cloud/' + action, json.dumps(body), content_type='application/json')

    def test_shell_and_catalog_and_preview_do_not_contact_targets(self):
        with patch('socket.getaddrinfo', side_effect=AssertionError('network forbidden')):
            self.assertEqual(self.client.get('/cloud/').status_code, 200)
            catalog = self.client.get('/api/operations/cloud/catalog')
            self.assertEqual(catalog.status_code, 200)
            self.assertGreaterEqual(len(catalog.json()['providers']), 11)
            preview = self.post('preview', {'names': ['acme'], 'providers': ['aws']})
        self.assertEqual(preview.status_code, 200, preview.content)
        from leakscope_app.models import Run
        self.assertEqual(Run.objects.count(), 0)

    def test_start_requires_explicit_active_consent_and_csrf(self):
        from leakscope_app.models import Run
        response = self.post('start', {'names': ['acme'], 'providers': ['aws']})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Run.objects.count(), 0)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/api/operations/cloud/start', '{}', content_type='application/json').status_code, 403)

    @patch('leakscope.phase3.cloud_jobs.request_candidate')
    def test_start_stores_evidence_and_normalized_observations_without_downloading_objects(self, request):
        request.return_value = {'status': 200, 'headers': {}, 'body': b'<ListBucketResult><Name>acme</Name><Contents><Key>a.pdf</Key></Contents></ListBucketResult>', 'truncated': False, 'resolved_ip': '192.0.2.1'}
        response = self.post('start', {'names': ['acme'], 'providers': ['aws'], 'active': True})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json()['run']['state'], 'succeeded')
        from leakscope_app.models import CloudResource, ServiceObservation
        row = CloudResource.objects.get()
        self.assertEqual(row.state, 'public')
        self.assertEqual(row.evidence['sample_names'], ['a.pdf'])
        self.assertTrue(ServiceObservation.objects.filter(provider='cloud:aws', endpoint=row.endpoint).exists())
        self.assertEqual(request.call_count, 1)
        resources = self.client.get('/api/operations/cloud/resources?provider=aws&state=public')
        self.assertEqual(resources.json()['count'], 1)

    @patch('leakscope.phase3.cloud_jobs.request_candidate')
    def test_errors_and_generic_http_do_not_become_cloud_inventory(self, request):
        from leakscope_app.models import CloudResource
        request.return_value = {'status': 200, 'headers': {}, 'body': b'<html>hello</html>', 'truncated': False}
        response = self.post('start', {'names': ['acme'], 'providers': ['aws'], 'active': True})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(CloudResource.objects.count(), 0)
        run_id = response.json()['run']['id']
        outcomes = self.client.get('/api/operations/cloud/runs/' + run_id).json()
        self.assertEqual(outcomes['result']['outcomes'][0]['state'], 'unverified')

    @patch('leakscope.phase3.cloud_jobs.run_cloud.apply_async')
    def test_dispatch_freezes_validated_spec_and_is_routed_to_discovery(self, submit):
        response = self.post('start', {'names': ['acme'], 'providers': ['aws'], 'active': True})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(submit.call_args.kwargs['kwargs']['spec']['names'], ['acme'])
        from django.conf import settings
        self.assertEqual(settings.CELERY_TASK_ROUTES['leakscope.phase3.run_cloud']['queue'], 'discovery')

    @patch('leakscope.phase3.cloud_jobs.request_candidate')
    def test_unchanged_resources_do_not_change_when_request_ids_change(self, request):
        from leakscope_app.models import ObservationChange, ServiceObservation
        for token in ('request-one', 'request-two'):
            request.return_value = {'status': 200, 'headers': {'x-amz-request-id': token},
                'body': f'<ListBucketResult><Name>acme</Name><RequestId>{token}</RequestId></ListBucketResult>'.encode(), 'truncated': False}
            self.assertEqual(self.post('start', {'names': ['acme'], 'providers': ['aws'], 'active': True}).status_code, 202)
        self.assertEqual(ObservationChange.objects.count(), 0)
        self.assertEqual(ServiceObservation.objects.count(), 1)

    @patch('leakscope.phase3.cloud_jobs.request_candidate')
    def test_existing_resource_absence_updates_observations_and_keeps_response_evidence(self, request):
        from leakscope_app.models import CloudResource, EvidenceArtifact
        body = {'names': ['acme'], 'providers': ['aws'], 'active': True}
        request.return_value = {'status': 200, 'headers': {}, 'body': b'<ListBucketResult><Name>acme</Name></ListBucketResult>'}
        self.post('start', body)
        request.return_value = {'status': 404, 'headers': {}, 'body': b'<Error><Code>NoSuchBucket</Code></Error>'}
        self.post('start', body)
        row = CloudResource.objects.get()
        self.assertEqual(row.state, 'not_found')
        self.assertEqual(row.endpoint.observations.first().data['metadata']['cloud_evidence']['state'], 'not_found')
        self.assertEqual(EvidenceArtifact.objects.filter(endpoint=row.endpoint, kind='cloud-response').count(), 2)

    @patch('leakscope.phase3.jobs.fetch_endpoint.apply_async')
    @patch('leakscope.phase3.cloud_jobs.request_candidate')
    @patch('leakscope.phase3.cloud_jobs.time.sleep')
    def test_dotted_bucket_resources_capture_their_own_paths(self, sleep, request, submit):
        from leakscope_app.models import CloudResource, ProviderEndpointState, ObservationChange, Run
        def reply(candidate, *args, **kwargs):
            return {'status': 200, 'headers': {}, 'body': f'<ListBucketResult><Name>{candidate["name"]}</Name></ListBucketResult>'.encode()}
        request.side_effect = reply
        self.post('start', {'names': ['a.example', 'b.example'], 'providers': ['aws'], 'active': True})
        first = CloudResource.objects.get(name='a.example')
        response = self.post('resources/' + str(first.pk) + '/fetch', {'actions': ['http']})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(submit.call_args.kwargs['kwargs']['snapshot']['path'], '/a.example/')
        self.assertEqual(ProviderEndpointState.objects.count(), 2)
        self.assertEqual(ObservationChange.objects.count(), 0)
        self.assertEqual(Run.objects.get(pk=response.json()['run']['id']).kind, 'capture')

    @patch('leakscope.phase3.cloud_jobs.publish_state')
    @patch('leakscope.phase3.cloud_jobs.request_candidate')
    def test_cancellation_preserves_completed_outcomes(self, request, progress):
        from leakscope_app.models import Run
        request.return_value = {'status': 200, 'headers': {}, 'body': b'<html>unknown</html>'}
        def cancel(*args, **kwargs):
            row = Run.objects.get(kind='cloud')
            self.post('runs/' + str(row.pk) + '/cancel', {})
        progress.side_effect = cancel
        response = self.post('start', {'names': ['acme', 'other'], 'providers': ['aws'], 'active': True})
        self.assertEqual(response.json()['run']['state'], 'cancelled')
        result = response.json()['run']['result']
        self.assertEqual(len(result['outcomes']), 1)
        self.assertTrue(result['partial'])
        request.assert_called_once()
