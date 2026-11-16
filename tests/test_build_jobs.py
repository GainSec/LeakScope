import json
from unittest.mock import patch
from leakscope.build import engine
from leakscope.build.blocks.base import BlockError, Done, Waiting, get
from leakscope.build.kinds import endpoint_items
from leakscope.phase3.jobs import search_progress_key, scoped_query
from leakscope_app.models import Run, SavedLookup, SearchProgress, TagAssignment
from leakscope.phase3.state import ingest_observation
from tests.test_build_blocks import observe, CTX
from tests.test_build_engine import EngineCase, step


def finished(kind='lookup', state='succeeded', error='', **result):
    return Run.objects.create(kind=kind, state=state, error=error, result=json.dumps(result))


class SearchBlockTests(EngineCase):
    def begin(self, allowance=None, **settings):
        block = get('search')
        cleaned = block.clean({'provider': 'zoomeye', 'query': 'app="x"', **settings})
        child = Run.objects.create(kind='lookup', state='queued')
        with patch('leakscope.build.blocks.find.queue_lookup', return_value=child) as queue:
            outcome = block.begin(CTX, [], cleaned, allowance or {'pages': 5, 'contacts': 25})
        return block, cleaned, outcome, queue, child

    def test_search_creates_a_passive_lookup_and_waits(self):
        block, cleaned, outcome, queue, child = self.begin(pages=2, page_size=50)
        lookup = SavedLookup.objects.get()
        self.assertEqual(queue.call_args.args[0], lookup)
        self.assertEqual((lookup.name, lookup.provider, lookup.query, lookup.scope, lookup.collection_mode, lookup.auto_fetch, lookup.actions, lookup.enabled),
                         ('Workflow · Test', 'zoomeye', 'app="x"', {}, 'passive', False, [], False))
        self.assertEqual((lookup.page_count, lookup.start_page, lookup.page_size, lookup.max_results), (2, 1, 50, 100))
        self.assertIsInstance(outcome, Waiting)
        self.assertEqual(outcome.run_ids, [str(child.pk)])
        self.assertEqual(block.cost(cleaned, 0).pages, 2)
        self.assertEqual(block.cost(cleaned, 0).items_out, 100)
        self.assertEqual(block.cost(block.clean({'provider': 'shodan', 'query': 'q'}), 0).items_out, 100)

    def test_continue_starts_after_earlier_pages_and_beginning_ignores_them(self):
        key = search_progress_key('zoomeye', scoped_query('zoomeye', 'app="x"', {}), 20)
        progress = SearchProgress.objects.create(key=key, provider='zoomeye', page_size=20, next_page=4)
        self.begin()
        self.assertEqual(SavedLookup.objects.get().start_page, 4)
        SavedLookup.objects.all().delete()
        self.begin(start='beginning')
        self.assertEqual(SavedLookup.objects.get().start_page, 1)
        SavedLookup.objects.all().delete()
        progress.exhausted = True; progress.save()
        _, _, outcome, queue, _ = self.begin()
        self.assertIsInstance(outcome, Done)
        self.assertEqual((outcome.items, outcome.note), ([], 'Earlier runs already fetched every result for this search'))
        queue.assert_not_called()
        self.assertEqual(SavedLookup.objects.count(), 0)

    def test_every_run_reuses_one_lookup_so_its_daily_budget_accumulates(self):
        self.begin(pages=1)
        first = SavedLookup.objects.get()
        _, _, _, queue, _ = self.begin(pages=3)
        self.assertEqual(SavedLookup.objects.count(), 1)
        self.assertEqual((queue.call_args.args[0].pk, SavedLookup.objects.get().page_count), (first.pk, 3))
        self.begin(query='app="y"')
        self.assertEqual(SavedLookup.objects.count(), 2)

    def test_a_deleted_endpoint_is_counted_not_fatal(self):
        from django.core.exceptions import ObjectDoesNotExist
        items, _ = endpoint_items([str(observe(1).pk)])
        block = get('http_fingerprint')
        with patch('leakscope.build.blocks.collect.queue_capture', side_effect=ObjectDoesNotExist()):
            outcome = block.begin(CTX, items, block.clean({}), {'pages': 5, 'contacts': 5})
        self.assertIn('1 not queued', outcome.note)

    def test_search_respects_the_page_allowance(self):
        block, cleaned, outcome, _, child = self.begin(allowance={'pages': 2, 'contacts': 25}, pages=5)
        self.assertEqual(SavedLookup.objects.get().page_count, 2)
        Run.objects.filter(pk=child.pk).update(state='succeeded', result=json.dumps({'endpoint_ids': [], 'pages': 2, 'new_results': 0, 'repeat_results': 0}))
        done = block.resume(CTX, [], cleaned, outcome.state, {str(child.pk): Run.objects.get(pk=child.pk)})
        self.assertEqual((done.pages, done.left_out), (2, 1))
        self.assertIn('2 of 5 pages', done.note)

    def test_a_skipped_lookup_is_reported_not_waited_for(self):
        block = get('search')
        skipped = Run.objects.create(kind='lookup', state='skipped', error='Daily lookup job budget reached (UTC day)')
        with patch('leakscope.build.blocks.find.queue_lookup', return_value=skipped):
            outcome = block.begin(CTX, [], block.clean({'provider': 'netlas', 'query': 'q'}), {'pages': 5, 'contacts': 25})
        self.assertEqual((outcome.items, outcome.note), ([], 'Daily lookup job budget reached (UTC day)'))
        self.assertEqual(SavedLookup.objects.get().page_size, 20)

    def test_search_resume_returns_new_and_repeat_counts(self):
        a, b = observe(1), observe(2)
        block = get('search')
        child = finished(endpoint_ids=[str(a.pk), str(b.pk)], pages=1, new_results=1, repeat_results=1)
        done = block.resume(CTX, [], block.clean({'provider': 'zoomeye', 'query': 'q'}), {'requested_pages': 1, 'queued': {str(child.pk): 1}}, {str(child.pk): child})
        self.assertEqual([item['id'] for item in done.items], [str(a.pk), str(b.pk)])
        self.assertEqual((done.pages, done.note, done.left_out), (1, '1 new · 1 captured before', 0))
        failed = finished(state='failed', error='ZoomEye returned no usable observations')
        with self.assertRaisesMessage(BlockError, 'ZoomEye returned no usable observations'):
            block.resume(CTX, [], {}, {'requested_pages': 1, 'queued': {str(failed.pk): 1}}, {str(failed.pk): failed})


