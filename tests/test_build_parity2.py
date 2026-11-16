"""Blocks for Custom queries, Cloud discovery, the Industrial page, screenshot collections and domain review."""
import json
from unittest.mock import patch
from leakscope.build.blocks.base import BlockError, Cost, Waiting, get
from leakscope.build.kinds import domain_item, endpoint_items
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import (CloudResource, CustomQuery, DomainCandidate, DomainExpansion, EvidenceArtifact, Run, SavedLookup,
                                  ScreenshotCollection, ScreenshotCuration, ScreenshotMembership)
from tests.test_build_blocks import observe, CTX
from tests.test_build_engine import EngineCase

ALLOW = {'pages': 5, 'contacts': 25}


def items_of(*endpoints):
    return endpoint_items([str(row.pk) for row in endpoints])[0]


class CustomQueryBlockTests(EngineCase):
    def test_runs_the_saved_template_through_one_passive_lookup(self):
        saved = CustomQuery.objects.create(name='Open panels', query_template='title:"{keyword}" country:{country}', provider='shodan', default_type='jenkins')
        block = get('run_custom_query')
        cleaned = block.clean({'query': str(saved.pk), 'keyword': 'admin', 'country': 'NL', 'pages': 2, 'start': 'beginning'})
        self.assertEqual(block.cost(cleaned, 1).pages, 2)
        child = Run.objects.create(kind='lookup', state='queued')
        with patch('leakscope.build.blocks.find.queue_lookup', return_value=child) as queue:
            outcome = block.begin(CTX, [], cleaned, ALLOW)
        self.assertIsInstance(outcome, Waiting)
        lookup = SavedLookup.objects.get()
        self.assertEqual(queue.call_args.args[0], lookup)
        self.assertEqual((lookup.provider, lookup.query, lookup.service_type, lookup.collection_mode, lookup.page_count, lookup.enabled),
                         ('shodan', 'title:"admin" country:NL', 'jenkins', 'passive', 2, False))
        self.assertEqual(lookup.scope, {'country': 'NL', 'network': '', 'exclude': ''})
        self.assertEqual((block.reach, block.group), ('credits', 'find'))

    def test_provider_can_be_changed_and_a_keyword_value_runs_once_per_result(self):
        saved = CustomQuery.objects.create(name='By host', query_template='hostname:{keyword}', provider='shodan')
        hosts = [ingest_observation('zoomeye', {'address': name, 'port': 443, 'scheme': 'https', 'service_type': 'nginx', 'metadata': {}}) for name in ('a.example.com', 'b.example.com')]
        block = get('run_custom_query')
        cleaned = block.clean({'query': str(saved.pk), 'provider': 'zoomeye', 'keyword': '{{hostname}}', 'start': 'beginning'})
        self.assertEqual(block.cost(cleaned, 2).pages, 2)
        children = [Run.objects.create(kind='lookup', state='queued') for _ in range(2)]
        with patch('leakscope.build.blocks.find.queue_lookup', side_effect=children):
            outcome = block.begin(CTX, items_of(*hosts), cleaned, ALLOW)
        self.assertEqual(len(outcome.run_ids), 2)
        self.assertEqual(sorted(SavedLookup.objects.values_list('provider', 'query')), [('zoomeye', 'hostname:a.example.com'), ('zoomeye', 'hostname:b.example.com')])

    def test_a_deleted_query_is_a_clear_failure(self):
        block = get('run_custom_query')
        with self.assertRaisesMessage(BlockError, 'no longer exists'): block.begin(CTX, [], block.clean({'query': '999'}), ALLOW)


