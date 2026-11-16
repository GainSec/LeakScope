import json
from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3 import asset_jobs as jobs


class ServiceResearchJobsTests(TestCase):
    @patch('leakscope.phase3.asset_jobs.research_services')
    def test_web_service_candidates_are_cached_and_keep_cited_evidence(self,research):
        from leakscope_app.models import AssetRelationship
        research.return_value={'target':'example.com','services':[{'provider':'Example Vendor','category':'analytics',
            'relation':'web_service_candidate','confidence':60,'evidence':[{'source_url':'https://example.com/privacy',
            'excerpt':'We use Example Vendor for analytics.','signal':'relationship_statement','method':'firecrawl'}]}],
            'sources':[{'url':'https://example.com/privacy'}],'requests':1,'partial':False,'errors':[]}
        result=jobs.run_asset_enrichment.apply(kwargs={'kind':'services','target':'example.com'}).get()
        self.assertEqual(result['services'][0]['provider'],'Example Vendor')
        edge=AssetRelationship.objects.get(relation='web_service_candidate')
        self.assertEqual(edge.evidence.source,'firecrawl')
        self.assertEqual(edge.evidence.source_url,'https://example.com/privacy')
        self.assertNotIn(edge.relation,{'uses_service','owns'})
        cached=jobs.queue_enrichment('services','example.com')
        self.assertTrue(cached['cached'])
        research.assert_called_once()

    @patch('leakscope.phase3.asset_jobs.queue_enrichment')
    def test_service_research_buttons_queue_explicit_organization_or_target(self,queue):
        from leakscope.phase3 import relationships as store
        queue.return_value={'run':{'id':'fixture'}}
        org=store.node('organization','Example Corp')
        response=self.client.post(f'/api/assets/organizations/{org.pk}/services','{}',content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        queue.assert_called_once_with('services','Example Corp',organization_id=str(org.pk))
        queue.reset_mock()
        response=self.client.post('/api/assets/services',json.dumps({'target':'8.8.8.8'}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        queue.assert_called_once_with('services','8.8.8.8')

    @patch('leakscope.phase3.asset_jobs.research_services')
    def test_organization_research_attaches_to_existing_organization(self,research):
        from leakscope.phase3 import relationships as store
        org=store.node('organization','Example Corp')
        research.return_value={'services':[],'sources':[],'requests':1,'errors':[],'partial':False}
        result=jobs.run_asset_enrichment.apply(kwargs={'kind':'services','target':org.value,'organization_id':str(org.pk)}).get()
        self.assertEqual(result['root_id'],str(org.pk))
        self.assertEqual(research.call_args.kwargs['target_kind'],'organization')
