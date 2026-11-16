from unittest.mock import patch
from django.test import TestCase
from leakscope_app.models import CustomQuery


class LegacyDemoRoutingTests(TestCase):
    @patch('leakscope.phase3.jobs.run_lookup.apply_async')
    @patch('leakscope.tasks.check_custom_query.delay')
    def test_legacy_custom_query_json_demo_cannot_fall_through_to_normal_sdk(self,legacy,submit):
        query=CustomQuery.objects.create(name='Legacy',query_template='product:fixture',provider='shodan',default_type='elastic',active_probe_default=True)
        response=self.client.post('/custom-queries/run',{'id':query.pk,'demo':True,'page':8,'page_count':20,'active_probe':True},content_type='application/json')
        self.assertEqual(response.status_code,202,response.content)
        snapshot=submit.call_args.kwargs['kwargs']['snapshot']
        self.assertEqual(snapshot['collection_mode'],'demo');self.assertEqual(snapshot['page_count'],1);self.assertFalse(snapshot['auto_fetch'])
        legacy.assert_not_called()

    @patch('leakscope.phase3.jobs.run_lookup.apply_async')
    @patch('leakscope.tasks.check_main.delay')
    def test_legacy_search_results_demo_uses_current_minimum_collector(self,legacy,submit):
        for route in ['/search/elastic/results','/keyword/search']:
            response=self.client.post(route,{'type':'elastic','demo':True,'page':8,'page_count':20,'active_probe':True,'provider':'zoomeye'},content_type='application/json')
            self.assertEqual(response.status_code,202,response.content)
            self.assertEqual(submit.call_args.kwargs['kwargs']['snapshot']['page_count'],1)
            legacy.assert_not_called()

    @patch('leakscope.tasks.check_custom_query.delay')
    def test_legacy_explicit_bad_demo_flag_fails_without_spending(self,legacy):
        query=CustomQuery.objects.create(name='Legacy',query_template='test',provider='shodan')
        response=self.client.post('/custom-queries/run',{'id':query.pk,'demo':'false'})
        self.assertEqual(response.status_code,400,response.content);legacy.assert_not_called()

    def test_legacy_misspelled_collection_mode_is_rejected_before_any_lookup(self):
        with patch('leakscope.tasks.check_custom_query.delay') as task:
            response=self.client.post('/custom-queries/run',{'collection_mode':'Demo'},content_type='application/json')
        self.assertEqual(response.status_code,400);task.assert_not_called()
