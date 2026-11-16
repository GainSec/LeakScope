"""Organization-only lifecycle boundaries; no provider calls or target probes."""
from unittest.mock import patch
from uuid import uuid4
from django.test import TestCase, Client
from leakscope.phase3 import relationships as store
from leakscope_app.models import AssetNode, AssetEvidence, AssetRelationship, WatchRule, ServiceEndpoint


class OrganizationLifecycleTests(TestCase):
    def setUp(self):
        self.source=store.node('organization','Source Corp',{'aliases':['Source alias']})
        self.target=store.node('organization','Destination Corp',{'aliases':['Destination alias']})
        self.base='/api/assets/organizations/'
        self.domain=store.node('domain','example.com')
        self.proof=store.evidence(self.source,'user_scope',{'kind':'domain','value':'example.com','origin':'manual'},source_url='https://example.com/record')
        store.relation(self.source,self.domain,'user_scope',self.proof)
        self.watch=WatchRule.objects.create(name='Keep watch',domains=['example.com'],auto_fetch=False)

    def post(self,action,data=None,org=None):
        return self.client.post(self.base+str((org or self.source).pk)+'/'+action,data or {},content_type='application/json')

    def test_archive_restore_filters_and_read_only_membership(self):
        archived=self.post('archive')
        self.assertEqual(archived.status_code,200,archived.content)
        self.assertEqual(archived.json()['organization']['status'],'archived')
        active=self.client.get(self.base).json()['results']
        self.assertNotIn(str(self.source.pk),[r['id'] for r in active])
        self.assertEqual(len(self.client.get(self.base+'?status=archived').json()['results']),1)
        self.assertEqual(len(self.client.get(self.base+'?status=all').json()['results']),2)
        detail=self.client.get(self.base+str(self.source.pk)).json()
        self.assertEqual(detail['domains'][0]['name'],'example.com')
        self.assertEqual(self.post('domains',{'domains':['new.example.com']}).status_code,400)
        self.assertEqual(self.post('restore').json()['organization']['status'],'active')
        self.assertEqual(self.post('domains',{'domains':['new.example.com']}).status_code,200)
        self.watch.refresh_from_db();self.assertTrue(self.watch.enabled);self.assertFalse(self.watch.auto_fetch)

    def test_delete_removes_workspace_membership_without_deleting_assets_evidence_or_watch(self):
        self.assertEqual(self.post('delete',{'confirmation':'wrong'}).status_code,400)
        deleted=self.post('delete',{'confirmation':self.source.value})
        self.assertEqual(deleted.status_code,200,deleted.content)
        self.assertEqual(deleted.json()['organization']['status'],'deleted')
        self.assertTrue(AssetNode.objects.filter(pk=self.domain.pk).exists())
        self.assertTrue(AssetEvidence.objects.filter(pk=self.proof.pk).exists())
        self.assertTrue(WatchRule.objects.filter(pk=self.watch.pk).exists())
        self.assertFalse(AssetRelationship.objects.filter(source=self.source,relation='user_scope').exists())
        self.assertEqual(self.client.get(self.base+str(self.source.pk)).status_code,404)
        self.assertEqual(self.post('restore').status_code,400)
        self.assertEqual(self.post('domains',{'domains':['new.example.com']}).status_code,400)
        # A later provider mention may retain the identity, but never resurrect the workspace record.
        again=store.node('organization',self.source.value)
        self.assertEqual(again.pk,self.source.pk)
        self.assertEqual(again.organization_status,'deleted')
        self.assertNotIn(str(self.source.pk),[n['id'] for n in store.graph()['nodes']])

    def test_merge_preserves_evidence_ids_and_provenance_rewires_incoming_and_deduplicates_display(self):
        proof2=store.evidence(self.target,'user_scope',{'kind':'domain','value':'example.com','origin':'manual'})
        store.relation(self.target,self.domain,'user_scope',proof2)
        network=store.node('network','192.0.2.0/24')
        reg=store.evidence(network,'rdap',{'organization':self.source.value})
        store.relation(network,self.source,'registered_to',reg,80)
        ids=set(AssetEvidence.objects.values_list('pk',flat=True))
        result=self.post('merge',{'target_id':str(self.target.pk),'confirmation':self.source.value})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['organization']['id'],str(self.target.pk))
        self.target.refresh_from_db();self.source.refresh_from_db();self.proof.refresh_from_db()
        self.assertEqual(self.source.organization_status,'merged')
        self.assertEqual(self.source.merged_into_id,self.target.pk)
        self.assertEqual(set(self.target.attributes['aliases']),{'Source Corp','Source alias','Destination alias'})
        self.assertTrue(ids<=set(AssetEvidence.objects.values_list('pk',flat=True)))
        self.assertEqual(self.proof.subject_id,self.target.pk)
        self.assertEqual(self.proof.source_url,'https://example.com/record')
        self.assertEqual(self.proof.data['organization_origins'][0]['id'],str(self.source.pk))
        self.assertEqual(self.proof.key,store._hash([str(self.target.pk),self.proof.source,store._stable(self.proof.data)]))
        self.assertTrue(AssetRelationship.objects.filter(source=network,target=self.target,relation='registered_to',evidence=reg).exists())
        self.assertFalse(AssetRelationship.objects.filter(source=self.source).exists())
        detail=self.client.get(self.base+str(self.target.pk)).json()
        self.assertEqual(len(detail['domains']),1)
        self.assertEqual(store.node('organization','Source Corp').pk,self.target.pk)
        self.assertEqual(self.client.get(self.base+str(self.source.pk)).json()['organization']['id'],str(self.target.pk))
        self.assertEqual(len(self.client.get(self.base).json()['results']),1)
        self.assertTrue(AssetEvidence.objects.filter(subject=self.target,source='organization_lifecycle').exists())

    def test_merge_rejects_invalid_targets_confirmation_and_preserves_atomic_state(self):
        for target in [self.source.pk,uuid4(),self.domain.pk,'bad']:
            with self.subTest(target=target):self.assertIn(self.post('merge',{'target_id':str(target),'confirmation':self.source.value}).status_code,[400,404])
        self.assertEqual(self.post('merge',{'target_id':str(self.target.pk),'confirmation':'wrong'}).status_code,400)
        self.post('archive',org=self.target)
        self.assertEqual(self.post('merge',{'target_id':str(self.target.pk),'confirmation':self.source.value}).status_code,400)
        self.source.refresh_from_db();self.assertEqual(self.source.organization_status,'active')
        self.assertTrue(AssetRelationship.objects.filter(source=self.source).exists())

    def test_merge_chains_redirect_future_ingestion_without_changing_destination_label_or_aliases(self):
        last=store.node('organization','Final Corp',{'aliases':['Final alias']})
        self.post('merge',{'target_id':str(self.target.pk),'confirmation':self.source.value})
        self.post('merge',{'target_id':str(last.pk),'confirmation':self.target.value},org=self.target)
        resolved=store.node('organization','Source Corp',{'aliases':[]},label='Old label')
        self.assertEqual(resolved.pk,last.pk)
        self.assertEqual(resolved.label,'Final Corp')
        self.assertIn('Source alias',resolved.attributes['aliases'])
        self.assertEqual(self.client.get(self.base+str(self.source.pk)).json()['organization']['id'],str(last.pk))

    def test_graph_hides_archived_in_overview_but_keeps_explicit_archived_root_readable(self):
        self.post('archive')
        self.assertNotIn(str(self.source.pk),[n['id'] for n in store.graph()['nodes']])
        focused=store.graph(root_id=str(self.source.pk))
        self.assertIn(str(self.source.pk),[n['id'] for n in focused['nodes']])

    def test_preview_is_read_only_and_no_provider_requests_start(self):
        before=AssetEvidence.objects.count()
        response=self.client.get(self.base+str(self.source.pk)+'/manage?target_id='+str(self.target.pk))
        self.assertEqual(response.status_code,200,response.content)
        p=response.json()['preview'];self.assertEqual(p['organization']['id'],str(self.source.pk));self.assertEqual(p['target']['id'],str(self.target.pk))
        self.assertEqual(p['counts']['domains'],1);self.assertEqual(p['counts']['evidence'],1)
        self.assertEqual(AssetEvidence.objects.count(),before)

    def test_mutation_requires_csrf_and_list_filter_is_validated(self):
        client=Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.base+str(self.source.pk)+'/archive',{},content_type='application/json').status_code,403)
        self.assertEqual(self.client.get(self.base+'?status=deleted').status_code,400)

    @patch('leakscope.phase3.asset_jobs.free_registry.discover_organization')
    def test_inactive_organizations_cannot_start_discovery_or_materialize_running_results(self,collect):
        from leakscope.phase3.asset_jobs import queue_enrichment
        self.post('archive')
        with self.assertRaises(ValueError):queue_enrichment('organization',self.source.value,organization_id=str(self.source.pk))
        self.assertEqual(self.post('discover').status_code,400)
        self.assertEqual(self.post('services').status_code,400)
        collect.assert_not_called()
        self.post('restore')
        def during(*args,**kwargs):
            self.post('archive')
            return {'organization':self.source.value,'domains':[{'name':'new.example.com'}],'partial':False,'errors':[]}
        collect.side_effect=during
        result=queue_enrichment('organization',self.source.value,organization_id=str(self.source.pk))['run']['result']
        self.assertTrue(result['skipped'])
        self.assertFalse(AssetNode.objects.filter(value='new.example.com').exists())

    @patch('leakscope.phase3.asset_jobs.free_registry.discover_organization')
    def test_large_merged_alias_set_is_preserved_with_bounded_discovery_selection(self,collect):
        from leakscope.phase3.asset_jobs import queue_enrichment
        names=['Alias '+str(i) for i in range(25)]
        self.target.attributes={'aliases':names};self.target.save(update_fields=['attributes'])
        self.post('merge',{'target_id':str(self.target.pk),'confirmation':self.source.value})
        self.target.refresh_from_db()
        self.assertTrue(set(names)<=set(self.target.attributes['aliases']))
        collect.return_value={'organization':self.target.value,'domains':[],'networks':[],'errors':[],'partial':False}
        result=queue_enrichment('organization',self.target.value,organization_id=str(self.target.pk))['run']['result']
        self.assertEqual(len(collect.call_args.kwargs['aliases']),20)
        self.assertTrue(result['partial']);self.assertIn('alias_coverage',result)

    def test_failed_merge_rolls_back_already_moved_proofs(self):
        original_attrs=dict(self.target.attributes)
        with patch('leakscope.phase3.organization_lifecycle._audit',side_effect=ValueError('Fixture failure')):
            self.assertEqual(self.post('merge',{'target_id':str(self.target.pk),'confirmation':self.source.value}).status_code,400)
        self.proof.refresh_from_db();self.source.refresh_from_db();self.target.refresh_from_db()
        self.assertEqual(self.proof.subject_id,self.source.pk)
        self.assertEqual(self.source.organization_status,'active')
        self.assertEqual(self.target.attributes,original_attrs)

    def test_overlapping_shared_evidence_links_merge_without_unique_constraint_failure(self):
        shared=store.evidence(self.domain,'fixture',{'fact':'shared registration'})
        one=store.relation(self.domain,self.source,'registrant_organization',shared,40)
        store.relation(self.domain,self.target,'registrant_organization',shared,80)
        response=self.post('merge',{'target_id':str(self.target.pk),'confirmation':self.source.value})
        self.assertEqual(response.status_code,200,response.content)
        links=AssetRelationship.objects.filter(source=self.domain,target=self.target,relation='registrant_organization',evidence=shared)
        self.assertEqual(links.count(),1);self.assertEqual(links.get().confidence,80)

    @patch('leakscope.phase3.asset_jobs.free_registry.discover_organization')
    def test_long_auto_discovered_names_do_not_break_destination_discovery(self,collect):
        from leakscope.phase3.asset_jobs import queue_enrichment
        long=store.node('organization','Long '+('x'*295))
        response=self.post('merge',{'target_id':str(self.target.pk),'confirmation':long.value},org=long)
        self.assertEqual(response.status_code,200,response.content)
        collect.return_value={'organization':self.target.value,'domains':[],'networks':[],'errors':[],'partial':False}
        result=queue_enrichment('organization',self.target.value,organization_id=str(self.target.pk))['run']['result']
        self.target.refresh_from_db();self.assertIn(long.value,self.target.attributes['aliases'])
        self.assertNotIn(long.value,collect.call_args.kwargs['aliases'])
        self.assertEqual(result['alias_coverage']['unsupported_length'],1)
        self.assertTrue(result['partial'])

    def test_self_relationship_retirement_retains_its_original_direction_and_times(self):
        proof=store.evidence(self.source,'fixture',{'service':'same merged entity'})
        edge=store.relation(self.source,self.target,'uses_service',proof,65)
        self.post('merge',{'target_id':str(self.target.pk),'confirmation':self.source.value})
        event=AssetEvidence.objects.filter(subject=self.target,source='organization_lifecycle').get(data__action='merge')
        history=event.data['retired_self_relationships'][0]
        self.assertEqual(history['source_id'],str(self.source.pk));self.assertEqual(history['target_id'],str(self.target.pk))
        self.assertEqual(history['confidence'],65);self.assertEqual(history['first_seen'],edge.first_seen.isoformat())


