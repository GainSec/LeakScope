import inspect
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from django.test import TestCase,override_settings
from leakscope import tasks
from leakscope_app import models
from leakscope.services.registry import service_types,model_for_type
from leakscope.services.runs import enqueue,get_run,publish_state,serialize_run
from leakscope.celery import app
from celery.exceptions import Ignore


class CoreContractTests(TestCase):
    def test_each_service_has_model_and_alias_contract(self):
        for spec in service_types():
            self.assertEqual(model_for_type(spec['key']),spec['model'])
            for alias in spec['aliases']:
                self.assertEqual(model_for_type(alias),spec['model'])
            self.assertTrue(spec['preview'])
            self.assertTrue(spec['export'])

    def test_public_worker_registration_survives_module_extraction(self):
        for name in ['check_main','check_custom_query','run_preview','export_preview_data','brute_buckets','javascript_search','github_repo_search','monitor','monitor_periodic','check_mongo','check_dir','check_angular','check_elastic']:
            task=getattr(tasks,name)
            self.assertEqual(task.name,'leakscope.tasks.'+name)
            self.assertIs(task.app,app)

    def test_implementations_live_in_responsibility_modules(self):
        from leakscope.handlers import discovery
        from leakscope.previews import services
        from leakscope.exporters import service
        from leakscope.providers import discovery as providers
        self.assertIs(tasks.check_gitlab,discovery.check_gitlab)
        self.assertIs(tasks._preview_mongo,services._preview_mongo)
        self.assertIs(tasks.export_preview_data,service.export_preview_data)
        self.assertIs(tasks.get_shodan_client,providers.get_shodan_client)

    def test_export_aliases_reach_real_exporter(self):
        search=models.Search.objects.create(type='registry',keyword='',country='',network='')
        response=Mock(status_code=200,ok=True,headers={});response.json.return_value={'repositories':[]}
        for kind,model in [('nexus',models.Nexus),('artifactory',models.Artifactory),('harbor',models.Registry),('dockerapi',models.Registry)]:
            response.json.return_value=[] if kind in ('nexus','artifactory') else {'repositories':[]}
            obj=model.objects.create(search=search,ip='192.0.2.1',port='80',url='http://192.0.2.1')
            with patch.object(tasks.export_preview_data,'update_state') as state,patch('leakscope.exporters.service.requests.get',return_value=response),patch('leakscope.exporters.service._write_export_file',return_value={'token':'a'*32,'path':'fixture.json'}):
                with self.assertRaises(Ignore):tasks.export_preview_data.run(type=kind,target_id=obj.pk,active_probe=True)
                self.assertEqual(state.call_args.kwargs['state'],'SUCCESS')
            obj.delete()

    def test_terminal_run_not_downgraded_by_late_progress(self):
        task=Mock()
        run=enqueue(task,type='elastic')
        fake=Mock();fake.request.id=run.task_id
        publish_state(fake,state='SUCCESS',meta={'total':2})
        publish_state(fake,state='PROGRESS',meta={'current':1,'total':2})
        self.assertEqual(get_run(run.pk,refresh=False).state,'succeeded')

    def test_stale_backend_pending_becomes_lost(self):
        from datetime import timedelta
        from django.utils import timezone
        run=enqueue(Mock(),type='elastic')
        models.Run.objects.filter(pk=run.pk).update(created_at=timezone.now()-timedelta(hours=2))
        result=Mock();result.backend.get_task_meta.return_value={'status':'PENDING','result':None}
        with patch('leakscope.services.runs.AsyncResult',return_value=result),override_settings(LEAKSCOPE_RUN_TIMEOUT=60):
            self.assertEqual(get_run(run.pk).state,'lost')

    def test_seen_count_preserves_legacy_hidden_status(self):
        fp=tasks._fingerprint('api_key','192.0.2.1',80)
        models.FingerprintLog.objects.create(fingerprint=fp,type='keys',ip='192.0.2.1',port='80',status='hidden')
        tasks.record_fingerprint('keys','192.0.2.1',80)
        obj=models.FingerprintLog.objects.get(fingerprint=fp)
        self.assertEqual(obj.status,'hidden')
        self.assertEqual(obj.seen_count,2)

    def test_js_fetch_uses_timeout_and_bounded_stream(self):
        from leakscope.handlers.discovery import send_request
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.iter_content.return_value=[b'const fixture = true;']
        with patch('leakscope.handlers.discovery.requests.get',return_value=response) as get:
            self.assertIn('fixture',send_request('https://example.test/fixture.js'))
            self.assertEqual(get.call_args.kwargs['timeout'],(5,15))
            self.assertTrue(get.call_args.kwargs['stream'])

    def test_parser_does_not_write_unrequested_artifacts(self):
        from leakscope.handlers.discovery import parser_file
        with patch('builtins.open') as opened:
            result=parser_file('var x = "/fixture/path";',r'"([^\"]+)"','https://example.test/test.js')
            self.assertTrue(result)
            opened.assert_not_called()

    def test_atomic_export_and_expiry_cleanup_leave_other_files(self):
        import os,time
        from leakscope.exporters.storage import write_export_file,cleanup_expired_exports
        with tempfile.TemporaryDirectory() as d,override_settings(LEAKSCOPE_EXPORT_DIR=d,LEAKSCOPE_EXPORT_TTL_SECONDS=60):
            result=write_export_file([{'fixture':True}])
            path=Path(result['path'])
            self.assertEqual(json.loads(path.read_text()),[{'fixture':True}])
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            other=Path(d)/'owner-notes.txt';other.write_text('retain')
            os.utime(path,(time.time()-120,time.time()-120))
            self.assertEqual(cleanup_expired_exports(),1)
            self.assertTrue(other.exists())
            self.assertFalse(list(Path(d).glob('*.tmp')))

    def test_stale_started_backend_becomes_lost(self):
        from datetime import timedelta
        from django.utils import timezone
        run=enqueue(Mock(),type='elastic')
        models.Run.objects.filter(pk=run.pk).update(state='running',created_at=timezone.now()-timedelta(hours=2),started_at=timezone.now()-timedelta(hours=2))
        result=Mock();result.backend.get_task_meta.return_value={'status':'STARTED','result':None}
        with patch('leakscope.services.runs.AsyncResult',return_value=result),override_settings(LEAKSCOPE_RUN_TIMEOUT=60):
            self.assertEqual(get_run(run.pk).state,'lost')

    def test_discovery_matrix_supplies_keyword_active_and_product_hint(self):
        from leakscope.services.registry import dispatch_discovery
        cases=[('elastic','check_elastic',{'keyword':'fixture','active_probe':True}),('gogs','check_gitea',{'active_probe':True,'product_hint':'gogs'}),('api_key','check_keys',{}),('europe','check_amazonbe',{})]
        for kind,name,expected in cases:
            with patch.object(tasks,name,return_value={'fixture':True}) as handler:
                dispatch_discovery(kind,0,{},Mock(),{'config':{}},keyword='fixture',active_probe=True)
                self.assertEqual(handler.call_args.kwargs,dict(config={'config':{}},**expected))

    def test_preview_matrix_selects_actual_protocol_and_metadata(self):
        from leakscope.services.registry import preview_for_type
        obj=Mock(ip='192.0.2.1',port='80',url='http://192.0.2.1',title='fixture')
        cases=[('harbor','_preview_registry',('http://192.0.2.1',)),('consulkv','_preview_consul',('192.0.2.1','80')),('github','_preview_github',(obj,))]
        for kind,name,args in cases:
            with patch.object(tasks,name,return_value={'summary':'fixture','items':[]}) as handler:
                preview_for_type(kind,obj)
                handler.assert_called_once_with(*args)
        with patch.object(tasks,'_preview_mongo') as mongo:
            self.assertIn('metadata',preview_for_type('mongoexpress',obj)['summary'])
            mongo.assert_not_called()

    def test_capabilities_distinguish_live_and_metadata_operations(self):
        specs={spec['key']:spec for spec in service_types()}
        self.assertEqual(specs['mongo']['export_mode'],'live')
        self.assertEqual(specs['mongoexpress']['export_mode'],'metadata')
        self.assertEqual(specs['keys']['preview_mode'],'metadata')
        self.assertEqual(specs['registry']['preview_mode'],'live')

    def test_csv_exports_escape_formulas_in_all_shapes_json_unchanged(self):
        import csv,io
        from leakscope.exporters.storage import write_export_file
        values=['=HYPERLINK("https://example.test")','+SUM(1,2)','-2+3','@fixture','\t=cmd','\r=cmd','plain']
        with tempfile.TemporaryDirectory() as d,override_settings(LEAKSCOPE_EXPORT_DIR=d):
            for data in [[{'value':value} for value in values],values]:
                info=write_export_file(data,'csv')
                rows=list(csv.reader(io.StringIO(Path(info['path']).read_bytes().decode('utf-8'))))
                self.assertEqual([row[0] for row in rows[1:]], ["'"+value if value[0] in '=+-@\t\r' else value for value in values])
                info=write_export_file(data,'json')
                self.assertEqual(json.loads(Path(info['path']).read_text()),data)
            info=write_export_file([{'=header':'plain'}],'csv')
            self.assertEqual(next(csv.reader(io.StringIO(Path(info['path']).read_bytes().decode('utf-8'))))[0],"'=header")

    def test_failed_live_export_has_actionable_error_and_no_artifact(self):
        search=models.Search.objects.create(type='elastic',keyword='',country='',network='')
        obj=models.Elastic.objects.create(search=search,ip='192.0.2.1',port='9200')
        response=Mock(status_code=500,ok=False)
        with tempfile.TemporaryDirectory() as d,override_settings(LEAKSCOPE_EXPORT_DIR=d),patch.object(tasks.export_preview_data,'update_state') as state,patch('leakscope.exporters.service.requests.get',return_value=response):
            with self.assertRaises(Ignore):
                tasks.export_preview_data.run(type='elastic',target_id=obj.pk,active_probe=True)
            self.assertEqual(state.call_args.kwargs['state'],'FAILURE')
            meta=state.call_args.kwargs['meta']
            self.assertIn('HTTP 500',meta['exc_message'][0])
            self.assertNotIn('download_token',meta)
            self.assertEqual(list(Path(d).iterdir()),[])

    def test_mixed_live_export_preserves_data_and_reports_partial_errors(self):
        search=models.Search.objects.create(type='registry',keyword='',country='',network='')
        obj=models.Registry.objects.create(search=search,ip='192.0.2.1',port='80',url='http://192.0.2.1')
        def response(payload=None,status=200):
            value=Mock(status_code=status,ok=status==200,headers={})
            value.json.return_value=payload
            return value
        responses=[response(),response({'repositories':['good','bad']}),response({'tags':['latest']}),response(status=500)]
        with tempfile.TemporaryDirectory() as d,override_settings(LEAKSCOPE_EXPORT_DIR=d),patch.object(tasks.export_preview_data,'update_state') as state,patch('leakscope.exporters.service.requests.get',side_effect=responses):
            with self.assertRaises(Ignore):
                tasks.export_preview_data.run(type='registry',target_id=obj.pk,active_probe=True)
            self.assertEqual(state.call_args.kwargs['state'],'SUCCESS')
            meta=state.call_args.kwargs['meta']
            self.assertTrue(meta['partial'])
            self.assertEqual(meta['data_count'],1)
            self.assertEqual(meta['error_count'],1)
            self.assertIn('HTTP 500',meta['errors'][0])
            payload=json.loads(Path(meta['filename']).read_text())
            self.assertEqual(payload[0],{'repo':'good','tags':['latest']})
            self.assertEqual(payload[1],{'repo':'bad','error':'HTTP 500'})

    def test_document_error_field_is_data_not_an_export_failure(self):
        search=models.Search.objects.create(type='elastic',keyword='',country='',network='')
        obj=models.Elastic.objects.create(search=search,ip='192.0.2.1',port='9200')
        indices=Mock(ok=True);indices.json.return_value=[{'index':'logs'}]
        documents=Mock(ok=True);documents.json.return_value={'hits':{'hits':[{'_source':{'error':'business log entry'}}]}}
        with patch.object(tasks.export_preview_data,'update_state') as state,patch('leakscope.exporters.service.requests.get',side_effect=[indices,documents]),patch('leakscope.exporters.service._write_export_file',return_value={'token':'a'*32,'path':'fixture.json'}) as write:
            with self.assertRaises(Ignore):
                tasks.export_preview_data.run(type='elastic',target_id=obj.pk,active_probe=True)
            self.assertEqual(state.call_args.kwargs['state'],'SUCCESS')
            self.assertFalse(state.call_args.kwargs['meta'].get('partial',False))
            write.assert_called_once_with([{'error':'business log entry'}],'json')

    def test_live_http_failures_are_not_empty_successful_exports(self):
        search=models.Search.objects.create(type='elastic',keyword='',country='',network='')
        response=Mock(status_code=500,ok=False)
        response.json.return_value=[]
        cases=[('nexus',models.Nexus,{}),('artifactory',models.Artifactory,{}),('solr',models.Solr,{}),('amazonbuckets',models.AmazonBuckets,{'bucket':'fixture.example.test'})]
        for kind,Model,extra in cases:
            with self.subTest(type=kind):
                names={field.name for field in Model._meta.fields}
                values={key:value for key,value in {'search':search,'ip':'192.0.2.1','port':'80',**extra}.items() if key in names}
                obj=Model.objects.create(**values)
                with patch.object(tasks.export_preview_data,'update_state') as state,patch('leakscope.exporters.service.requests.get',return_value=response),patch('leakscope.exporters.service._write_export_file') as write:
                    with self.assertRaises(Ignore):
                        tasks.export_preview_data.run(type=kind,target_id=obj.pk,active_probe=True)
                    self.assertEqual(state.call_args.kwargs['state'],'FAILURE')
                    self.assertIn('HTTP 500',state.call_args.kwargs['meta']['exc_message'][0])
                    write.assert_not_called()