class CaptureBlockTests(EngineCase):
    def test_capture_queues_one_job_per_item_up_to_the_allowance(self):
        endpoints = [observe(n) for n in range(1, 6)]
        items, _ = endpoint_items([str(e.pk) for e in endpoints])
        block = get('http_fingerprint')
        cleaned = block.clean({})
        children = [Run.objects.create(kind='capture', state='queued'), None, Run.objects.create(kind='capture', state='queued')]
        with patch('leakscope.build.blocks.collect.queue_capture', side_effect=children) as queue:
            outcome = block.begin(CTX, items, cleaned, {'pages': 5, 'contacts': 3})
        self.assertEqual(queue.call_count, 3)
        self.assertEqual(queue.call_args_list[0].args, (items[0]['id'], ['httpx']))
        self.assertEqual(queue.call_args_list[0].kwargs, {'origin': 'workflow:w1', 'cooldown_seconds': 3600, 'max_daily': 1000})
        self.assertEqual(outcome.run_ids, [str(children[0].pk), str(children[2].pk)])
        self.assertEqual((outcome.state['not_queued'], outcome.state['left_out']), (1, 2))
        self.assertEqual(block.cost(cleaned, 7).contacts, 7)
        with self.assertRaises(ValueError): block.clean({'action': 'nmap'})
        self.assertEqual({key: get(key).action for key in ('http_fingerprint', 'fetch_response', 'screenshot', 'protocol_identity', 'port_check')},
                         {'http_fingerprint': 'httpx', 'fetch_response': 'http', 'screenshot': 'screenshot', 'protocol_identity': 'identity', 'port_check': 'check'})

    def test_nothing_queued_finishes_at_once(self):
        items, _ = endpoint_items([str(observe(1).pk)])
        block = get('http_fingerprint')
        with patch('leakscope.build.blocks.collect.queue_capture', side_effect=ValueError('unavailable')):
            outcome = block.begin(CTX, items, block.clean({}), {'pages': 5, 'contacts': 5})
        self.assertIsInstance(outcome, Done)
        self.assertEqual((outcome.items, outcome.contacts), ([], 0))
        self.assertIn('1 not queued', outcome.note)

    def test_capture_resume_keeps_only_successful_captures(self):
        a, b = observe(1), observe(2)
        good, bad = finished('capture'), finished('capture', state='failed', error='timeout')
        block = get('http_fingerprint')
        state = {'endpoints': {str(good.pk): str(a.pk), str(bad.pk): str(b.pk)}, 'not_queued': 1, 'left_out': 2}
        done = block.resume(CTX, [], block.clean({}), state, {str(good.pk): good, str(bad.pk): bad})
        self.assertEqual([item['id'] for item in done.items], [str(a.pk)])
        self.assertEqual((done.contacts, done.left_out), (2, 2))
        self.assertEqual(done.note, '1 captured · 1 not queued (cooldown, budget or unsupported) · 1 failed')