class FindSimilarBlockTests(EngineCase):
    def test_searches_for_the_same_favicon_once_per_distinct_value(self):
        def host(last, icon):
            return ingest_observation('shodan', {'address': '192.0.2.%d' % last, 'port': 443, 'scheme': 'https', 'service_type': 'nginx',
                                                 'metadata': {'http': {'favicon': {'hash': icon}}} if icon else {}})
        rows = [host(1, -1234567), host(2, -1234567), host(3, 7654321), host(4, None)]
        block = get('find_similar')
        cleaned = block.clean({'match': 'favicon', 'provider': 'shodan', 'country': 'NL', 'start': 'beginning'})
        self.assertEqual((block.takes, block.reach, block.cost(cleaned, 4).pages), ('endpoint', 'credits', 4))
        children = [Run.objects.create(kind='lookup', state='queued') for _ in range(2)]
        with patch('leakscope.build.blocks.find.queue_lookup', side_effect=children):
            outcome = block.begin(CTX, items_of(*rows), cleaned, ALLOW)
        self.assertEqual(sorted(SavedLookup.objects.values_list('query', flat=True)),
                         ['http.favicon.hash:"-1234567" country:"NL"', 'http.favicon.hash:"7654321" country:"NL"'])
        self.assertEqual((len(outcome.run_ids), outcome.state['unusable']), (2, 1))

    def test_nothing_to_match_on_finishes_with_a_note(self):
        block = get('find_similar')
        done = block.begin(CTX, items_of(observe(1)), block.clean({'match': 'certificate', 'provider': 'netlas'}), ALLOW)
        self.assertEqual(done.items, [])
        self.assertIn('1 results had no value for the query', done.note)


class CloudBlockTests(EngineCase):
    def test_names_and_incoming_domains_are_checked_within_the_contact_ceiling(self):
        block = get('cloud_discovery')
        cleaned = block.clean({'names': ['Acme'], 'provider': 'aws', 'max_requests': 40})
        self.assertEqual((block.reach, block.gives), ('target', 'endpoint'))
        self.assertEqual(block.cost(cleaned, 1), Cost(contacts=40, items_out=40))
        child = Run.objects.create(kind='cloud', state='queued')
        incoming = [domain_item('acme.example', source='test'), domain_item('bad_name.example', source='test')]
        with patch('leakscope.build.blocks.cloud.queue_cloud', return_value=child) as queue:
            outcome = block.begin(CTX, incoming, cleaned, {'pages': 5, 'contacts': 3})
        spec = queue.call_args.args[0]
        self.assertEqual((spec['names'], spec['providers'], spec['kinds'], spec['max_requests'], spec['permutations']), (['acme', 'acme.example'], ['aws'], ['storage'], 3, False))
        self.assertEqual((outcome.run_ids, outcome.state['unusable']), ([str(child.pk)], 1))

    def test_resume_passes_on_resources_that_answered(self):
        block = get('cloud_discovery')
        open_host, denied_host = observe(1, technology='amazons3be'), observe(2, technology='amazons3be')
        public = CloudResource.objects.create(url='https://acme.s3.amazonaws.com/', provider='aws', name='acme', kind='bucket', state='public', endpoint=open_host)
        denied = CloudResource.objects.create(url='https://acme-dev.s3.amazonaws.com/', provider='aws', name='acme-dev', kind='bucket', state='access_denied', endpoint=denied_host)
        child = Run.objects.create(kind='cloud', state='succeeded', result=json.dumps({'attempted': 7, 'resource_ids': [str(public.pk), str(denied.pk)]}))
        state = {'queued': {str(child.pk): 7}, 'left_out': 0, 'unusable': 0}
        done = block.resume(CTX, [], block.clean({'names': ['acme']}), state, {str(child.pk): child})
        self.assertEqual((sorted(i['id'] for i in done.items), done.contacts), (sorted([str(open_host.pk), str(denied_host.pk)]), 7))
        self.assertIn('7 checks', done.note)
        only = block.resume(CTX, [], block.clean({'names': ['acme'], 'pass_on': 'public'}), state, {str(child.pk): child})
        self.assertEqual([i['id'] for i in only.items], [str(open_host.pk)])
        failed = Run.objects.create(kind='cloud', state='failed', error='boom')
        with self.assertRaisesMessage(BlockError, 'boom'): block.resume(CTX, [], block.clean({'names': ['acme']}), {'queued': {str(failed.pk): 1}}, {str(failed.pk): failed})

    def test_nothing_to_check_and_no_room(self):
        block = get('cloud_discovery')
        self.assertEqual(block.begin(CTX, [], block.clean({}), ALLOW).note, 'No names to check')
        with patch('leakscope.build.blocks.cloud.queue_cloud') as queue:
            done = block.begin(CTX, [], block.clean({'names': ['acme']}), {'pages': 5, 'contacts': 0})
        self.assertEqual((queue.called, done.left_out), (False, 1))

    def test_saved_cloud_resources_start(self):
        host, other = observe(1, technology='amazons3be'), observe(2, technology='gcsbucket')
        CloudResource.objects.create(url='https://acme.s3.amazonaws.com/', provider='aws', name='acme', kind='bucket', state='public', endpoint=host)
        CloudResource.objects.create(url='https://storage.googleapis.com/acme/', provider='google', name='acme', kind='bucket', state='access_denied', endpoint=other)
        CloudResource.objects.create(url='https://gone.s3.amazonaws.com/', provider='aws', name='gone', kind='bucket', state='public')
        block = get('saved_cloud_resources')
        self.assertTrue(block.start)
        ids = lambda **settings: sorted(i['id'] for i in block.begin(CTX, [], block.clean(settings), ALLOW).items)
        self.assertEqual(ids(), sorted([str(host.pk), str(other.pk)]))
        self.assertEqual(ids(state='public'), [str(host.pk)])
        self.assertEqual(ids(provider='google'), [str(other.pk)])
        self.assertEqual(ids(search='nothing'), [])


