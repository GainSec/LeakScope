from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from leakscope.phase3.state import ingest_observation, set_tags
from leakscope_app.models import (CertificateRecord, CertificateName, CloudResource,
    Gitlab, Mongo, Search, FindingReview)


class SavedTableSortingTests(TestCase):
    def endpoints(self, **query):
        response = self.client.get('/api/operations/endpoints', query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_endpoint_sort_is_global_before_pagination_and_preserves_filter(self):
        for address in ['z.example.test', 'b.example.test', 'a.example.test']:
            ingest_observation('shodan', {'address': address, 'port': 443, 'service_type': 'http'})
        ingest_observation('zoomeye', {'address': '0.example.test', 'port': 443})
        data = self.endpoints(sort='endpoint', direction='asc', provider='shodan', page=2, page_size=1)
        self.assertEqual(data['count'], 3)
        self.assertEqual(data['results'][0]['address'], 'b.example.test')
        self.assertEqual(self.endpoints(sort='endpoint', direction='desc', provider='shodan', page_size=1)['results'][0]['address'], 'z.example.test')

    def test_endpoint_tags_and_observation_counts_sort_before_slice(self):
        a = ingest_observation('shodan', {'address': 'a.example.test', 'port': 443})
        b = ingest_observation('shodan', {'address': 'b.example.test', 'port': 443})
        ingest_observation('shodan', {'address': 'c.example.test', 'port': 443})
        ingest_observation('zoomeye', {'address': 'b.example.test', 'port': 443})
        set_tags('endpoint', a.pk, ['Zulu', 'Beta'])
        set_tags('endpoint', b.pk, ['Alpha'])
        self.assertEqual([x['address'] for x in self.endpoints(sort='tags', direction='asc')['results']], ['b.example.test','a.example.test','c.example.test'])
        self.assertEqual([x['address'] for x in self.endpoints(sort='tags', direction='desc')['results']], ['a.example.test','b.example.test','c.example.test'])
        self.assertEqual(self.endpoints(sort='observations', direction='desc', page_size=1)['results'][0]['address'], 'b.example.test')

    def test_certificate_name_and_issuer_sort_globally_missing_last(self):
        for key, name, issuer in [('a','z.example.test', {'O':'Zulu'}),('b','a.example.test',{'O':'Alpha'}),('c',None,{})]:
            row = CertificateRecord.objects.create(key=key*64, fingerprint=key*64, domains=[name] if name else [], issuer=issuer)
            if name: CertificateName.objects.create(certificate=row, name=name)
        response = self.client.get('/api/assets/certificates', {'sort':'names','direction':'asc','page_size':1,'page':2})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['results'][0]['key'], 'a'*64)
        response = self.client.get('/api/assets/certificates', {'sort':'issuer','direction':'desc'})
        self.assertEqual([x['key'][0] for x in response.json()['results']], ['a','b','c'])

    def test_cloud_sort_preserves_filters_and_global_pagination(self):
        for name in ['zeta','beta','alpha']:
            CloudResource.objects.create(name=name, url='https://'+name+'.example.test',provider='aws',kind='bucket',state='public')
        CloudResource.objects.create(name='aaa', url='https://aaa.example.test',provider='google',kind='bucket',state='public')
        r = self.client.get('/api/operations/cloud/resources', {'sort':'name','direction':'asc','provider':'aws','page_size':1,'page':2})
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(r.json()['results'][0]['name'],'beta')

    def test_legacy_merge_sorts_before_each_model_top_k(self):
        source = Search.objects.create(type='gitlab')
        for model, addresses in [(Gitlab,['z.example.test','b.example.test']), (Mongo,['x.example.test','a.example.test'])]:
            for address in addresses: model.objects.create(search=source,ip=address,port='443',provider='shodan')
        for direction, expected in [('asc',['a.example.test','b.example.test','x.example.test','z.example.test']),('desc',['z.example.test','x.example.test','b.example.test','a.example.test'])]:
            actual=[]
            for page in range(1,5):
                r=self.client.get('/api/findings',{'sort':'target','direction':direction,'page_size':1,'page':page})
                self.assertEqual(r.status_code,200,r.content)
                actual.extend(x['ip'] for x in r.json()['results'])
            self.assertEqual(actual,expected)

    def test_legacy_review_order_respects_hidden_overrides(self):
        source=Search.objects.create(type='gitlab')
        a=Gitlab.objects.create(search=source,ip='a.test',port='443',confirmed=False,for_later=False)
        b=Gitlab.objects.create(search=source,ip='b.test',port='443',confirmed=True)
        FindingReview.objects.create(type='gitlab',object_id=a.pk,hidden=True)
        r=self.client.get('/api/findings',{'sort':'status','direction':'asc','status':'all'})
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual([x['status'] for x in r.json()['results']],['confirmed','hidden'])

    def test_bad_sort_keys_and_directions_are_rejected(self):
        for url in ['/api/operations/endpoints','/api/assets/certificates','/api/operations/cloud/resources','/api/findings']:
            for query in [{'sort':'__secret'},{'direction':'sideways'}]:
                with self.subTest(url=url,query=query):
                    self.assertEqual(self.client.get(url,query).status_code,400)
