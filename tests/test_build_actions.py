"""Remove tags, Stop if, Count and Wait."""
from datetime import timedelta
from unittest.mock import patch
from django.utils import timezone
from leakscope.build import engine
from leakscope.build.blocks.base import Waiting, get
from leakscope.build.kinds import domain_item, endpoint_items
from leakscope.phase3.state import set_tags, tag_names
from tests.test_build_blocks import observe, CTX
from tests.test_build_engine import EngineCase, step


class RemoveTagsTests(EngineCase):
    def test_named_tags_are_removed_whoever_added_them_and_others_stay(self):
        a, b = observe(1), observe(2)
        set_tags('endpoint', str(a.pk), ['review', 'keep'])
        set_tags('endpoint', str(a.pk), ['review'], origin='workflow:x:s3')
        set_tags('endpoint', str(b.pk), ['keep'])
        block = get('remove_tags')
        items = endpoint_items([str(a.pk), str(b.pk)])[0]
        done = block.begin(CTX, items, block.clean({'tags': ['Review']}), {})
        self.assertEqual((tag_names('endpoint', str(a.pk)), tag_names('endpoint', str(b.pk))), (['keep'], ['keep']))
        self.assertEqual(done.note, 'Removed from 1 of 2')
        self.assertEqual([item['fields']['tags'] for item in done.items], [['keep'], ['keep']])
        self.assertEqual((block.takes, block.gives, block.reach, block.group), ('endpoint', 'endpoint', 'local', 'organize'))


class StopIfTests(EngineCase):
    def test_it_passes_everything_or_nothing_and_says_why(self):
        block = get('stop_if')
        items = [domain_item(str(n) + '.example.com') for n in range(5)]
        over = block.begin(CTX, items, block.clean({'when': 'more_than', 'count': 3}), {})
        self.assertEqual((over.items, over.note), ([], 'Stopped: 5 is more than 3'))
        under = block.begin(CTX, items, block.clean({'when': 'fewer_than', 'count': 10}), {})
        self.assertEqual((under.items, under.note), ([], 'Stopped: 5 is fewer than 10'))
        fine = block.begin(CTX, items, block.clean({'when': 'more_than', 'count': 5}), {})
        self.assertEqual((len(fine.items), fine.note), (5, '5 passed on'))

    def test_later_steps_are_skipped_when_it_stops(self):
        for n in range(1, 4): observe(n)
        _, run = self.launch({'steps': [step('a', 'saved_results', limit=50), step('b', 'stop_if', when='more_than', count=2), step('c', 'tag', tags=['never'])]})
        result = self.slice(run)['result']
        self.assertEqual([(row['id'], row['state']) for row in result['steps']], [('a', 'done'), ('b', 'done'), ('c', 'skipped')])
        self.assertEqual(run.state, 'succeeded')