from concurrent.futures import ThreadPoolExecutor
import threading
from unittest import skipUnless
from django.db import connection, connections, transaction
from django.test import TransactionTestCase


@skipUnless(connection.vendor=='postgresql','PostgreSQL row locking concurrency checks')
class OrganizationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.a=store.node('organization','Concurrent A')
        self.b=store.node('organization','Concurrent B')

    def workers(self,*operations):
        barrier=threading.Barrier(len(operations))
        def execute(fn):
            connections.close_all()
            try:
                with connection.cursor() as cursor:cursor.execute('SET statement_timeout=10000')
                barrier.wait(timeout=10)
                return fn()
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            futures=[pool.submit(execute,fn) for fn in operations]
            return [future.result(timeout=15) for future in futures]

    def test_opposing_merges_serialize_without_cycles_or_partial_moves(self):
        from leakscope.phase3.organization_lifecycle import merge,canonical
        def attempt(source,target):
            try:merge(source.pk,target.pk,source.value);return 'merged'
            except ValueError:return 'rejected'
        result=self.workers(lambda:attempt(self.a,self.b),lambda:attempt(self.b,self.a))
        self.assertCountEqual(result,['merged','rejected'])
        self.a.refresh_from_db();self.b.refresh_from_db()
        self.assertEqual(canonical(self.a).pk,canonical(self.b).pk)
        self.assertEqual(AssetNode.objects.filter(kind='organization',organization_status='active').count(),1)

    def test_concurrent_ingestion_stays_on_the_canonical_merged_organization(self):
        from leakscope.phase3.organization_lifecycle import merge
        domain=store.node('domain','concurrent.example.com')
        def ingest():
            with transaction.atomic():
                root=store.node('organization',self.a.value)
                proof=store.evidence(root,'fixture',{'fact':'concurrent insertion'})
                store.relation(root,domain,'user_scope',proof)
                return proof.pk
        proof_id,_=self.workers(ingest,lambda:merge(self.a.pk,self.b.pk,self.a.value))
        proof=AssetEvidence.objects.get(pk=proof_id)
        self.assertEqual(proof.subject_id,self.b.pk)
        self.assertTrue(AssetRelationship.objects.filter(source=self.b,target=domain,evidence=proof).exists())
        self.assertFalse(AssetRelationship.objects.filter(source=self.a).exists())
