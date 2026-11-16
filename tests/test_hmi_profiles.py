from django.test import SimpleTestCase


class HmiProfileTests(SimpleTestCase):
    def test_hmi_profiles_have_all_port_queries_and_capture_on_unusual_ports(self):
        from leakscope.phase3.catalog import catalog_entry,profile_query
        from leakscope.phase3.capture import supported_actions
        for key in ['wago_hmi','codesys_webvisu','keba_kecontact','comet_websensor','kaco_blueplanet','rockware_magicimm','ospa_pool','pump_status_hmi']:
            item=catalog_entry(key)
            self.assertEqual(item['category'],'ot')
            for provider in ('shodan','zoomeye','netlas'):
                query=profile_query(key,provider,'fingerprint')
                self.assertTrue(query['supported'])
                self.assertNotIn('port:',query['query'])
                self.assertNotIn('port=',query['query'])
            self.assertIn('screenshot',supported_actions({'address':'127.0.0.1','port':33490,'service_type':key,'transport':'tcp','scheme':''}))

    def test_hmi_classification_needs_cooccurring_ui_signals(self):
        from leakscope.phase3.hmi_profiles import match_hmi
        self.assertIn('wago_hmi',[m['key'] for m in match_hmi('WAGO','Hostname: controller Description: PFC200')])
        self.assertEqual(match_hmi('WAGO','Product catalog and download center'),[])
        self.assertIn('comet_websensor',[m['key'] for m in match_hmi('Web Sensor','MinMax mem Mobile web Events viewer')])

    def test_vnc_is_a_native_screenshot_capability_on_any_tcp_port(self):
        from leakscope.phase3.catalog import profile_query
        from leakscope.phase3.capture import supported_actions
        self.assertIn('screenshot',supported_actions({'address':'127.0.0.1','port':42187,'service_type':'vnc','transport':'tcp','scheme':''}))
        for provider in ('shodan','zoomeye','netlas'):
            self.assertTrue(profile_query('vnc',provider)['supported'])
