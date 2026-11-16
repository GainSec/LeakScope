from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone

from leakscope.phase3 import relationships as store
from leakscope.phase3.state import ingest_observation


class AssetContextSavedDnsTests(TestCase):
    def record(self,name,value,kind='A',expires=None,**extra):
        return {'name':name,'type':kind,'value':value,'expires_at':(expires or timezone.now()+timedelta(minutes=5)).isoformat(),**extra}

    def context(self,target):
        with patch('socket.getaddrinfo',side_effect=AssertionError('No implicit DNS lookup')):
            response=self.client.get('/api/assets/context',{'target':target})
        self.assertEqual(response.status_code,200,response.content)
        return response.json()

    def test_fresh_exact_domain_addresses_enable_followups_without_lookup(self):
        store.ingest_dns({'target':'gainsec.com','records':[
            self.record('gainsec.com','192.0.2.10'),self.record('gainsec.com','2001:db8::10','AAAA')]})
        data=self.context('gainsec.com')
        self.assertEqual(set(data['ips']),{'192.0.2.10','2001:db8::10'})
        self.assertTrue(data['actions']['resolve'])
        self.assertTrue(data['actions']['netblock'])
        self.assertEqual(data['target'],'gainsec.com')
        self.assertEqual({item['address'] for item in data['ip_sources']},set(data['ips']))
        self.assertTrue(all(item['source']=='dns' and item['evidence_id'] and item['expires_at'] for item in data['ip_sources']))

    def test_expired_missing_expiry_and_sibling_addresses_are_not_followup_targets(self):
        store.ingest_dns({'target':'gainsec.com','records':[
            self.record('gainsec.com','192.0.2.20',expires=timezone.now()-timedelta(seconds=1)),
            {'name':'gainsec.com','type':'AAAA','value':'2001:db8::20'},
            self.record('mail.gainsec.com','192.0.2.21'),
            self.record('other.example','192.0.2.22')]})
        self.assertEqual(self.context('gainsec.com')['ips'],[])

    def test_cached_answer_query_owner_handles_cname_target_but_not_other_queries(self):
        root=store.node('domain','gainsec.com')
        store.evidence(root,'enrichment:dns',{'result':{'target':'gainsec.com','records':[
            self.record('edge.example','192.0.2.30',query_name='gainsec.com'),
            self.record('mail.example','192.0.2.31',query_name='mail.gainsec.com')]}},expires_at=timezone.now()+timedelta(minutes=2))
        self.assertEqual(self.context('gainsec.com')['ips'],['192.0.2.30'])

    def test_expired_cache_or_individual_record_and_wildcard_scope_are_not_used(self):
        root=store.node('domain','gainsec.com')
        store.evidence(root,'enrichment:dns',{'result':{'target':'gainsec.com','records':[
            self.record('gainsec.com','192.0.2.40')]}},expires_at=timezone.now()-timedelta(seconds=1))
        store.evidence(root,'enrichment:dns',{'result':{'target':'gainsec.com','records':[
            self.record('gainsec.com','192.0.2.41',expires=timezone.now()-timedelta(seconds=1))]}},expires_at=timezone.now()+timedelta(minutes=1))
        self.assertEqual(self.context('gainsec.com')['ips'],[])
        self.assertEqual(self.context('*.gainsec.com')['ips'],[])

    def test_hidden_source_proof_cannot_supply_saved_dns_address(self):
        endpoint=ingest_observation('fixture',{'address':'192.0.2.1','port':443,'metadata':{}})
        root=store.node('domain','gainsec.com')
        source=endpoint.observations.get()
        store.evidence(root,'dns',dict(self.record('gainsec.com','192.0.2.50'),observation_pk=source.pk.hex),expires_at=timezone.now()+timedelta(minutes=5))
        source.hidden=True;source.save(update_fields=['hidden'])
        self.assertEqual(self.context('gainsec.com')['ips'],[])

    def test_endpoint_context_preserves_observed_ip_and_adds_saved_fresh_address(self):
        endpoint=ingest_observation('fixture',{'address':'gainsec.com','port':443,'scheme':'https',
            'metadata':{'resolved_ip':'192.0.2.60'}})
        store.ingest_dns({'target':'gainsec.com','records':[self.record('gainsec.com','192.0.2.61')]})
        with patch('socket.getaddrinfo',side_effect=AssertionError('No implicit DNS lookup')):
            response=self.client.get('/api/assets/context',{'type':'endpoint','id':str(endpoint.pk)})
        self.assertEqual(response.status_code,200)
        self.assertEqual(set(response.json()['ips']),{'192.0.2.60','192.0.2.61'})
        self.assertEqual(response.json()['target'],'gainsec.com')

    def test_malformed_cache_or_mismatched_query_target_cannot_supply_an_ip(self):
        root=store.node('domain','gainsec.com')
        future=timezone.now()+timedelta(minutes=5)
        store.evidence(root,'enrichment:dns',[],expires_at=future)
        store.evidence(root,'enrichment:dns',{'result':{'target':'other.example','records':[
            self.record('gainsec.com','192.0.2.70')]}},expires_at=future)
        store.ingest_dns({'target':'gainsec.com','records':[
            self.record('gainsec.com','192.0.2.71','AAAA'),self.record('gainsec.com','2001:db8::71','A')]})
        self.assertEqual(self.context('gainsec.com')['ips'],[])