class CountTests(EngineCase):
    def test_counts_per_value_largest_first_and_lists_count_each_entry(self):
        block = get('count')
        items = [domain_item('a.example.com', status='accepted'), domain_item('b.example.com', status='accepted'), domain_item('c.example.net', status='pending'), domain_item('d.example.org')]
        done = block.begin(CTX, items, block.clean({'by': 'status'}), {})
        self.assertEqual([(i['kind'], i['id'], i['fields']) for i in done.items],
                         [('value', 'accepted', {'value': 'accepted', 'count': 2}), ('value', 'pending', {'value': 'pending', 'count': 1}), ('value', '(none)', {'value': '(none)', 'count': 1})])
        self.assertEqual(done.note, '3 values of status · accepted 2 · pending 1 · (none) 1')
        tagged = [{'kind': 'endpoint', 'id': str(n), 'fields': {'tags': tags}} for n, tags in enumerate((['a', 'b'], ['a'], []))]
        self.assertEqual([(i['id'], i['fields']['count']) for i in block.begin(CTX, tagged, block.clean({'by': 'tags'}), {}).items], [('a', 2), ('b', 1), ('(none)', 1)])
        self.assertEqual((block.gives, block.adds_fixed), ('value', ('value', 'count')))

    def test_counts_can_be_exported_and_nothing_else_fits_after(self):
        observe(1)
        ok = {'steps': [step('a', 'saved_results', limit=50), step('b', 'count', by='technology'), step('c', 'export_file', format='csv')]}
        engine.validate(ok)
        with self.assertRaises(ValueError) as caught: engine.validate({'steps': ok['steps'][:2] + [step('c', 'tag', tags=['x'])]})
        self.assertIn('Tag needs endpoint items, but Count gives value', str(caught.exception))
        with patch('leakscope.exporters.storage.write_export_text', return_value={'token': 't'}) as text:
            export = get('export_file')
            counted = [{'kind': 'value', 'id': 'nginx', 'fields': {'value': 'nginx', 'count': 4}}]
            export.begin(CTX, counted, export.clean({'format': 'txt'}), {})
            export.begin(CTX, counted, export.clean({'format': 'md'}), {})
        self.assertEqual(text.call_args_list[0].args, ('nginx\t4\n', 'txt'))
        self.assertIn('| value | count |\n| --- | --- |\n| nginx | 4 |', text.call_args_list[1].args[0])


class WaitTests(EngineCase):
    def test_wait_holds_the_results_until_the_time_has_passed(self):
        block = get('wait')
        items = [domain_item('a.example.com')]
        waiting = block.begin(CTX, items, block.clean({'minutes': 10}), {})
        self.assertIsInstance(waiting, Waiting)
        self.assertEqual(waiting.run_ids, [])
        self.assertIsInstance(block.resume(CTX, items, block.clean({'minutes': 10}), waiting.state, {}), Waiting)
        later = timezone.now() + timedelta(minutes=11)
        with patch('django.utils.timezone.now', return_value=later):
            done = block.resume(CTX, items, block.clean({'minutes': 10}), waiting.state, {})
        self.assertEqual((done.items, done.note), (items, 'Waited 10 minutes'))
        for bad in ({'minutes': 0}, {'minutes': 61}, {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): block.clean(bad)

    def test_a_run_waits_and_then_goes_on(self):
        observe(1)
        _, run = self.launch({'steps': [step('a', 'saved_results', limit=50), step('b', 'wait', minutes=5), step('c', 'tag', tags=['after'])]})
        first = self.slice(run)['result']
        self.assertEqual([row['state'] for row in first['steps']][:2], ['done', 'waiting'])
        self.assertEqual(self.slice(run)['result']['steps'][1]['state'], 'waiting')
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(minutes=6)):
            result = self.slice(run)['result']
        self.assertEqual([(row['state'], row.get('items_out')) for row in result['steps']], [('done', 1), ('done', 1), ('done', 1)])


