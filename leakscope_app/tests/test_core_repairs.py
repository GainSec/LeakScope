import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import TestCase, RequestFactory, override_settings
from celery.exceptions import Ignore
from leakscope import tasks
from leakscope_app import models, views


class CoreRepairTests(TestCase):
    def setUp(self):
        self.search = models.Search.objects.create(type='elastic', keyword='', country='', network='', provider='zoomeye')
        self.config = {'config': {'blacklist': []}}
        self.match = {'ip_str': '192.0.2.1', 'port': 9200, 'data': '{"cluster_name":"fixture","indices":[]}'}

    def test_result_inherits_search_provider(self):
        obj = models.Elastic.objects.create(search=self.search, ip='192.0.2.1', port='9200', name='', size='', indices='', indicator='')
        self.assertEqual(obj.provider, 'zoomeye')

    def test_passive_gcs_uses_valid_model_field(self):
        result = tasks.check_gcsbucket(0, self.match, self.search, self.config)
        self.assertTrue(result)
        self.assertEqual(models.GcsBucket.objects.count(), 1)

    def test_opensearch_has_own_model(self):
        tasks.check_opensearch(0, self.match, self.search, '', self.config)
        self.assertEqual(models.OpenSearch.objects.count(), 1)
        self.assertEqual(models.Elastic.objects.count(), 0)

    def test_same_ip_other_port_is_not_deduplicated(self):
        tasks.check_elastic(0, self.match, self.search, '', self.config)
        tasks.check_elastic(0, dict(self.match, port=9201), self.search, '', self.config)
        self.assertEqual(models.Elastic.objects.count(), 2)

    def test_database_blacklist_is_loaded(self):
        models.BlacklistEntry.objects.create(value='192.0.2.1')
        with patch.dict(os.environ, {'BLACKLIST': '', 'LEAKSCOPE_CONFIG_FILE': '/not-present'}):
            self.assertTrue(tasks.is_blacklisted('192.0.2.1', tasks.get_config()))

    def test_secret_config_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'private.json';p.write_text('{"config":{"SHODAN_API_KEY":"fixture-key"}}')
            with patch.dict(os.environ, {'LEAKSCOPE_CONFIG_FILE': str(p)}):
                self.assertEqual(tasks.get_config()['config']['SHODAN_API_KEY'], 'fixture-key')

    def test_zoomeye_keeps_quoted_phrases(self):
        query = tasks.build_provider_query('zoomeye', 'title:"hello world"', keyword='two words')
        self.assertIn('"hello world"', query)
        self.assertIn('"two words"', query)
        self.assertNotIn('hello && world', query)

    def test_shodan_fallback_variants_keep_scope(self):
        client=Mock();client.host_search.return_value={'matches': [], 'total': 0}
        with patch.object(tasks,'get_shodan_client',return_value=client), patch.object(tasks.check_main,'update_state'):
            with self.assertRaises(Ignore):
                tasks.check_main.run(self.search.pk,type='solr',network='192.0.2.0/24',country='US',keyword='hello',exclude='excluded')
        query=client.host_search.call_args.args[0]
        for value in ['net:192.0.2.0/24','country:US','hello','-excluded']:self.assertIn(value,query)

    def test_custom_provider_failure_is_terminal(self):
        q=models.CustomQuery.objects.create(name='fixture',query_template='test',default_type='elastic')
        with patch.object(tasks,'get_shodan_client',side_effect=tasks.ShodanClientError('fixture error')),patch.object(tasks.check_custom_query,'update_state') as state:
            with self.assertRaises(Ignore):tasks.check_custom_query.run(q.pk)
        self.assertEqual(state.call_args.kwargs['state'],'FAILURE')

    def test_export_success_not_overwritten(self):
        key=models.Keys.objects.create(search=self.search,ip='192.0.2.2',port='80',title='fixture')
        with patch.object(tasks.export_preview_data,'update_state') as state,patch('leakscope.exporters.service._write_export_file',return_value={'token':'a'*32,'path':'fixture.json'}):
            with self.assertRaises(Ignore):tasks.export_preview_data.run(type='keys',target_id=key.pk,active_probe=True)
        terminal=[x.kwargs['state'] for x in state.call_args_list if x.kwargs['state'] in ('SUCCESS','FAILURE')]
        self.assertEqual(terminal,['SUCCESS'])

    def test_exports_use_configured_root_and_retry_download(self):
        with tempfile.TemporaryDirectory() as d,override_settings(LEAKSCOPE_EXPORT_DIR=d,LEAKSCOPE_EXPORT_TTL=3600):
            info=tasks._write_export_file([{'fixture':True}])
            self.assertEqual(Path(info['path']).parent,Path(d))
            for _ in range(2):
                response=views.export_download(RequestFactory().get('/'),info['token'])
                self.assertEqual(response.status_code,200)
                self.assertTrue(b''.join(response.streaming_content))
                response.close()
            self.assertEqual(views.export_download(RequestFactory().get('/'),'../bad').status_code,404)

    def test_hidden_rows_are_not_in_database_context(self):
        obj=models.Elastic.objects.create(search=self.search,ip='192.0.2.1',port='9200',name='',size='',indices='',indicator='')
        models.FingerprintLog.objects.create(fingerprint=views._fingerprint('Elastic',obj.ip,obj.port),type='Elastic',ip=obj.ip,port=obj.port,status='hidden')
        with patch.object(views,'render',side_effect=lambda r,t,c:c):context=views.database(RequestFactory().get('/'))
        self.assertFalse(any(isinstance(x,models.Elastic) and x.pk==obj.pk for group in context['results'] for x in group))

    def test_dashboard_provider_counts_are_populated(self):
        models.Keys.objects.create(search=self.search,ip='192.0.2.2',port='80')
        with patch.object(tasks,'check_credits',return_value=0),patch.object(views,'render',side_effect=lambda r,t,c:c):context=views.index(RequestFactory().get('/'))
        self.assertEqual(sum(json.loads(context['zoomeye_data'])),1)

    def test_zoomeye_operator_clauses_not_conjoined_with_operators(self):
        query=tasks.build_provider_query('zoomeye','title:"A" OR title:"B"',country='US')
        self.assertNotIn('&& OR &&',query)
        self.assertIn('||',query)

    def test_configured_cidr_blacklist_blocks_member(self):
        self.assertTrue(tasks.is_blacklisted('192.0.2.10',{'config':{'blacklist':['192.0.2.0/24']}}))

    def test_expired_export_is_not_downloaded(self):
        import time
        with tempfile.TemporaryDirectory() as d,override_settings(LEAKSCOPE_EXPORT_DIR=d,LEAKSCOPE_EXPORT_TTL=60):
            info=tasks._write_export_file({'fixture':True})
            os.utime(info['path'],(time.time()-120,time.time()-120))
            self.assertEqual(views.export_download(RequestFactory().get('/'),info['token']).status_code,410)

    def test_standalone_review_identity(self):
        from leakscope.services.identity import set_hidden,is_hidden
        first=models.Javascript.objects.create(path='https://example.test/a.js',secrets='',context='',keyword='')
        second=models.Javascript.objects.create(path='https://example.test/b.js',secrets='',context='',keyword='')
        set_hidden('javascript',first,True)
        self.assertTrue(is_hidden('javascript',first))
        self.assertFalse(is_hidden('javascript',second))
        set_hidden('javascript',first,False)
        self.assertFalse(is_hidden('javascript',first))

    def test_registry_aliases_and_capabilities(self):
        from leakscope.services.registry import model_for_type,service_types
        self.assertEqual(model_for_type('harbor'),models.Registry)
        self.assertEqual(model_for_type('consulkv'),models.ConsulKV)
        self.assertFalse(next(x for x in service_types() if x['key']=='github')['discovery'])

    def test_run_survives_backend_expiry_and_dispatch_failure(self):
        from leakscope.services.runs import enqueue,get_run,write_state,serialize_run
        task=Mock();task.apply_async.side_effect=RuntimeError('fixture publish failure')
        run=enqueue(task,type='elastic',parameters={'keyword':'fixture'})
        self.assertEqual(run.state,'failed')
        task.apply_async.side_effect=None
        run=enqueue(task,type='elastic')
        write_state(run.task_id,'SUCCESS',{'total':1})
        with patch('leakscope.services.runs.AsyncResult',side_effect=RuntimeError('expired')):
            stored=get_run(run.id)
        self.assertEqual(stored.state,'succeeded')
        self.assertEqual(serialize_run(stored)['result']['total'],1)

    def test_run_failure_backend_metadata_is_decodable(self):
        from leakscope.services.runs import publish_state
        fake=Mock();fake.request.id=None
        publish_state(fake,state='FAILURE',meta={'error':'fixture error'})
        payload=fake.update_state.call_args.kwargs['meta']
        self.assertEqual(payload['exc_type'],'RuntimeError')
        self.assertEqual(payload['exc_message'],['fixture error'])

    def test_all_handler_failures_are_not_empty_success(self):
        client=Mock();client.host_search.return_value={'matches':[self.match],'total':1}
        with patch.object(tasks,'get_shodan_client',return_value=client),patch.object(tasks,'check_gitlab',side_effect=ValueError('fixture parse failure')),patch.object(tasks.check_main,'update_state') as state:
            with self.assertRaises(Ignore):tasks.check_main.run(self.search.pk,type='gitlab')
        self.assertEqual(state.call_args.kwargs['state'],'FAILURE')

    def test_completed_provider_page_survives_later_failure(self):
        client=Mock();client.host_search.side_effect=[{'matches':[dict(self.match,port=80)],'total':2},tasks.ShodanClientError('Fixture quota exhausted')]
        with patch.object(tasks,'get_shodan_client',return_value=client),patch.object(tasks.check_main,'update_state') as state:
            with self.assertRaises(Ignore):tasks.check_main.run(self.search.pk,type='gitlab',page_count=2)
        self.assertEqual(models.Gitlab.objects.count(),1)
        self.assertEqual(client.host_search.call_count,2)
        self.assertEqual(state.call_args.kwargs['state'],'SUCCESS')
        self.assertTrue(state.call_args.kwargs['meta']['partial'])
        self.assertIn('quota',state.call_args.kwargs['meta']['errors'][0])

    def test_custom_completed_provider_page_survives_later_failure(self):
        query=models.CustomQuery.objects.create(name='fixture',query_template='title:GitLab',default_type='gitlab')
        client=Mock();client.host_search.side_effect=[{'matches':[dict(self.match,port=80)],'total':2},tasks.ShodanClientError('Fixture quota exhausted')]
        with patch.object(tasks,'get_shodan_client',return_value=client),patch.object(tasks.check_custom_query,'update_state') as state:
            with self.assertRaises(Ignore):tasks.check_custom_query.run(query.pk,page_count=2)
        self.assertEqual(models.Gitlab.objects.count(),1)
        self.assertTrue(state.call_args.kwargs['meta']['partial'])
        query.refresh_from_db();self.assertEqual(query.last_status,'partial')

    def test_shodan_literals_escape_quotes_and_backslashes(self):
        query=tasks.build_shodan_query('title:GitLab',keyword='Acme "Systems"',exclude='folder\\name "fixture"')
        self.assertIn('"Acme \\"Systems\\""',query)
        self.assertIn('-"folder\\\\name \\"fixture\\""',query)

    def test_generic_custom_query_respects_blacklist(self):
        models.BlacklistEntry.objects.create(value=self.match['ip_str'])
        query=models.CustomQuery.objects.create(name='generic',query_template='fixture',default_type='custom')
        client=Mock();client.host_search.return_value={'matches':[self.match],'total':1}
        with patch.object(tasks,'get_shodan_client',return_value=client),patch.object(tasks.check_custom_query,'update_state') as state:
            with self.assertRaises(Ignore):tasks.check_custom_query.run(query.pk)
        result=state.call_args.kwargs['meta']['results']
        self.assertFalse(any(result.values()))
