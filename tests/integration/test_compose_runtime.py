"""Opt-in checks against an isolated, already-started staging Compose project."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]

@unittest.skipUnless(os.environ.get('LEAKSCOPE_COMPOSE_TESTS') == '1', 'isolated Compose checks are opt-in')
class ComposeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config_path = os.environ.get('LEAKSCOPE_CONFIG_SOURCE')
        if not config_path:
            raise unittest.SkipTest('An explicit empty staging provider config is required')
        config = json.loads(Path(config_path).read_text()).get('config', {})
        if config.get('SHODAN_API_KEY') or config.get('ZOOMEYE_API_KEY'):
            raise unittest.SkipTest('Integration tests refuse provider credentials')

    def compose(self, *args):
        command = ['docker', 'compose', '-p', 'leakscope-staging', '-f', 'docker-compose.yml', '-f', 'deploy/compose.staging.yml', *args]
        result = subprocess.run(command, cwd=ROOT, env=os.environ, capture_output=True, text=True, check=True, timeout=60)
        return result.stdout

    def test_network_and_scheduler_scope(self):
        config=json.loads(self.compose('config','--format','json'))
        self.assertFalse(config['services']['redis'].get('ports'))
        self.assertEqual(config['services']['web']['ports'][0]['host_ip'],'127.0.0.1')
        self.assertEqual(str(config['services']['web']['ports'][0]['published']),'2629')
        self.assertNotIn('beat',config['services'])

    def test_nonroot_private_config_and_no_baked_secret(self):
        code='import os,json;from pathlib import Path;print(json.dumps({"uid":os.getuid(),"private_config":Path("/run/secrets/provider_config").is_file(),"baked_config":Path("/app/config.json").exists()}))'
        result=json.loads(self.compose('run','--rm','--no-deps','-T','web','python','-c',code))
        self.assertNotEqual(result['uid'],0)
        self.assertTrue(result['private_config'])
        self.assertFalse(result['baked_config'])

    def test_completed_migration(self):
        data=[json.loads(line) for line in self.compose('ps','-a','--format','json').splitlines() if line.strip()]
        migration=next(row for row in data if row['Service']=='migrate')
        self.assertEqual(migration['ExitCode'],0)

    def test_real_worker_export_and_failure_state(self):
        code = """
import os,json,time
os.environ.setdefault('DJANGO_SETTINGS_MODULE','leakscope.settings')
import django
django.setup()
from leakscope.celery import app,debug_task
from leakscope.services.runs import enqueue,get_run
from leakscope import tasks
from leakscope_app.models import Search,Keys
from urllib.request import urlopen

def complete(run):
    for _ in range(150):
        run=get_run(run.pk)
        if run.state in {'succeeded','failed','skipped','lost','cancelled'}:return run
        time.sleep(.1)
    raise RuntimeError('Worker deadline exceeded')

assert complete(enqueue(debug_task,kind='debug')).state=='succeeded'
search=Search.objects.create(type='keys',keyword='integration-fixture',provider='shodan')
key=Keys.objects.create(search=search,ip='192.0.2.242',port='443',title='integration-fixture')
run=complete(enqueue(tasks.export_preview_data,kind='export',type='keys',kwargs={'type':'keys','target_id':key.pk,'active_probe':True}))
assert run.state=='succeeded',run.error
token=json.loads(run.result)['download_token']
url='http://127.0.0.1:8000/export/download/'+token
with urlopen(url,timeout=5) as response:first=response.read()
with urlopen(url,timeout=5) as response:assert response.read()==first
assert json.loads(first)[0]['title']=='integration-fixture'
search=Search.objects.create(type='elastic',keyword='integration-fixture',provider='shodan')
run=complete(enqueue(tasks.check_main,kind='discover',type='elastic',kwargs={'fk':search.pk,'type':'elastic','provider':'shodan'}))
assert run.state=='failed'
meta=app.backend.get_task_meta(run.task_id)
assert meta['status']=='FAILURE' and isinstance(meta['result'],Exception)
print('WORKER_EXPORT_FAILURE_OK')
"""
        result=self.compose('exec','-T','web','python','-c',code)
        self.assertIn('WORKER_EXPORT_FAILURE_OK',result)