class CoverageTests(EngineCase):
    def test_cloud_network_adds_the_provider_service_and_region_from_local_ranges(self):
        a, b = observe(1), observe(2)
        items = endpoint_items([str(a.pk), str(b.pk)])[0]
        first = items[0]['fields']['ip']
        rows = [{'ip': first, 'provider': 'aws', 'provider_label': 'Amazon Web Services', 'prefix': '192.0.2.0/24', 'services': ['EC2', 'AMAZON'], 'regions': ['us-east-1']}]
        block = get('cloud_network')
        with patch('leakscope.phase3.cloud_ranges.lookup', return_value=rows) as lookup:
            done = block.begin(CTX, items, block.clean({}), {})
        self.assertEqual(sorted(lookup.call_args.args[0]), sorted(i['fields']['ip'] for i in items))
        got = {i['fields']['ip']: (i['fields']['cloud_provider'], i['fields']['cloud_service'], i['fields']['cloud_region']) for i in done.items}
        self.assertEqual(got[first], ('Amazon Web Services', 'AMAZON, EC2', 'us-east-1'))
        self.assertEqual([value for ip, value in got.items() if ip != first], [('', '', '')])
        self.assertEqual(done.note, '1 in a published cloud range · 1 not')
        self.assertEqual((block.reach, block.adds_fixed), ('local', ('cloud_provider', 'cloud_service', 'cloud_region')))

    def test_has_dns_records_splits_domains_by_saved_dns_evidence(self):
        from leakscope.phase3 import relationships as store
        store.evidence(store.node('domain', 'known.example.com'), 'dns', {'type': 'A', 'values': ['192.0.2.1']})
        store.node('domain', 'bare.example.com')
        items = [domain_item(name) for name in ('known.example.com', 'bare.example.com', 'unsaved.example.com')]
        block = get('has_dns_records')
        have = block.begin(CTX, items, block.clean({'keep': 'with'}), {})
        self.assertEqual(([i['id'] for i in have.items], have.note), (['known.example.com'], '1 with saved DNS records · 2 without'))
        lack = block.begin(CTX, items, block.clean({'keep': 'without'}), {})
        self.assertEqual([i['id'] for i in lack.items], ['bare.example.com', 'unsaved.example.com'])
        self.assertEqual((block.takes, block.gives, block.reach), ('domain', 'domain', 'local'))

    def test_tenant_lookup_asks_once_per_domain_with_one_request(self):
        from leakscope_app.models import Run
        block = get('tenant_lookup')
        items = [domain_item('acme.example'), domain_item('shop.acme.example')]
        child = Run.objects.create(kind='asset_enrichment', state='succeeded')
        with patch('leakscope.phase3.asset_jobs.queue_enrichment', side_effect=[{'cached': True, 'result': {}}, {'cached': False, 'run': {'id': str(child.pk)}}]) as queue:
            waiting = block.begin(CTX, items, block.clean({}), {'pages': 5, 'contacts': 25})
        self.assertEqual([(call.args, call.kwargs) for call in queue.call_args_list],
                         [(('identity_metadata', 'acme.example'), {'max_requests': 1}), (('identity_metadata', 'shop.acme.example'), {'max_requests': 1})])
        done = block.resume(CTX, items, block.clean({}), waiting.state, {str(child.pk): child})
        self.assertEqual(sorted(i['id'] for i in done.items), ['acme.example', 'shop.acme.example'])
        self.assertIn('2 looked up (1 already saved)', done.note)
        self.assertEqual((block.reach, block.takes), ('public', ('endpoint', 'domain')))


class SharedEvidenceTests(EngineCase):
    def link(self, endpoint, kind, value):
        from leakscope_app.models import EvidenceCorrelation
        EvidenceCorrelation.objects.create(endpoint=endpoint, observation=endpoint.observations.first(), kind=kind, value=value)

    def test_it_passes_on_other_saved_results_that_share_saved_evidence(self):
        a, b, c, d = (observe(n) for n in range(1, 5))
        for endpoint in (a, b): self.link(endpoint, 'certificate.sha256', 'a' * 64)
        for endpoint in (a, c): self.link(endpoint, 'tracking.google_tag_manager', 'GTM-EXAMPLE1')
        self.link(d, 'certificate.sha256', 'b' * 64)
        block = get('shared_evidence')
        items = endpoint_items([str(a.pk)])[0]
        others = block.begin(CTX, items, block.clean({}), {})
        self.assertEqual(sorted(i['id'] for i in others.items), sorted([str(b.pk), str(c.pk)]))
        self.assertEqual(others.note, '2 saved results share evidence with 1 of 1')
        certs = block.begin(CTX, items, block.clean({'kind': 'certificate', 'include': 'yes'}), {})
        self.assertEqual(sorted(i['id'] for i in certs.items), sorted([str(a.pk), str(b.pk)]))
        self.assertEqual(block.begin(CTX, endpoint_items([str(d.pk)])[0], block.clean({}), {}).items, [])
        self.assertEqual((block.reach, block.group, block.cost(block.clean({'limit': 40}), 3).items_out), ('local', 'find', 40))
