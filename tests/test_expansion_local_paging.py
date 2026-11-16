"""Local collection pages retain names and correlation state without network calls."""
import time
from unittest.mock import patch
from django.test import TestCase
from leakscope.phase3 import expansion_sources as sources
from leakscope.phase3 import relationships as store
from leakscope_app.models import AssetEvidence,AssetNode,CertificateName,CertificateRecord


class ExpansionLocalPagingTests(TestCase):
    def setUp(self):
        self.context={'deadline':time.monotonic()+30,'seed_domains':['example.com'],'company_names':['Example Corp']}
        self.seed={'kind':'domain','value':'example.com','depth':0}
    def collect(self,source,cursor=None):
        context={**self.context,'cursor':cursor or {}}
        return sources.collect(source,self.seed,config={},spend=lambda *a,**k:self.fail('local collection must not spend'),context=context)
    def pages(self,source):
        # Fixture insertion is not part of the source collection deadline.
        self.context['deadline']=time.monotonic()+30
        cursor=None;seen=[]
        for _ in range(30):
            result=self.collect(source,cursor);seen.append(result)
            if 'continuation' not in result:return seen
            self.assertNotEqual(result['continuation'],cursor,'local pagination must advance')
            cursor=result['continuation']
        self.fail('local pagination did not terminate')
    def test_retained_certificates_have_resumable_record_pages(self):
        records=[CertificateRecord(key=f'cert-{i}',domains=[f'host{i}.example.com']) for i in range(270)]
        CertificateRecord.objects.bulk_create(records)
        CertificateName.objects.bulk_create([CertificateName(certificate=record,name=record.domains[0]) for record in records])
        first=self.collect('certificates')
        self.assertIn('continuation',first)
        self.assertLessEqual(len(first['candidates']),250)
        pages=self.pages('certificates')
        names=[c['domain'] for page in pages for c in page['candidates']]
        self.assertEqual(len(names),270)
        self.assertEqual(len(set(names)),270)
    def test_record_page_retains_all_sans_on_each_fetched_certificate(self):
        names=[f'host{i}.example.com' for i in range(610)]
        cert=CertificateRecord.objects.create(key='many-sans',domains=names)
        CertificateName.objects.create(certificate=cert,name=names[0])
        result=self.collect('certificates')
        self.assertEqual({c['domain'] for c in result['candidates']},set(names))
    def test_saved_domain_proofs_have_resumable_record_pages(self):
        nodes=[AssetNode(key=f'node-{i}',kind='domain',value=f'host{i}.example.com') for i in range(270)]
        AssetNode.objects.bulk_create(nodes)
        AssetEvidence.objects.bulk_create([AssetEvidence(key=f'proof-{i}',subject=node,source='dns',data={'name':node.value}) for i,node in enumerate(nodes)])
        first=self.collect('saved')
        self.assertIn('continuation',first)
        self.assertLessEqual(len(first['candidates']),250)
        pages=self.pages('saved')
        names={c['domain'] for page in pages for c in page['candidates']}
        self.assertEqual(names,{node.value for node in nodes})
    def test_identifier_correlation_survives_page_boundaries(self):
        root=store.node('domain','example.com')
        store.evidence(root,'http',{'tracking':'GTM-EXAMPLE1'})
        other=store.node('domain','different.net')
        store.evidence(other,'http',{'tracking':'GTM-EXAMPLE1'})
        noise=[AssetNode(key=f'noise-{i}',kind='domain',value=f'unrelated{i}.net') for i in range(260)]
        AssetNode.objects.bulk_create(noise)
        AssetEvidence.objects.bulk_create([AssetEvidence(key=f'noise-proof-{i}',subject=node,source='http',data={'title':'nothing shared'}) for i,node in enumerate(noise)])
        first=self.collect('saved')
        self.assertIn('continuation',first)
        pages=self.pages('saved')
        matches=[c for page in pages for c in page['candidates'] if c['domain']=='different.net']
        self.assertEqual(len(matches),1)
        self.assertIn('GTM-EXAMPLE1',matches[0]['identifiers'])
    def test_pivot_edge_pages_do_not_drop_domains(self):
        root=store.node('domain','example.com');address=store.node('ip','192.0.2.1')
        store.relation(root,address,'resolves_to',store.evidence(root,'dns',{'address':address.value}))
        for i in range(260):
            name=store.node('domain',f'other{i}.net')
            store.relation(name,address,'resolves_to',store.evidence(name,'dns',{'address':address.value}))
        pages=self.pages('saved')
        names={c['domain'] for page in pages for c in page['candidates']}
        self.assertTrue({f'other{i}.net' for i in range(260)}<=names)
        self.assertGreater(len(pages),1)

    def test_deadline_retains_next_record_for_both_local_sources(self):
        nodes=[store.node('domain',f'host{i}.example.com') for i in range(3)]
        for i,node in enumerate(nodes):
            store.evidence(node,'dns',{'name':node.value})
            record=CertificateRecord.objects.create(key=f'deadline-cert-{i}',domains=[node.value])
            CertificateName.objects.create(certificate=record,name=node.value)
        for source,collector in [('saved',sources._saved),('certificates',sources._certificates)]:
            with self.subTest(source=source):
                first={'candidates':[],'leads':[],'notes':[]}
                with patch.object(sources.time,'monotonic',side_effect=[0,100]):
                    collector(self.seed,{**self.context,'deadline':50},first)
                self.assertEqual(len(first['candidates']),1)
                self.assertIn('continuation',first)
                self.assertEqual(first['blocked'],'deadline')
                following=self.collect(source,first['continuation'])
                expected={node.value for node in nodes}
                combined=[c['domain'] for c in first['candidates']+following['candidates'] if c['domain'] in expected]
                self.assertEqual(set(combined),expected)
                self.assertEqual(len(combined),3)

    def test_identifier_seed_only_uses_independent_matching_proofs(self):
        first=store.node('domain','brand-a.net');second=store.node('domain','brand-b.net')
        store.evidence(first,'http',{'tracking':['GTM-EXAMPLE1','GTM-EXAMPLE2']})
        store.evidence(second,'http',{'tracking':'GTM-EXAMPLE2'})
        store.evidence(store.node('domain','derived.net'),'domain_expansion',{'tracking':'GTM-EXAMPLE1'})
        self.seed={'kind':'identifier','value':'GTM-EXAMPLE1','depth':0}
        result=self.collect('saved')
        self.assertEqual({c['domain'] for c in result['candidates']},{first.value,second.value})
        self.assertTrue(all(c['evidence'][0]['signal']=='shared_identifier' for c in result['candidates']))
