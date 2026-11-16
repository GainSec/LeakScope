from django.test import SimpleTestCase

from leakscope.phase3.collection_policy import COLLECTION_MODES, plan_collection, validate_collection_mode


def endpoint(service='unknown', scheme='', transport='tcp'):
    return {'address':'192.0.2.27','port':42137,'transport':transport,'service_type':service,'scheme':scheme}


class CollectionPolicyTests(SimpleTestCase):
    def test_modes_validate_without_silently_broadening(self):
        self.assertEqual(COLLECTION_MODES,('passive','demo','semi_passive','screenshot','full'))
        for mode in COLLECTION_MODES:self.assertEqual(validate_collection_mode(mode),mode)
        self.assertEqual(validate_collection_mode('',allow_custom=True),'')
        for mode in ('',None,False,{},'FULL','semi-passive','custom'):
            with self.subTest(mode=mode),self.assertRaises(ValueError):validate_collection_mode(mode)

    def test_passive_never_plans_a_target_action(self):
        for target in (endpoint('elastic','https'),endpoint('s7'),endpoint('grpc','https'),endpoint('bacnet',transport='udp')):
            plan=plan_collection(target,'passive')
            self.assertEqual(plan,{'mode':'passive','actions':[],'skipped':[]})

    def test_semi_passive_prefers_one_http_fingerprint_with_curl_fallback(self):
        target=endpoint('http','http')
        self.assertEqual(plan_collection(target,'semi_passive')['actions'],['httpx'])
        plan=plan_collection(target,'semi_passive',{'curl':True,'httpx':False,'gowitness':True})
        self.assertEqual(plan['actions'],['http'])
        self.assertTrue(any('httpx' in reason for reason in plan['skipped']))
        plan=plan_collection(target,'semi_passive',{'curl':False,'httpx':False,'gowitness':True})
        self.assertEqual(plan['actions'],['check'])
        self.assertTrue(plan['skipped'])

    def test_observed_http_takes_priority_over_native_profile_identity(self):
        self.assertEqual(plan_collection(endpoint('s7','http'),'semi_passive')['actions'],['httpx'])

    def test_native_identity_already_retains_wire_and_grpc_is_not_a_web_page(self):
        for target in (endpoint('s7'),endpoint('grpc','https'),endpoint('bacnet',transport='udp')):
            for mode in ('semi_passive','full'):
                self.assertEqual(plan_collection(target,mode)['actions'],['identity'])
            plan=plan_collection(target,'screenshot')
            self.assertEqual(plan['actions'],[])
            self.assertTrue(plan['skipped'])

    def test_unknown_tcp_is_reachability_only_until_full_is_selected(self):
        self.assertEqual(plan_collection(endpoint(),'semi_passive')['actions'],['check'])
        self.assertEqual(plan_collection(endpoint(),'full')['actions'],['check','tcp'])

    def test_standard_web_port_alone_does_not_trigger_automatic_http(self):
        target=dict(endpoint(),port=443)
        self.assertEqual(plan_collection(target,'semi_passive')['actions'],['check'])
        self.assertEqual(plan_collection(target,'full')['actions'],['check','tcp'])
        self.assertEqual(plan_collection(target,'screenshot')['actions'],[])
        target['scheme']='https'
        self.assertEqual(plan_collection(target,'semi_passive')['actions'],['httpx'])

    def test_screenshot_only_and_full_http_do_not_add_redundant_tcp(self):
        target=endpoint('https','https')
        self.assertEqual(plan_collection(target,'screenshot')['actions'],['screenshot'])
        self.assertEqual(plan_collection(target,'full')['actions'],['http','httpx','screenshot'])
        plan=plan_collection(target,'full',{'curl':True,'httpx':False,'gowitness':False})
        self.assertEqual(plan['actions'],['http'])
        self.assertTrue(any('httpx' in reason for reason in plan['skipped']))
        self.assertTrue(any('Screenshot' in reason for reason in plan['skipped']))
        plan=plan_collection(target,'screenshot',{'curl':True,'httpx':True,'gowitness':False})
        self.assertEqual(plan['actions'],[])
        self.assertTrue(any('Screenshot' in reason for reason in plan['skipped']))

    def test_unknown_udp_never_gets_a_guessed_payload_or_fake_connection_check(self):
        for mode in ('semi_passive','screenshot','full'):
            plan=plan_collection(endpoint(transport='udp'),mode)
            self.assertEqual(plan['actions'],[])
            self.assertTrue(plan['skipped'])
