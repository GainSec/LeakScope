from django.test import SimpleTestCase, TestCase
from leakscope.intelligence.queries import compile_queries


class NativeDeviceQueryTests(SimpleTestCase):
    def test_modbus_vendor_model_queries_use_native_evidence_on_all_ports(self):
        signals=[{'field':'modbus.vendor','value':'Delta Electronics, Inc.'},{'field':'modbus.product','value':'AS228T-A'}]
        result=compile_queries(signals)
        for provider,query in result.items():
            self.assertTrue(query['supported'],(provider,query))
            self.assertIn('AS228T-A',query['query'])
            self.assertNotIn('port:',query['query']);self.assertNotIn('port=',query['query'])
        self.assertIn('shodan.module:modbus',result['shodan']['query'])
        self.assertIn('banner="Delta Electronics, Inc."',result['zoomeye']['query'])
        self.assertIn('modbus.mei_response.objects.vendor:',result['netlas']['query'])
        self.assertIn('modbus.mei_response.objects.product_code:',result['netlas']['query'])

    def test_bacnet_vendor_model_use_documented_decoded_fields(self):
        result=compile_queries([{'field':'bacnet.vendor','value':'Tridium'},{'field':'bacnet.model','value':'Niagara4 Station'}])
        for provider,query in result.items():self.assertTrue(query['supported'],provider)
        self.assertIn('shodan.module:bacnet',result['shodan']['query'])
        self.assertIn('bacnet.model_name:"Niagara4 Station"',result['netlas']['query'])

    def test_generic_and_native_fields_preserve_every_predicate_and_scope(self):
        result=compile_queries([{'field':'modbus.vendor','value':'Delta'},{'field':'country','value':'US'}],scope={'network':'192.0.2.0/24'})
        for provider,query in result.items():
            self.assertTrue(query['supported'],provider)
            self.assertIn('Delta',query['query']);self.assertIn('US',query['query']);self.assertIn('192.0.2.0/24',query['query'])

    def test_any_and_alternatives_do_not_silently_change_meaning(self):
        result=compile_queries([{'field':'modbus.vendor','value':'Delta'},{'field':'bacnet.vendor','value':'Tridium'}],mode='any')
        self.assertFalse(result['shodan']['supported'])
        self.assertIn(' || ',result['zoomeye']['query']);self.assertIn(' OR ',result['netlas']['query'])
        result=compile_queries([{'field':'modbus.vendor','value':'Delta'},{'field':'modbus.vendor','value':'ABB'}])
        self.assertFalse(result['shodan']['supported'])
        self.assertIn(' || ',result['zoomeye']['query']);self.assertIn(' OR ',result['netlas']['query'])

    def test_unknown_mixed_field_is_unavailable_without_dropping_it(self):
        result=compile_queries([{'field':'modbus.vendor','value':'Delta'},{'field':'madeup','value':'secret'}])
        self.assertTrue(all(not query['supported'] for query in result.values()))

    def test_native_values_cannot_break_out_of_query_quotes(self):
        result=compile_queries([{'field':'bacnet.model','value':'Niagara" OR port:22'}])
        for query in result.values():
            self.assertTrue(query['supported']);self.assertIn('Niagara\\" OR port:22',query['query'])

    def test_builtin_profiles_have_protocol_identity_and_three_queries(self):
        from leakscope.phase3.device_profiles import DEVICE_PROFILES
        self.assertEqual(set(DEVICE_PROFILES),{'delta_modbus','delta_as228t_a','tridium_bacnet','niagara4_bacnet'})
        for key,profile in DEVICE_PROFILES.items():
            self.assertIn(profile['service_type'],{'modbus','bacnet'})
            self.assertEqual(set(profile['queries']),{'shodan','zoomeye','netlas'})
            for query in profile['queries'].values():
                self.assertTrue(query['supported']);self.assertTrue(query['basis'])
                self.assertNotIn('port:',query['query']);self.assertNotIn('port=',query['query'])


