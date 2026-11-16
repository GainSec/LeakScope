import json
from django.test import TestCase, Client
from django.core.files.uploadedfile import SimpleUploadedFile
from pathlib import Path
from leakscope.phase3.state import ingest_observation

BASE='/api/operations/signatures'
JA4S='t1205o1_c02f_1ece00aee4e9'

class TechnologySignatureTests(TestCase):
    def post(self,path,body):return self.client.post(path,json.dumps(body),content_type='application/json')
    def create(self,**extra):
        return self.post(BASE,{'name':'Fixture server','technology':'fixture','kind':'ja4s','value':JA4S,**extra})
    def test_two_seeded_native_signatures_have_probe_and_direction_scope(self):
        response=self.client.get(BASE);self.assertEqual(response.status_code,200)
        rows=response.json()['results'];self.assertEqual({r['technology'] for r in rows},{'amaru','cardanonode'})
        self.assertTrue(all(r['kind']=='ouroboros' and r['scope']['direction']=='response' for r in rows))
    def test_signature_create_edit_disable_and_delete_are_durable(self):
        response=self.create();self.assertEqual(response.status_code,201);pk=response.json()['signature']['id']
        self.assertEqual(self.post(BASE+'/'+pk,{'name':'Renamed','enabled':False}).status_code,200)
        row=next(r for r in self.client.get(BASE).json()['results'] if r['id']==pk)
        self.assertEqual((row['name'],row['enabled']),('Renamed',False))
        self.assertEqual(self.post(BASE+'/'+pk+'/delete',{}).status_code,200)
        self.assertFalse(any(r['id']==pk for r in self.client.get(BASE).json()['results']))
    def test_invalid_fingerprints_and_unsupported_rules_rejected(self):
        for extra in ({'kind':'ja4s','value':'bad'}, {'kind':'ja4ts','value':'1_2-3_1460_999'}, {'match':'regex'}, {'enabled':'yes'}, {'kind':'made-up'}):
            self.assertEqual(self.create(**extra).status_code,400,extra)
    def test_signature_writes_require_csrf(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post(BASE,'{}',content_type='application/json').status_code,403)
    def test_new_observations_match_saved_exact_ja4_signature(self):
        self.assertEqual(self.create().status_code,201)
        o=ingest_observation('netlas',{'address':'192.0.2.44','port':443,'metadata':{'fingerprints':[{'kind':'ja4s','value':JA4S,'direction':'response'}]}})
        self.assertEqual(o.observations.latest('last_collected_at').data['metadata']['signature_matches'][0]['technology'],'fixture')
    def test_colliding_signatures_do_not_choose_one_technology(self):
        self.assertEqual(self.create().status_code,201);self.assertEqual(self.create(name='Other',technology='other').status_code,201)
        o=ingest_observation('netlas',{'address':'192.0.2.44','port':443,'metadata':{'fingerprints':[{'kind':'ja4s','value':JA4S,'direction':'response'}]}})
        self.assertEqual(o.observations.latest('last_collected_at').data['metadata']['signature_status'],'ambiguous')
        self.assertNotIn('implementation',o.observations.latest('last_collected_at').data['metadata'])
    def test_shared_tcp_fingerprint_has_no_seeded_implementation_match(self):
        o=ingest_observation('netlas',{'address':'192.0.2.44','port':3001,'metadata':{'fingerprints':[{'kind':'ja4ts','value':'65160_2-4-8-1-3_1460_7','direction':'response'}]}})
        self.assertEqual(o.observations.latest('last_collected_at').data['metadata'].get('signature_matches',[]),[])
    def test_incoming_mux_or_missing_probe_context_does_not_label_implementation(self):
        for direction in ('request','response'):
            o=ingest_observation('native:identity',{'address':'192.0.2.44','port':3001,'metadata':{'fingerprints':[{'kind':'ouroboros','value':'network magic mismatch','direction':direction}]}})
            self.assertEqual(o.observations.latest('last_collected_at').data['metadata'].get('signature_matches',[]),[])
    def test_scoped_native_refusal_labels_response_family(self):
        fp={'kind':'ouroboros','value':'network magic mismatch','direction':'response','probe':'ouroboros.network-magic-mismatch.v1','protocol':0,'version':15,'message_type':2,'reason_code':2}
        o=ingest_observation('native:identity',{'address':'192.0.2.44','port':3001,'service_type':'cardano','metadata':{'fingerprints':[fp]}})
        self.assertEqual(o.observations.latest('last_collected_at').data['metadata'].get('implementation'),'amaru')
        self.assertEqual(o.observations.latest('last_collected_at').data['metadata']['implementation_basis'],'response signature; experimental')
    def test_disabled_rule_and_forged_match_fields_are_not_used(self):
        response=self.create();self.assertEqual(response.status_code,201);r=response.json()['signature'];self.post(BASE+'/'+r['id'],{'enabled':False})
        o=ingest_observation('netlas',{'address':'192.0.2.44','port':443,'metadata':{'signature_matches':[{'technology':'forged'}],'implementation':'forged','fingerprints':[{'kind':'ja4s','value':JA4S,'direction':'response'}]}})
        self.assertEqual(o.observations.latest('last_collected_at').data['metadata'].get('signature_matches',[]),[])
        self.assertNotIn('implementation',o.observations.latest('last_collected_at').data['metadata'])
    def test_capture_preview_is_offline_and_does_not_create_inventory(self):
        from leakscope_app.models import ServiceEndpoint
        data=(Path(__file__).parent/'fixtures/ja4/controls.pcap').read_bytes()
        r=self.client.post(BASE+'/import',{'capture':SimpleUploadedFile('control.pcap',data)})
        self.assertEqual(r.status_code,200);self.assertGreater(len(r.json()['results']),0)
        self.assertEqual(ServiceEndpoint.objects.count(),0)
    def test_invalid_capture_import_is_rejected_without_writes(self):
        self.assertEqual(self.client.post(BASE+'/import',{'capture':SimpleUploadedFile('bad.pcap',b'bad')}).status_code,400)
    def test_initiator_ja4_is_not_assigned_to_the_listening_server(self):
        self.assertEqual(self.create(kind='ja4t',value='65160_2-4-8-1-3_1460_7').status_code,201)
        o=ingest_observation('import:ja4',{'address':'192.0.2.44','port':443,'metadata':{'fingerprints':[{'kind':'ja4t','value':'65160_2-4-8-1-3_1460_7','direction':'request','subject':'probe'}]}})
        self.assertEqual(o.observations.latest('last_collected_at').data['metadata']['signature_matches'],[])
    def test_requested_profile_stays_unverified_but_independent_signature_is_visible(self):
        self.assertEqual(self.create().status_code,201)
        endpoint=ingest_observation('netlas',{'address':'192.0.2.44','port':443,'service_type':'https','metadata':{'service_type_basis':'Requested search profile; identity not verified','fingerprints':[{'kind':'ja4s','value':JA4S,'direction':'response'}]}})
        from leakscope_app.atlas import classify_levels
        observation=endpoint.observations.latest('last_collected_at')
        levels={i['key']:i['level'] for i in classify_levels(observation.data,'netlas')}
        self.assertEqual(levels.get('fixture'),'signature_matched')
        self.assertEqual(levels.get('unverified'),'candidate');self.assertNotIn('https',levels)
