"""Saved-data investigation contracts; no provider or target traffic."""
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from leakscope_app.models import ServiceEndpoint, ServiceObservation
from leakscope.phase3 import relationships as store


class InvestigationTests(TestCase):
    def setUp(self):
        self.endpoint = ServiceEndpoint.objects.create(key='fixture',address='192.0.2.1',port=443)
        self.earlier = timezone.now()-timedelta(days=20)
        self.before = self.observation('before', {'title':'Old','metadata':{'product':'alpha','removed':True}})
        self.after = self.observation('after', {'title':'New','metadata':{'product':'beta','added':True}})

    def observation(self, fingerprint, data, **fields):
        defaults = dict(endpoint=self.endpoint,provider='shodan',fingerprint=fingerprint,data=data,observed_at=self.earlier)
        defaults.update(fields)
        return ServiceObservation.objects.create(**defaults)

    def compare(self, before=None, after=None):
        return self.client.get(f'/api/investigation/endpoints/{self.endpoint.pk}/compare',{'before':str((before or self.before).pk),'after':str((after or self.after).pk)})

    def test_distinguishes_provider_time_collection_and_duplicate_interval(self):
        self.before.last_observed_at=self.earlier+timedelta(days=2)
        self.before.save()
        response=self.client.get(f'/api/operations/endpoints/{self.endpoint.pk}')
        self.assertEqual(response.status_code,200)
        row=next(r for r in response.json()['observations'] if r['id']==str(self.before.pk))
        self.assertEqual(row['provenance']['label'],'Provider-reported')
        self.assertEqual(row['provenance']['observed_at'],self.earlier.isoformat())
        self.assertEqual(row['last_observed_at'],self.before.last_observed_at.isoformat())
        self.assertEqual(row['provenance']['collected_at'],self.before.last_collected_at.isoformat())
        self.assertIn('revalid',row['provenance']['basis'])
        self.assertIn('duplicate',self.compare().json()['basis'].lower())

    def test_unknown_observed_time_is_unknown_and_fetch_is_direct(self):
        self.before.observed_at=None
        self.before.save()
        capture=self.observation('fetch',{},provider='fetch:http',observed_at=None)
        rows=self.client.get(f'/api/operations/endpoints/{self.endpoint.pk}').json()['observations']
        self.assertIsNone(next(x for x in rows if x['id']==str(self.before.pk))['provenance']['observed_at'])
        self.assertEqual(next(x for x in rows if x['id']==str(capture.pk))['provenance']['label'],'Direct capture')

    def test_compare_stable_meaningful_paths_without_provenance_noise(self):
        data=self.compare().json()
        self.assertEqual(data['count'],4)
        self.assertEqual([(c['path'],c['kind']) for c in data['changes']],[('/metadata/added','added'),('/metadata/product','changed'),('/metadata/removed','removed'),('/title','changed')])
        self.assertEqual(data['before']['id'],str(self.before.pk))
        self.assertFalse(data['truncated'])

    def test_same_provider_endpoint_visible_distinct_records_required(self):
        other=self.observation('other',{},provider='zoomeye')
        self.assertEqual(self.compare(after=other).status_code,400)
        self.assertEqual(self.compare(after=self.before).status_code,400)
        other.provider='shodan';other.hidden=True;other.save()
        self.assertEqual(self.compare(after=other).status_code,404)
        other.hidden=False
        other.endpoint=ServiceEndpoint.objects.create(key='other',address='192.0.2.2',port=80)
        other.save()
        self.assertEqual(self.compare(after=other).status_code,404)
        self.endpoint.observations.update(hidden=True)
        self.assertEqual(self.compare().status_code,404)

    def test_secrets_redacted_and_volatile_provider_fields_ignored(self):
        self.before.data={'metadata':{'timestamp':'old','password':'before-secret','link':'https://user:pass@example.test?api_key=hidden','note':'configured-secret'}}
        self.after.data={'metadata':{'timestamp':'new','password':'after-secret','link':'https://other:password@example.test?api_key=another','note':'safe'}}
        self.before.save();self.after.save()
        with patch('leakscope.services.run_events.config.get_config',return_value={'config':{'netlas':{'api_key':'configured-secret'}}}):
            response=self.compare()
        self.assertEqual(response.status_code,200,response.content)
        text=response.content.decode()
        for secret in ['before-secret','after-secret','user:pass','other:password','configured-secret','api_key=hidden','api_key=another']:
            self.assertNotIn(secret,text)
        self.assertNotIn('/metadata/timestamp',text)

    def test_comparison_output_is_bounded(self):
        self.after.data={f'field{i:04}':'x'*4000 for i in range(300)}
        self.after.save()
        data=self.compare().json()
        self.assertLessEqual(len(data['changes']),100)
        self.assertTrue(data['truncated'])
        self.assertLess(len(self.compare().content),180000)

    def test_org_summary_scoped_visible_and_bounded(self):
        org=store.node('organization','Fixture Inc')
        domain=store.node('domain','example.com')
        proof=store.evidence(org,'user_scope',{'kind':'domain','value':domain.value})
        store.relation(org,domain,'user_scope',proof)
        for number in range(15):store.evidence(domain,'dns',{'type':'TXT','value':str(number)})
        unrelated=store.node('domain','unrelated.test')
        store.evidence(unrelated,'foreign-private',{'secret':'do not show'})
        hidden=store.evidence(domain,'hidden-private',{'observation_pk':self.before.pk.hex})
        self.before.hidden=True;self.before.save()
        result=self.client.get(f'/api/assets/organizations/{org.pk}').json()
        self.assertEqual(len(result['domains']),1)
        overview=result['overview']
        self.assertEqual(overview['counts']['scope_domains'],1)
        self.assertEqual(overview['counts']['assets'],1)
        self.assertEqual(overview['counts']['evidence'],16)
        self.assertEqual(overview['sources'],[{'source':'dns','count':15},{'source':'user_scope','count':1}])
        self.assertEqual(len(overview['recent_evidence']),12)
        self.assertTrue(overview['recent_truncated'])
        self.assertNotIn('hidden-private',str(overview))
        self.assertNotIn('foreign-private',str(overview))
        self.assertIn('ownership',overview['basis'])

    def test_org_endpoint_technologies_are_visible_saved_and_explicit(self):
        org=store.node('organization','Fixture Inc')
        service=store._service(self.endpoint)
        proof=store.evidence(org,'user_scope',{'reference':{'type':'endpoint','id':str(self.endpoint.pk)}})
        store.relation(org,service,'user_scope',proof)
        self.after.data={'service_type':'https'};self.after.save()
        self.before.data={'service_type':'hidden-product'};self.before.hidden=True;self.before.save()
        overview=self.client.get(f'/api/assets/organizations/{org.pk}').json()['overview']
        self.assertEqual(overview['counts']['endpoints'],1)
        self.assertEqual(overview['technologies'],[{'technology':'https','count':1}])
        self.assertNotIn('hidden-product',str(overview))

    def test_nested_added_payload_and_long_paths_remain_bounded(self):
        self.before.data={}
        self.after.data={'x'*5000:{str(i):['x'*4000]*30 for i in range(25)}}
        self.before.save();self.after.save()
        response=self.compare()
        self.assertEqual(response.status_code,200,response.content[:100])
        self.assertLess(len(response.content),15000)
        self.assertTrue(response.json()['truncated'])

    def test_long_values_with_equal_preview_still_report_a_change(self):
        self.before.data={'banner':'x'*3000+'old'}
        self.after.data={'banner':'x'*3000+'new'}
        self.before.save();self.after.save()
        result=self.compare().json()
        self.assertEqual(result['count'],1)
        self.assertNotEqual(result['changes'][0]['before'],result['changes'][0]['after'])
        self.assertTrue(result['truncated'])

    def test_native_scan_and_cloud_verification_are_direct_captures(self):
        for provider in ['scan:tcp','cloud:aws']:
            row=self.observation(provider,{},provider=provider)
            result=self.client.get(f'/api/operations/endpoints/{self.endpoint.pk}').json()
            actual=next(x for x in result['observations'] if x['id']==str(row.pk))
            self.assertEqual(actual['provenance']['label'],'Direct capture')

    def test_default_endpoint_and_artifact_evidence_redaction_preserves_reveal(self):
        from leakscope_app.models import EvidenceArtifact
        secret='https://user:pass@example.test/?token=opaque Bearer credential configured-secret'
        self.after.data={'metadata':{'note':secret}}
        self.after.save()
        EvidenceArtifact.objects.create(endpoint=self.endpoint,kind='response',relative_path='fixture.txt',sha256='a'*64,
            metadata={'note':secret})
        with patch('leakscope.services.run_events.config.get_config',return_value={'config':{'SHODAN_API_KEY':'configured-secret'}}):
            default=self.client.get(f'/api/operations/endpoints/{self.endpoint.pk}')
            revealed=self.client.get(f'/api/operations/endpoints/{self.endpoint.pk}?reveal=true')
        for value in ['user:pass','token=opaque','Bearer credential','configured-secret']:
            self.assertNotIn(value,default.content.decode())
            self.assertIn(value,revealed.content.decode())

    def test_default_legacy_detail_redaction_preserves_reveal(self):
        from leakscope_app.models import Search, Jenkins
        search=Search.objects.create(type='jenkins',provider='shodan',keyword='fixture')
        secret='https://user:pass@example.test/?token=opaque Bearer credential configured-secret'
        saved=Jenkins.objects.create(search=search,provider='shodan',ip='192.0.2.9',port='443',url='https://192.0.2.9',jobs=secret)
        with patch('leakscope.services.run_events.config.get_config',return_value={'config':{'SHODAN_API_KEY':'configured-secret'}}):
            default=self.client.get(f'/api/findings/jenkins/{saved.pk}')
            revealed=self.client.get(f'/api/findings/jenkins/{saved.pk}?reveal=true')
        self.assertEqual(default.status_code,200,default.content)
        for value in ['user:pass','token=opaque','Bearer credential','configured-secret']:
            self.assertNotIn(value,default.content.decode())
            self.assertIn(value,revealed.content.decode())

    def test_domain_service_members_count_matches_existing_detail_members(self):
        org=store.node('organization','Fixture Inc')
        domain=store.node('domain','vendor.example.com')
        proof=store.evidence(org,'fixture',{'uses':'vendor'})
        store.relation(org,domain,'uses_service',proof)
        store.relation(org,domain,'references_domain',proof)
        result=self.client.get(f'/api/assets/organizations/{org.pk}').json()
        self.assertEqual(len(result['members']),1)
        self.assertEqual(result['overview']['counts']['linked_results'],1)
        self.assertEqual(result['overview']['counts']['scope_domains'],0)

    def test_large_org_endpoint_summary_uses_subquery_not_literal_id_list(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from leakscope_app.models import AssetNode,AssetRelationship
        import uuid
        org=store.node('organization','Large fixture')
        proof=store.evidence(org,'user_scope',{'kind':'service'})
        endpoints=[ServiceEndpoint(id=uuid.uuid4(),key=f'bulk{i}',address=f'host{i}.example.test',port=443) for i in range(1005)]
        ServiceEndpoint.objects.bulk_create(endpoints)
        ServiceObservation.objects.bulk_create([ServiceObservation(endpoint=e,provider='shodan',fingerprint='bulk',data={}) for e in endpoints])
        members=[AssetNode(key=f'bulk{i}',kind='service',value=f'tcp://host{i}:443',attributes={'endpoint_id':str(e.pk)}) for i,e in enumerate(endpoints)]
        members.append(AssetNode(key='malformed',kind='service',value='malformed',attributes={'endpoint_id':'not-a-uuid'}))
        AssetNode.objects.bulk_create(members)
        AssetRelationship.objects.bulk_create([AssetRelationship(source=org,target=m,relation='user_scope',evidence=proof) for m in members])
        with CaptureQueriesContext(connection) as queries:
            result=self.client.get(f'/api/assets/organizations/{org.pk}')
        self.assertEqual(result.status_code,200,result.content[:300])
        self.assertEqual(result.json()['overview']['counts']['endpoints'],1005)
        self.assertLess(len(queries),45)
        # New summary queries must stay constant in parameter/SQL size as scope grows.
        self.assertFalse(any(str(endpoints[-1].pk).replace('-','') in q['sql'] or str(endpoints[-1].pk) in q['sql'] for q in queries))

    def test_user_scope_and_organization_actions_are_not_provider_reports(self):
        from leakscope_app.investigation import provenance
        for source in ['user:qa-scope','user:scope','organization_lifecycle','user_scope']:
            self.assertEqual(provenance(source,None,None)['label'],'User supplied')

    def test_dns_service_relationship_signals_are_inferences(self):
        from leakscope_app.investigation import provenance
        self.assertEqual(provenance('dns_service_signal',None,None)['label'],'Inferred')