class IndustrialBlockTests(EngineCase):
    def test_inspection_runs_per_result_within_contacts_and_skips_other_technologies(self):
        modbus, web = observe(1, technology='modbus'), observe(2, technology='jenkins')
        block = get('industrial_inspect')
        child = Run.objects.create(kind='ot_inspection', state='succeeded')
        with patch('leakscope.phase3.ics_jobs.queue_inspection', side_effect=[child, ValueError('Read-only point inspection supports Modbus TCP and BACnet/IP')]) as queue:
            waiting = block.begin(CTX, items_of(modbus, web), block.clean({'timeout_seconds': 5}), ALLOW)
        self.assertEqual((queue.call_args_list[0].args[0].pk, queue.call_args_list[0].args[1]), (modbus.pk, {'timeout_seconds': 5}))
        done = block.resume(CTX, items_of(modbus, web), {}, waiting.state, {str(child.pk): child})
        self.assertEqual(([i['id'] for i in done.items], done.contacts), ([str(modbus.pk)], 1))
        self.assertIn('1 could not run', done.note)
        self.assertEqual((block.reach, block.cost(block.clean({}), 3)), ('target', Cost(contacts=3)))

    def test_port_inventory_passes_on_found_services_or_the_scanned_results(self):
        seed, found = observe(1), observe(9)
        block = get('port_inventory')
        child = Run.objects.create(kind='port_inventory', state='succeeded', result=json.dumps({'endpoint_ids': [str(found.pk)]}))
        cleaned = block.clean({'ports': '80,443', 'rate_per_second': 10})
        with patch('leakscope.phase3.ics_jobs.queue_scan', return_value=child) as queue:
            waiting = block.begin(CTX, items_of(seed), cleaned, ALLOW)
        self.assertEqual(queue.call_args.args[1], {'ports': '80,443', 'rate_per_second': 10, 'max_seconds': 120})
        self.assertEqual([i['id'] for i in block.resume(CTX, items_of(seed), cleaned, waiting.state, {str(child.pk): child}).items], [str(found.pk)])
        same = block.clean({'pass_on': 'same'})
        with patch('leakscope.phase3.ics_jobs.queue_scan', return_value=child):
            waiting = block.begin(CTX, items_of(seed), same, ALLOW)
        self.assertEqual([i['id'] for i in block.resume(CTX, items_of(seed), same, waiting.state, {str(child.pk): child}).items], [str(seed.pk)])
        self.assertEqual(block.reach, 'target')

    def test_web_interface_check(self):
        seed = observe(1, technology='unknown')
        block = get('web_probe')
        child = Run.objects.create(kind='web_probe', state='succeeded')
        with patch('leakscope.phase3.ics_jobs.queue_web_probe', return_value=child) as queue:
            waiting = block.begin(CTX, items_of(seed), block.clean({'scheme': 'http'}), ALLOW)
        self.assertEqual((queue.call_args.args[0].pk, queue.call_args.args[1]), (seed.pk, False))
        self.assertEqual(len(block.resume(CTX, items_of(seed), {}, waiting.state, {str(child.pk): child}).items), 1)


