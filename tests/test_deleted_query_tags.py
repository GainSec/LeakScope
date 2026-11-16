import json
from django.test import TestCase
from leakscope_app.models import CustomQuery,SavedLookup,Tag,TagAssignment


class DeletedQueryTagTests(TestCase):
    def setUp(self):
        self.tag=Tag.objects.create(name='shared-review')

    def query(self,name='Anduril'):
        return CustomQuery.objects.create(name=name,query_template='hostname:"anduril.com"')

    def lookup(self,name='GainSec',query=None):
        return SavedLookup.objects.create(name=name,provider='shodan',query='hostname:"gainsec.com"',custom_query=query)

    def assign(self,kind,key,origin='manual'):
        return TagAssignment.objects.create(tag=self.tag,target_kind=kind,target_key=str(key),origin=origin)

    def test_model_query_delete_cleans_all_origins_but_keeps_other_ids_kinds_and_tag(self):
        removed=self.query();kept=self.query('Other query');key=str(removed.pk)
        self.assign('query',key);self.assign('query',key,'inherited:fixture')
        retained=[self.assign('query',kept.pk),self.assign('endpoint',key),self.assign('lookup',key)]
        removed.delete()
        self.assertFalse(TagAssignment.objects.filter(target_kind='query',target_key=key).exists())
        self.assertEqual(set(TagAssignment.objects.values_list('pk',flat=True)),{item.pk for item in retained})
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())
        self.assertTrue(CustomQuery.objects.filter(pk=kept.pk).exists())

    def test_queryset_lookup_delete_uses_uuid_key_and_preserves_other_namespaces(self):
        removed=self.lookup();kept=self.lookup('Other lookup');key=str(removed.pk)
        self.assign('lookup',key);self.assign('lookup',key,'saved:fixture')
        retained=[self.assign('lookup',kept.pk),self.assign('query',key),self.assign('endpoint',key)]
        SavedLookup.objects.filter(pk=removed.pk).delete()
        self.assertFalse(TagAssignment.objects.filter(target_kind='lookup',target_key=key).exists())
        self.assertEqual(set(TagAssignment.objects.values_list('pk',flat=True)),{item.pk for item in retained})
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())

    def test_query_api_delete_keeps_linked_lookup_and_its_tags(self):
        query=self.query();key=str(query.pk);lookup=self.lookup(query=query)
        self.assign('query',key);lookup_tag=self.assign('lookup',lookup.pk)
        response=self.client.post('/api/queries/'+key+'/delete',json.dumps({}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(CustomQuery.objects.filter(pk=key).exists())
        self.assertFalse(TagAssignment.objects.filter(target_kind='query',target_key=key).exists())
        lookup.refresh_from_db();self.assertIsNone(lookup.custom_query_id)
        self.assertTrue(TagAssignment.objects.filter(pk=lookup_tag.pk).exists())
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())

    def test_lookup_api_delete_removes_only_the_deleted_lookup_assignments(self):
        lookup=self.lookup();key=str(lookup.pk);self.assign('lookup',key)
        other_kind=self.assign('query',key)
        response=self.client.post('/api/operations/lookups/'+key+'/delete',json.dumps({}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(SavedLookup.objects.filter(pk=key).exists())
        self.assertFalse(TagAssignment.objects.filter(target_kind='lookup',target_key=key).exists())
        self.assertTrue(TagAssignment.objects.filter(pk=other_kind.pk).exists())
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())