class WorkflowJobTests(EngineCase):
    DEFINITION = {'steps': [step('a', 'run_by_hand'), step('b', 'search', provider='zoomeye', query='app="x"'), step('c', 'keep_new'),
                            step('d', 'http_fingerprint'), step('e', 'tag', tags=['seen'])]}

    def test_full_workflow_search_keep_new_capture_tag(self):
        fresh, other, repeat = observe(1), observe(2), observe(3)
        observe(3)
        lookup_run = Run.objects.create(kind='lookup', state='queued')
        captures = [Run.objects.create(kind='capture', state='queued') for _ in range(2)]
        with patch('leakscope.build.blocks.find.queue_lookup', return_value=lookup_run), patch('leakscope.build.blocks.collect.queue_capture', side_effect=captures) as capture:
            _, run = self.launch(self.DEFINITION)
            self.assertEqual(self.slice(run)['result']['steps'][1]['state'], 'waiting')
            Run.objects.filter(pk=lookup_run.pk).update(state='succeeded', result=json.dumps(
                {'endpoint_ids': [str(fresh.pk), str(other.pk), str(repeat.pk)], 'pages': 1, 'new_results': 2, 'repeat_results': 1}))
            self.assertEqual(self.slice(run)['result']['steps'][3]['state'], 'waiting')
            self.assertEqual(capture.call_count, 2)
            Run.objects.filter(pk__in=[c.pk for c in captures]).update(state='succeeded')
            data = self.slice(run)
        result = data['result']
        self.assertEqual(data['state'], 'succeeded')
        self.assertEqual(result['spent'], {'pages': 1, 'contacts': 2})
        self.assertEqual([(row['id'], row['state'], row['items_in'], row['items_out']) for row in result['steps']],
                         [('a', 'done', 0, 1), ('b', 'done', 1, 3), ('c', 'done', 3, 2), ('d', 'done', 2, 2), ('e', 'done', 2, 2)])
        self.assertFalse(result['partial'])
        self.assertEqual(TagAssignment.objects.filter(tag__name='seen', origin__startswith='workflow:').count(), 2)

    def test_contact_ceiling_reached_exactly_marks_partial(self):
        endpoints = [observe(n) for n in range(1, 4)]
        definition = {'steps': [step('a', 'saved_results'), step('b', 'port_check'), step('c', 'http_fingerprint')], 'limits': {'contacts': 2}}
        captures = [Run.objects.create(kind='capture', state='succeeded') for _ in range(2)]
        with patch('leakscope.build.blocks.collect.queue_capture', side_effect=captures) as capture:
            _, run = self.launch(definition)
            self.slice(run)
            result = self.slice(run)['result']
        self.assertEqual(capture.call_count, 2)
        self.assertEqual([row['state'] for row in result['steps']], ['done', 'done', 'skipped'])
        self.assertEqual((result['steps'][1]['left_out'], result['spent']['contacts'], result['partial']), (1, 2, True))
        self.assertEqual(result['stopped_at']['step'], 'b')

    def test_estimate_counts_pages_and_contacts(self):
        numbers = engine.estimate({'steps': [step('a', 'run_by_hand'), step('b', 'search', provider='zoomeye', query='q', pages=3, page_size=10), step('d', 'http_fingerprint')]})
        self.assertEqual((numbers['pages'], numbers['contacts'], numbers['items']), (3, 25, 30))
        self.assertEqual(numbers['steps'][2]['contacts'], 30)


class PerResultJobTests(EngineCase):
    def items(self, *endpoints):
        return endpoint_items([str(e.pk) for e in endpoints])[0]

    def test_netlas_enrichment_spends_one_page_per_result_within_the_allowance(self):
        items = self.items(observe(1), observe(2), observe(3))
        block = get('netlas_enrich')
        runs = [Run.objects.create(kind='enrich', state='queued') for _ in range(2)]
        with patch('leakscope.services.runs.enqueue', side_effect=runs) as queue:
            outcome = block.begin(CTX, items, block.clean({}), {'pages': 2, 'contacts': 25})
        self.assertEqual(queue.call_count, 2)
        self.assertEqual(queue.call_args.kwargs['kwargs'], {'endpoint_id': items[1]['id'], 'collections': ['host', 'dns', 'certificates']})
        self.assertEqual((outcome.state['left_out'], block.cost(block.clean({}), 3).pages), (1, 3))
        Run.objects.filter(pk=runs[0].pk).update(state='succeeded'); Run.objects.filter(pk=runs[1].pk).update(state='failed')
        done = block.resume(CTX, items, {}, outcome.state, {str(r.pk): Run.objects.get(pk=r.pk) for r in runs})
        self.assertEqual(([i['id'] for i in done.items], done.pages, done.left_out, done.note), ([items[0]['id']], 2, 1, '1 enriched · 1 failed'))

    def test_public_lookups_reuse_saved_evidence_and_skip_results_that_do_not_apply(self):
        ip, name = observe(1), ingest_observation('zoomeye', {'address': 'login.example.com', 'port': 443, 'scheme': 'https', 'service_type': 'nginx', 'metadata': {}})
        items = self.items(ip, name)
        child = Run.objects.create(kind='asset_enrichment', state='succeeded')
        with patch('leakscope.phase3.asset_jobs.queue_enrichment', return_value={'cached': True, 'result': {}}) as queue:
            done = get('network_owner').begin(CTX, items, {}, {'pages': 5, 'contacts': 25})
        self.assertEqual(queue.call_args.args, ('netblock', '192.0.2.1'))
        self.assertEqual(([i['id'] for i in done.items], done.note, done.pages, done.contacts), ([str(ip.pk)], '1 looked up (1 already saved) · 1 could not run', 0, 0))
        with patch('leakscope.phase3.asset_jobs.queue_enrichment', return_value={'cached': False, 'run': {'id': str(child.pk)}}) as queue:
            waiting = get('dns_records').begin(CTX, items, {}, {'pages': 5, 'contacts': 25})
        self.assertEqual(queue.call_args.args, ('dns', 'login.example.com'))
        self.assertEqual(waiting.run_ids, [str(child.pk)])
        done = get('dns_records').resume(CTX, items, {}, waiting.state, {str(child.pk): child})
        self.assertEqual([i['id'] for i in done.items], [str(name.pk)])
        with patch('leakscope.phase3.asset_jobs.queue_enrichment', side_effect=ValueError('bad target')):
            self.assertIn('1 could not run', get('names_for_address').begin(CTX, items[:1], {}, {'pages': 5, 'contacts': 25}).note)
        self.assertEqual({get(key).reach for key in ('names_for_address', 'network_owner', 'dns_records', 'domain_registration')}, {'public'})

    def test_related_services_passes_on_what_the_provider_found(self):
        seed, found = observe(1), observe(9)
        items = self.items(seed)
        block = get('related_services')
        child = Run.objects.create(kind='related_services', state='succeeded', result=json.dumps({'endpoint_ids': [str(found.pk)], 'stored': 1}))
        with patch('leakscope.phase3.ics_jobs.queue_related', return_value=child) as queue:
            waiting = block.begin(CTX, items, block.clean({'provider': 'shodan'}), {'pages': 5, 'contacts': 25})
        self.assertEqual((queue.call_args.args[1:], queue.call_args.args[0].pk), (('shodan', 'passive', 100), seed.pk))
        done = block.resume(CTX, items, {}, waiting.state, {str(child.pk): child})
        self.assertEqual(([i['id'] for i in done.items], done.pages), ([str(found.pk)], 1))
        self.assertEqual(block.cost(block.clean({'provider': 'shodan', 'max_results': 10}), 4), type(block.cost(block.clean({'provider': 'shodan'}), 1))(pages=4, items_out=40))