class NativeDeviceEvidenceTests(TestCase):
    def setUp(self):
        from leakscope.phase3.state import ingest_observation
        self.modbus=ingest_observation('fetch:ot',{'address':'192.0.2.20','port':33502,'service_type':'modbus','metadata':{'device':{'objects':[{'id':0,'value':'Delta Electronics, Inc.'},{'id':1,'value':'AS228T-A'},{'id':2,'value':'V1.08.20'}]}}})
        self.bacnet=ingest_observation('fetch:ot',{'address':'192.0.2.21','port':47809,'transport':'udp','service_type':'bacnet','metadata':{'device':{'vendor_name':'Tridium','model_name':'Niagara4 Station','firmware_revision':'4.7','application_software_version':'Tridium 4.7'}}})

    def fields(self,endpoint):
        from leakscope_app.intelligence import _source_context
        return _source_context({'references':[{'type':'endpoint','id':str(endpoint.pk)}]}, {})['fields']

    def test_native_device_objects_are_observed_fields_with_actual_provenance(self):
        fields=self.fields(self.modbus);byfield={field['field']:field for field in fields}
        self.assertEqual(byfield['modbus.vendor']['value'],'Delta Electronics, Inc.')
        self.assertEqual(byfield['modbus.product']['value'],'AS228T-A')
        self.assertEqual(byfield['modbus.revision']['value'],'V1.08.20')
        self.assertEqual(byfield['modbus.vendor']['provenance']['provider'],'fetch:ot')
        self.assertTrue(byfield['modbus.vendor']['provenance']['observation_id'])

    def test_bacnet_native_fields_and_imported_keys_are_supported_without_guessing(self):
        self.assertEqual({f['field']:f['value'] for f in self.fields(self.bacnet) if f['field'].startswith('bacnet.')},
            {'bacnet.vendor':'Tridium','bacnet.model':'Niagara4 Station','bacnet.firmware':'4.7','bacnet.application':'Tridium 4.7'})
        from leakscope.phase3.state import ingest_observation
        imported=ingest_observation('icstracker:index',{'address':'192.0.2.22','port':47808,'service_type':'bacnet','metadata':{'device':{'Vendor Name':'Tridium','Model Name':'Niagara4 Station'}}})
        self.assertIn('bacnet.model',[f['field'] for f in self.fields(imported)])

    def test_netlas_provider_structure_extracts_named_identifiers(self):
        from leakscope.phase3.state import ingest_observation
        endpoint=ingest_observation('netlas',{'address':'192.0.2.23','port':33502,'service_type':'modbus','metadata':{'raw':{'modbus':{'mei_response':{'objects':{'vendor':'ABB','product_code':'Test','revision':'1'}}}}}})
        values={f['field']:f['value'] for f in self.fields(endpoint)}
        self.assertEqual(values['modbus.vendor'],'ABB');self.assertEqual(values['modbus.product'],'Test')

    def test_hidden_observation_cannot_supply_fields_on_an_otherwise_visible_endpoint(self):
        from leakscope.phase3.state import ingest_observation
        self.modbus.observations.update(hidden=True)
        ingest_observation('fixture',{'address':self.modbus.address,'port':self.modbus.port,'service_type':'modbus','metadata':{}})
        self.assertFalse(any(f['field'].startswith('modbus.') for f in self.fields(self.modbus)))

    def test_native_redacted_values_are_not_query_signals(self):
        from leakscope_app.intelligence import _observed_fields
        fields=_observed_fields(self.modbus,'endpoint:'+str(self.modbus.pk),{'EXAMPLE_API_KEY':'AS228T-A'})
        self.assertNotIn('modbus.product',[f['field'] for f in fields])

    def test_provider_banner_and_generic_identity_have_named_native_fields(self):
        from leakscope.phase3.state import ingest_observation
        endpoint=ingest_observation('zoomeye',{'address':'192.0.2.24','port':33502,'service_type':'modbus','metadata':{'protocol':'tcp','service':'modbus','banner':'Device Identification\nVendorName:Delta Electronics, Inc.\nProductCode:AS228T-A\nRevision:1.2'}})
        values={f['field']:f['value'] for f in self.fields(endpoint)}
        self.assertEqual(values['modbus.vendor'],'Delta Electronics, Inc.')
        self.assertEqual(values['modbus.product'],'AS228T-A')
        endpoint=ingest_observation('fetch:identity',{'address':'192.0.2.25','port':33502,'service_type':'modbus','metadata':{'objects':[{'id':0,'value':'ABB'},{'id':1,'value':'Device'}]}})
        self.assertEqual(next(f['value'] for f in self.fields(endpoint) if f['field']=='modbus.vendor'),'ABB')

    def test_register_labels_titles_and_malformed_objects_do_not_become_native_identity(self):
        from leakscope.phase3.state import ingest_observation
        endpoint=ingest_observation('fixture',{'address':'192.0.2.26','port':33502,'service_type':'modbus','title':'Delta AS228T-A','metadata':{'points':[{'label':'VendorName:ABB'}],'device':{'objects':[{'id':True,'value':'Wrong'},{'id':7,'value':'Not vendor'},{'value':'No id'},{'id':[],'value':'Unhashable id'},{'id':{},'value':'Unhashable map id'}]}}})
        self.assertFalse(any(f['field'].startswith('modbus.') for f in self.fields(endpoint)))
