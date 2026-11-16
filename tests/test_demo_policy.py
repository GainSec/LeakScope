from django.test import SimpleTestCase


class DemoPolicyTests(SimpleTestCase):
    def test_flag_is_strict_and_normal_defaults_are_false(self):
        from leakscope.phase3.usage_policy import validate_demo
        self.assertFalse(validate_demo());self.assertTrue(validate_demo(True))
        for value in ['true','false',1,0,None,[],{}]:
            with self.subTest(value=value),self.assertRaises(ValueError):validate_demo(value)

    def test_snapshot_limits_do_not_mutate_saved_configuration(self):
        from leakscope.phase3.usage_policy import demo_snapshot
        original={'collection_mode':'full','page_count':20,'page_size':100,'max_results':10000,'actions':['screenshot'],'auto_fetch':True}
        demo=demo_snapshot(original,True)
        self.assertEqual(demo['collection_mode'],'demo')
        self.assertEqual((demo['page_count'],demo['page_size'],demo['max_results']),(1,1,1))
        self.assertFalse(demo['auto_fetch']);self.assertEqual(demo['actions'],[])
        self.assertEqual(original['collection_mode'],'full');self.assertEqual(original['page_count'],20)
        self.assertEqual(demo_snapshot(original,False),original)

    def test_cached_demo_service_and_its_supporting_source_stay_together(self):
        from leakscope.phase3.asset_jobs import demo_sample
        original={'sources':[{'url':'https://example.com/no-signal'},{'url':'https://example.com/evidence'}],
                  'services':[{'provider':'Stripe','evidence':[{'source_url':'https://example.com/evidence','excerpt':'uses Stripe'}]}],
                  'usage_policy':{'search_requests':1,'result_limit':1},'requests':1}
        result=demo_sample(original,cached=True)
        self.assertEqual(result['services'][0]['evidence'][0]['source_url'],result['sources'][0]['url'])
        self.assertEqual(result['usage_policy']['search_requests'],0)
        self.assertEqual(original['usage_policy']['search_requests'],1)
        self.assertEqual(len(original['sources']),2)