class ValuesAndGuardsTests(EngineCase):
    def test_a_query_that_uses_a_value_runs_once_per_distinct_result_within_the_page_ceiling(self):
        hosts = [ingest_observation('zoomeye', {'address': name, 'port': 443, 'scheme': 'https', 'service_type': 'nginx', 'metadata': {}}) for name in ('a.example.com', 'b.example.com')]
        items = endpoint_items([str(h.pk) for h in hosts] + [str(observe(1).pk)])[0] + endpoint_items([str(hosts[0].pk)])[0]
        block = get('search')
        cleaned = block.clean({'provider': 'zoomeye', 'query': 'hostname="{{hostname}}"', 'start': 'beginning'})
        self.assertEqual(block.cost(cleaned, 4).pages, 4)
        children = [Run.objects.create(kind='lookup', state='queued') for _ in range(2)]
        with patch('leakscope.build.blocks.find.queue_lookup', side_effect=children) as queue:
            outcome = block.begin(CTX, items, cleaned, {'pages': 5, 'contacts': 25})
        self.assertEqual(sorted(SavedLookup.objects.values_list('query', flat=True)), ['hostname="a.example.com"', 'hostname="b.example.com"'])
        self.assertEqual((queue.call_count, outcome.state['unusable'], outcome.state['skipped']), (2, 1, 0))
        found = observe(9)
        Run.objects.filter(pk__in=[c.pk for c in children]).update(state='succeeded', result=json.dumps({'endpoint_ids': [str(found.pk)], 'pages': 1, 'new_results': 1, 'repeat_results': 0}))
        done = block.resume(CTX, items, cleaned, outcome.state, {str(c.pk): Run.objects.get(pk=c.pk) for c in children})
        self.assertEqual(([i['id'] for i in done.items], done.pages, done.left_out), ([str(found.pk)], 2, 0))
        self.assertIn('1 results had no value for the query', done.note)
        SavedLookup.objects.all().delete()
        with patch('leakscope.build.blocks.find.queue_lookup', side_effect=[Run.objects.create(kind='lookup', state='queued')]):
            limited = block.begin(CTX, items, cleaned, {'pages': 1, 'contacts': 25})
        self.assertEqual((len(limited.run_ids), limited.state['skipped']), (1, 1))

    def test_take_a_value_set_a_value_and_tags_built_from_values(self):
        from leakscope.build.kinds import refresh
        a = ingest_observation('zoomeye', {'address': 'login.example.com', 'port': 443, 'scheme': 'https', 'service_type': 'nginx', 'title': 'Sign in v2.4', 'metadata': {}})
        items = endpoint_items([str(a.pk), str(observe(1).pk)])[0]
        take = get('take_value')
        done = take.begin(CTX, items, take.clean({'name': 'version', 'source': 'title', 'pattern': r'v(\d+\.\d+)'}), {})
        self.assertEqual(([i['fields']['version'] for i in done.items], done.note), (['2.4'], '1 named version · 1 had nothing to take'))
        kept = take.begin(CTX, items, take.clean({'name': 'host', 'source': 'hostname', 'missing': 'keep'}), {})
        self.assertEqual([i['fields']['host'] for i in kept.items], ['login.example.com', ''])
        setter = get('set_value')
        urls = setter.begin(CTX, kept.items, setter.clean({'name': 'url', 'text': 'https://{{host}}/login'}), {})
        self.assertEqual(([i['fields']['url'] for i in urls.items], urls.note), (['https://login.example.com/login'], '1 set · 1 dropped (a value it uses was empty)'))
        self.assertEqual(refresh(urls.items)[0][0]['fields']['url'], 'https://login.example.com/login')
        tag = get('tag')
        tag.begin(CTX, done.items, tag.clean({'tags': ['v-{{version}}', 'checked']}), {})
        self.assertEqual(sorted(TagAssignment.objects.filter(target_key=str(a.pk)).values_list('tag__name', flat=True)), ['checked', 'v-2.4'])
        for bad in ({'name': 'port', 'source': 'title'}, {'name': 'Bad Name', 'source': 'title'}, {'name': 'x', 'source': 'title', 'pattern': '(a+)+'}, {'name': 'x'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): take.clean(bad)
        self.assertEqual((take.adds(take.clean({'name': 'url', 'source': 'title'})), take.group), (['url'], 'data'))

    def test_values_are_checked_against_what_exists_at_each_step(self):
        ok = {'steps': [step('a', 'saved_results'), step('b', 'take_value', name='host', source='hostname'), step('c', 'search', provider='zoomeye', query='hostname="{{host}}"'),
                        step('d', 'tag', tags=['t-{{technology}}'])]}
        self.assertEqual(engine.estimate(ok)['steps'][2]['pages'], 100)
        cases = [({'steps': [step('a', 'saved_results'), step('b', 'tag', tags=['{{host}}'])]}, 'Step 2: {{host}} is not a value available at this step'),
                 ({'steps': [step('a', 'run_by_hand'), step('b', 'search', provider='zoomeye', query='x {{hostname}}')]}, 'Step 2: {{hostname}} is not a value available'),
                 ({'steps': [step('a', 'saved_results', search='{{ip}}')]}, 'Step 1: Address or title contains cannot use values'),
                 ({'steps': [step('a', 'saved_results'), step('b', 'take_value', name='host', source='hostname'), step('c', 'search', provider='zoomeye', query='q'), step('d', 'tag', tags=['{{host}}'])]},
                  'Step 4: {{host}} is not a value available'),
                 ({'steps': [step('a', 'saved_results'), {**step('b', 'search', provider='zoomeye', query='q'), 'when': {'rules': [{'field': 'port', 'op': 'is', 'value': '443'}]}}]}, 'cannot be limited to some results'),
                 ({'steps': [step('a', 'saved_results'), {**step('b', 'tag', tags=['x']), 'when': {'rules': []}}]}, 'Step 2: Only when: Conditions take 1–10 rules')]
        for definition, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesMessage(ValueError, expected): engine.validate(definition)

    def test_only_when_runs_a_step_for_matching_results_and_the_rest_pass_through(self):
        web, other = observe(1), ingest_observation('zoomeye', {'address': '192.0.2.2', 'port': 22, 'transport': 'tcp', 'service_type': 'ssh', 'metadata': {}})
        web_only = {'rules': [{'field': 'scheme', 'op': 'is', 'value': 'http'}]}
        definition = {'steps': [step('a', 'saved_results'), {**step('b', 'screenshot'), 'when': web_only},
                                {**step('c', 'protocol_identity'), 'when': {'rules': [{'field': 'scheme', 'op': 'empty', 'value': ''}]}}, step('d', 'tag', tags=['done'])]}
        shot, ident = Run.objects.create(kind='capture', state='queued'), Run.objects.create(kind='capture', state='succeeded')
        with patch('leakscope.build.blocks.collect.queue_capture', side_effect=[shot, ident]) as capture:
            _, run = self.launch(definition)
            waiting = self.slice(run)['result']['steps'][1]
            self.assertEqual((waiting['state'], waiting['items_in'], waiting['passed_by']), ('waiting', 1, 1))
            self.assertEqual(capture.call_args_list[0].args, (str(web.pk), ['screenshot']))
            Run.objects.filter(pk=shot.pk).update(state='succeeded')
            self.slice(run)
            result = self.slice(run)['result']
        self.assertEqual(capture.call_args_list[1].args, (str(other.pk), ['identity']))
        self.assertEqual([(row['id'], row['items_in'], row['passed_by'], row['items_out']) for row in result['steps']], [('a', 0, 0, 2), ('b', 1, 1, 2), ('c', 1, 1, 2), ('d', 2, 0, 2)])
        self.assertEqual(TagAssignment.objects.filter(tag__name='done').count(), 2)


class DomainBlockTests(EngineCase):
    def candidates(self, *rows):
        from leakscope_app.models import DomainCandidate, DomainExpansion
        expansion = DomainExpansion.objects.create(run=Run.objects.create(kind='domain_expansion', state='succeeded'), scope_key='k')
        for name, status, confidence in rows:
            DomainCandidate.objects.create(expansion=expansion, domain=name, root='example.com', status=status, confidence=confidence)
        return expansion

    def test_saved_domains_and_certificate_names_start_with_domains(self):
        from django.utils import timezone
        from leakscope_app.models import CertificateRecord, CertificateName, CertificateWatchMatch
        self.candidates(('login.example.com', 'accepted', 90), ('old.example.com', 'rejected', 20))
        self.candidates(('login.example.com', 'accepted', 90))
        block = get('saved_domains')
        run = lambda **settings: block.begin(CTX, [], block.clean(settings), {})
        self.assertEqual(sorted(item['id'] for item in run().items), ['login.example.com', 'old.example.com'])
        only = run(status='accepted').items
        self.assertEqual([(i['kind'], i['id'], i['fields']['root'], i['fields']['confidence']) for i in only], [('domain', 'login.example.com', 'example.com', 90)])
        self.assertEqual([i['id'] for i in run(conditions={'rules': [{'field': 'confidence', 'op': 'lt', 'value': '50'}]}).items], ['old.example.com'])
        watched, plain = CertificateRecord.objects.create(key='a', issuer={'O': 'Example CA'}), CertificateRecord.objects.create(key='b')
        for record, names in ((watched, ['sso.example.com', '*.example.com']), (plain, ['other.example.net'])):
            for name in names: CertificateName.objects.create(certificate=record, name=name)
        CertificateWatchMatch.objects.create(certificate=watched, watch_key='w', watch_name='Acme')
        certs = get('certificate_names')
        found = certs.begin(CTX, [], certs.clean({}), {}).items
        self.assertEqual([(i['id'], i['fields']['source'], i['fields']['issuer']) for i in found], [('sso.example.com', 'certificate', 'Example CA')])
        self.assertEqual(sorted(i['id'] for i in certs.begin(CTX, [], certs.clean({'matches': 'all'}), {}).items), ['other.example.net', 'sso.example.com'])

    def test_results_and_domains_convert_both_ways(self):
        host = ingest_observation('zoomeye', {'address': 'login.example.com', 'port': 443, 'scheme': 'https', 'service_type': 'nginx', 'metadata': {}})
        sub = ingest_observation('zoomeye', {'address': 'a.login.example.com', 'port': 443, 'scheme': 'https', 'service_type': 'nginx', 'metadata': {}})
        items = endpoint_items([str(host.pk), str(observe(1).pk)])[0]
        names = get('names_of_results').begin(CTX, items, {}, {})
        self.assertEqual(([i['id'] for i in names.items], names.note), (['login.example.com'], '1 names from 2 results'))
        back = get('results_for_domains')
        self.assertEqual([i['id'] for i in back.begin(CTX, names.items, back.clean({}), {}).items], [str(host.pk)])
        self.assertEqual(sorted(i['id'] for i in back.begin(CTX, names.items, back.clean({'subdomains': 'subdomains'}), {}).items), sorted([str(host.pk), str(sub.pk)]))

    def test_domains_flow_through_filters_values_lookups_watches_and_validation(self):
        from leakscope.build.kinds import domain_item
        from leakscope_app.models import WatchRule
        domains = [domain_item('login.example.com', confidence=90), domain_item('old.example.com', confidence=10)]
        kept = get('filter').begin(CTX, domains, get('filter').clean({'conditions': {'rules': [{'field': 'confidence', 'op': 'gt', 'value': '50'}]}}), {})
        self.assertEqual([i['id'] for i in kept.items], ['login.example.com'])
        with patch('leakscope.phase3.asset_jobs.queue_enrichment', return_value={'cached': True, 'result': {}}) as queue:
            done = get('dns_records').begin(CTX, domains, {}, {'pages': 5, 'contacts': 25})
        self.assertEqual((queue.call_args_list[0].args, [i['id'] for i in done.items]), (('dns', 'login.example.com'), ['login.example.com', 'old.example.com']))
        watch = get('create_watch')
        self.assertEqual(watch.begin(CTX, domains, watch.clean({'name': 'Acme names'}), {}).note, 'Created watch “Acme names” with 2 domains')
        self.assertEqual(watch.begin(CTX, domains[:1], watch.clean({'name': 'Acme names', 'enabled': 'no'}), {}).note, 'Updated watch “Acme names” with 1 domains')
        row = WatchRule.objects.get()
        self.assertEqual((row.domains, row.enabled, row.auto_fetch), (['login.example.com'], False, False))
        ok = {'steps': [step('a', 'saved_domains'), step('b', 'filter', conditions={'rules': [{'field': 'status', 'op': 'is', 'value': 'accepted'}]}),
                        step('c', 'search', provider='zoomeye', query='hostname="{{domain}}"'), step('d', 'names_of_results'), step('e', 'create_watch', name='W')]}
        self.assertEqual(len(engine.validate(ok)['steps']), 5)
        for definition, expected in [({'steps': [step('a', 'saved_domains'), step('b', 'tag', tags=['x'])]}, 'Step 2: Tag needs endpoint items, but Saved domains that match gives domain'),
                                     ({'steps': [step('a', 'saved_results'), step('b', 'create_watch', name='W')]}, 'needs domain items'),
                                     ({'steps': [step('a', 'saved_domains'), step('b', 'search', provider='zoomeye', query='{{hostname}}')]}, '{{hostname}} is not a value available'),
                                     ({'steps': [step('a', 'saved_domains'), step('b', 'take_value', name='root', source='domain')]}, 'already a field')]:
            with self.subTest(expected=expected), self.assertRaisesMessage(ValueError, expected): engine.validate(definition)

    def test_expand_domains_waits_for_the_expansion_and_passes_on_its_candidates(self):
        from leakscope.build.kinds import domain_item
        expansion = self.candidates(('new.example.com', 'pending', 80), ('weak.example.com', 'pending', 30))
        block = get('expand_domains')
        with patch('leakscope.phase3.domain_expansion.start', return_value=expansion) as start:
            waiting = block.begin(CTX, [domain_item('example.com')], block.clean({}), {})
        from leakscope.phase3.domain_expansion import DEFAULT_SOURCES
        self.assertEqual((start.call_args.args[0], waiting.run_ids), ({'domains': ['example.com'], 'sources': list(DEFAULT_SOURCES), 'budgets': {'web': 0, 'netlas': 0}}, [str(expansion.run_id)]))
        done = block.resume(CTX, [], block.clean({'keep': 'strong'}), waiting.state, {str(expansion.run_id): expansion.run})
        self.assertEqual(([i['id'] for i in done.items], done.note), (['new.example.com'], '1 strong of 2 candidates from 1 domains'))
        expansion.run.state = 'paused'
        self.assertIn('stopped at its own budget', block.resume(CTX, [], block.clean({}), waiting.state, {str(expansion.run_id): expansion.run}).note)
        self.assertEqual(get('expand_domains').reach, 'public')


class OutputAndFindBlockTests(EngineCase):
    def items(self, count=2):
        return endpoint_items([str(observe(n).pk) for n in range(1, count + 1)])[0]

    def test_web_request_refuses_internal_addresses_unless_allowed_and_never_follows_redirects(self):
        block = get('web_request')
        items = self.items()
        reply = type('Reply', (), {'status_code': 202, 'raw': type('Raw', (), {'read': lambda self, size, decode_content=True: b'{"ok":true}'})(),
                                   '__enter__': lambda self: self, '__exit__': lambda self, *a: False})()
        with patch('requests.request', return_value=reply) as request:
            refused = block.begin(CTX, items, block.clean({'url': 'http://127.0.0.1:8080/hook'}), {})
            self.assertEqual((request.call_count, refused.note), (0, '0 sent · 1 refused (internal or not http)'))
            self.assertEqual({i['fields']['response_status'] for i in refused.items}, {'refused: internal address'})
            allowed = block.begin(CTX, items, block.clean({'url': 'http://127.0.0.1:8080/hook', 'allow_internal': True, 'headers': 'X-Token: abc\nAccept: */*', 'body': 'hi'}), {})
        self.assertEqual((request.call_count, allowed.note), (1, '1 sent'))
        self.assertEqual(request.call_args.args, ('POST', 'http://127.0.0.1:8080/hook'))
        self.assertEqual((request.call_args.kwargs['headers'], request.call_args.kwargs['data'], request.call_args.kwargs['allow_redirects'], request.call_args.kwargs['timeout']),
                         ({'X-Token': 'abc', 'Accept': '*/*'}, b'hi', False, 15))
        self.assertEqual([(i['fields']['response_status'], i['fields']['response_body']) for i in allowed.items], [('202', '{"ok":true}')] * 2)
        with patch('requests.request', return_value=reply) as request, patch('leakscope.build.blocks.send._internal', return_value=False):
            each = block.begin(CTX, items + items[:1], block.clean({'method': 'GET', 'url': 'https://hooks.example.com/?ip={{ip}}', 'maximum': 1}), {})
        self.assertEqual((request.call_count, each.note), (1, '1 sent · 1 not sent (empty value or over the step limit)'))
        self.assertEqual([i['fields'].get('response_status') for i in each.items], ['202', None, '202'])
        for bad in ({'url': 'ftp://example.com/'}, {'url': 'https://example.com/', 'headers': 'no colon'}, {'url': 'https://example.com/', 'allow_internal': 'yes'}, {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): block.clean(bad)
        self.assertEqual((block.reach, block.adds(block.clean({'url': 'https://example.com/'}))), ('send', ['response_status', 'response_body']))
        ok = {'steps': [step('a', 'saved_results'), step('b', 'web_request', url='https://hooks.example.com/{{ip}}'), step('c', 'filter', conditions={'rules': [{'field': 'response_status', 'op': 'is', 'value': '200'}]}),
                        step('d', 'tag', tags=['hook-{{response_status}}'])]}
        self.assertEqual(len(engine.validate(ok)['steps']), 4)

    def test_export_location_sort_and_duplicates(self):
        from leakscope.build.blocks.base import BlockError
        items = self.items(3)
        with patch('leakscope.exporters.storage.write_export_file', return_value={'token': 'a' * 32, 'path': '/x'}) as write:
            done = get('export_file').begin(CTX, items, get('export_file').clean({}), {})
        self.assertEqual((write.call_args.args[1], write.call_args.args[0][0]['address'], done.note), ('csv', items[0]['fields']['address'], '3 rows · download: /export/download/' + 'a' * 32))
        with self.assertRaises(KeyError): get('send_email')
        named = [{**items[0], 'fields': {**items[0]['fields'], 'title': 'a | b', 'url': 'https://x.example/'}}] + items[1:]
        with patch('leakscope.exporters.storage.write_export_text', return_value={'token': 'b' * 32, 'path': '/x'}) as text:
            get('export_file').begin(CTX, named, get('export_file').clean({'format': 'md'}), {})
            get('export_file').begin(CTX, named, get('export_file').clean({'format': 'txt'}), {})
        table, kind = text.call_args_list[0].args
        self.assertEqual((kind, table.splitlines()[:6]), ('md', ['# Test', '', '3 items', '', '| address | port | protocol | technology | title | tags | last_seen | url |', '| --- | --- | --- | --- | --- | --- | --- | --- |']))
        self.assertIn('| 192.0.2.1 | 8080 | http | jenkins | a \\| b |', table)
        self.assertIn('https://x.example/', table)
        self.assertEqual((text.call_args_list[1].args[1], text.call_args_list[1].args[0].splitlines()[1]), ('txt', '192.0.2.2:8080\tjenkins\tHost 2'))
        with self.assertRaises(ValueError): get('export_file').clean({'format': 'xlsx'})
        with patch('leakscope.phase3.geography.lookup_ips', return_value={'192.0.2.1': {'country_name': 'Exampleland', 'country_code': 'EX', 'city': 'Sample'}}):
            located = get('location').begin(CTX, items, {}, {})
        self.assertEqual(([i['fields']['country_code'] for i in located.items], located.note), (['EX', '', ''], '1 located · 2 not in the local database'))
        ordered = get('sort').begin(CTX, located.items, get('sort').clean({'by': 'address', 'order': 'desc'}), {})
        self.assertEqual([i['fields']['address'] for i in ordered.items], ['192.0.2.3', '192.0.2.2', '192.0.2.1'])
        unique = get('remove_duplicates').begin(CTX, located.items, get('remove_duplicates').clean({'by': 'country_code'}), {})
        self.assertEqual(([i['fields']['address'] for i in unique.items], unique.note), (['192.0.2.1', '192.0.2.2'], '2 kept · 1 duplicates dropped'))

    def test_run_a_saved_lookup_and_search_for_a_service(self):
        from leakscope.build.blocks.base import BlockError
        lookup = SavedLookup.objects.create(name='Jenkins sweep', provider='shodan', query='product:jenkins', page_count=3, max_results=300)
        block = get('run_saved_lookup')
        settings = block.clean({'lookup': str(lookup.pk)})
        self.assertEqual((block.cost(settings, 0).pages, block.cost(settings, 0).items_out), (3, 300))
        short = block.begin(CTX, [], settings, {'pages': 2, 'contacts': 25})
        self.assertEqual((short.left_out, short.note), (1, 'Not run: it fetches 3 pages and 2 are left in this run'))
        child = Run.objects.create(kind='lookup', state='queued')
        with patch('leakscope.build.blocks.find.queue_lookup', return_value=child) as queue:
            waiting = block.begin(CTX, [], settings, {'pages': 5, 'contacts': 25})
        self.assertEqual((queue.call_args.args[0], waiting.run_ids), (lookup, [str(child.pk)]))
        with self.assertRaisesMessage(BlockError, 'no longer exists'): block.begin(CTX, [], block.clean({'lookup': '00000000-0000-0000-0000-000000000000'}), {'pages': 5, 'contacts': 25})
        service = get('search_service')
        built = {'supported': True, 'base_query': 'http.body="cluster_uuid"', 'scope': {'country': 'US'}, 'base_type': 'elastic'}
        with patch('leakscope_app.workbench._discovery_query', return_value=built) as query, patch('leakscope.build.blocks.find.queue_lookup', return_value=Run.objects.create(kind='lookup', state='queued')):
            service.begin(CTX, [], service.clean({'service': 'elastic', 'provider': 'zoomeye', 'country': 'US'}), {'pages': 5, 'contacts': 25})
        self.assertEqual(query.call_args.args[:2], ('elastic', 'zoomeye'))
        made = SavedLookup.objects.exclude(pk=lookup.pk).get()
        self.assertEqual((made.query, made.scope, made.service_type, made.collection_mode), ('http.body="cluster_uuid"', {'country': 'US'}, 'elastic', 'passive'))
        with patch('leakscope_app.workbench._discovery_query', return_value={'supported': False, 'reason': 'No verified query'}), self.assertRaisesMessage(BlockError, 'No verified query'):
            service.begin(CTX, [], service.clean({'service': 'elastic', 'provider': 'netlas'}), {'pages': 5, 'contacts': 25})


class ExportStorageTests(EngineCase):
    def test_markdown_and_text_exports_download_and_expire_like_the_others(self):
        import os, tempfile, time
        from django.test import override_settings
        from leakscope.exporters.storage import write_export_text, cleanup_expired_exports
        from leakscope.build.kinds import domain_item
        with tempfile.TemporaryDirectory() as directory, override_settings(LEAKSCOPE_EXPORT_DIR=directory):
            done = get('export_file').begin(CTX, [domain_item('login.example.com', source='certificate', status='accepted')], get('export_file').clean({'format': 'txt'}), {})
            token = done.note.rsplit('/', 1)[1]
            response = self.client.get('/export/download/' + token)
            self.assertEqual((response.status_code, response['Content-Type'], b''.join(response.streaming_content)), (200, 'text/plain; charset=utf-8', b'login.example.com\tcertificate\taccepted\n'))
            written = write_export_text('# x\n', 'md')
            self.assertEqual(oct(os.stat(written['path']).st_mode & 0o777), '0o600')
            self.assertEqual(self.client.get('/export/download/' + written['token'])['Content-Type'], 'text/markdown; charset=utf-8')
            os.utime(written['path'], (time.time() - 90000, time.time() - 90000))
            self.assertEqual(cleanup_expired_exports(), 1)
            with self.assertRaises(ValueError): write_export_text('x', 'exe')