class ScreenshotBlockTests(EngineCase):
    def shot(self, endpoint, name='a'):
        return EvidenceArtifact.objects.create(endpoint=endpoint, kind='screenshot', relative_path='shots/' + name + '.png', mime='image/png', sha256=name * 64)

    def test_has_a_screenshot_keeps_either_side(self):
        with_shot, without = observe(1), observe(2)
        self.shot(with_shot)
        block = get('has_screenshot')
        items = items_of(with_shot, without)
        self.assertEqual([i['id'] for i in block.begin(CTX, items, block.clean({}), ALLOW).items], [str(with_shot.pk)])
        self.assertEqual([i['id'] for i in block.begin(CTX, items, block.clean({'keep': 'without'}), ALLOW).items], [str(without.pk)])

    def test_add_to_collection_creates_it_once_and_can_keep(self):
        host, bare = observe(1), observe(2)
        self.shot(host, 'a'); newest = self.shot(host, 'b')
        block = get('add_to_collection')
        items = items_of(host, bare)
        done = block.begin(CTX, items, block.clean({'collection': 'Panels', 'keep': True}), ALLOW)
        collection = ScreenshotCollection.objects.get()
        self.assertEqual((collection.name, list(ScreenshotMembership.objects.values_list('artifact_id', flat=True))), ('Panels', [newest.pk]))
        self.assertTrue(ScreenshotCuration.objects.get(artifact=newest).pinned)
        self.assertEqual(len(done.items), 2)
        self.assertIn('1 screenshot added', done.note); self.assertIn('1 result has no screenshot', done.note)
        again = block.begin(CTX, items, block.clean({'collection': 'Panels', 'which': 'all'}), ALLOW)
        self.assertEqual((ScreenshotCollection.objects.count(), ScreenshotMembership.objects.count()), (1, 2))
        self.assertIn('1 screenshot added', again.note)
        self.assertEqual(block.reach, 'local')


class DomainReviewBlockTests(EngineCase):
    def test_reject_and_accept_through_the_expansion_review(self):
        expansion = DomainExpansion.objects.create(run=Run.objects.create(kind='domain_expansion', state='succeeded'), scope_key='k', spec={'domains': ['example.com']})
        candidate = DomainCandidate.objects.create(expansion=expansion, domain='shop.example.com', root='example.com')
        block = get('review_domains')
        items = [domain_item('shop.example.com', source='domain expansion', status='pending'), domain_item('other.example.net', source='certificate')]
        with patch('leakscope.phase3.domain_expansion.review') as review:
            done = block.begin(CTX, items, block.clean({'action': 'reject'}), ALLOW)
        self.assertEqual(review.call_args.args, (expansion.pk, {'action': 'reject', 'candidate_ids': [str(candidate.pk)]}))
        self.assertEqual(([i['fields']['status'] for i in done.items], len(done.items)), (['rejected', ''], 2))
        self.assertIn('1 rejected', done.note); self.assertIn('1 not found by domain expansion', done.note)
        with patch('leakscope.phase3.domain_expansion.review', side_effect=ValueError('Choose an organization to accept domains')):
            failed = block.begin(CTX, items[:1], block.clean({'action': 'accept'}), ALLOW)
        self.assertIn('1 could not be reviewed: Choose an organization to accept domains', failed.note)
        self.assertEqual((block.takes, block.gives, block.reach), ('domain', 'same', 'local'))


class CatalogTests(EngineCase):
    def test_new_blocks_are_offered_with_their_option_lists(self):
        CustomQuery.objects.create(name='Open panels', query_template='x', provider='shodan')
        ScreenshotCollection.objects.create(name='Panels')
        body = self.client.get('/api/build/blocks').json()
        keys = {row['key'] for row in body['blocks']}
        self.assertLessEqual({'find_similar', 'run_custom_query', 'cloud_discovery', 'saved_cloud_resources', 'industrial_inspect', 'port_inventory', 'web_probe',
                              'has_screenshot', 'add_to_collection', 'review_domains'}, keys)
        self.assertEqual(len(keys - {'probe'}), 66)
        self.assertEqual([name for _, name in body['options']['queries']], ['Open panels'])
        self.assertEqual(body['options']['collections'], ['Panels'])
