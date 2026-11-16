"""Organization blocks: add results or domains (to a chosen or new organization), start from its domains, and discover."""
from unittest.mock import patch
from leakscope.build.blocks.base import BlockError, Waiting, get
from leakscope.build.kinds import domain_item, endpoint_items
from leakscope_app import relationships
from leakscope_app.models import AssetNode, Run
from tests.test_build_blocks import observe, CTX
from tests.test_build_engine import EngineCase

ALLOW = {'pages': 5, 'contacts': 25}


class OrganizationBlockTests(EngineCase):
    def test_results_can_go_to_a_new_organization_by_name(self):
        block = get('add_to_organization')
        items = endpoint_items([str(observe(1).pk)])[0]
        done = block.begin(CTX, items, block.clean({'new_name': 'Acme Corp'}), ALLOW)
        root = AssetNode.objects.get(kind='organization')
        self.assertEqual((root.value, done.note[:7]), ('Acme Corp', '1 added'))
        self.assertIn('already there', block.begin(CTX, items, block.clean({'new_name': 'Acme Corp'}), ALLOW).note)
        self.assertEqual(AssetNode.objects.filter(kind='organization').count(), 1)
        with self.assertRaisesMessage(ValueError, 'Choose an organization or name a new one'): block.clean({})

    def test_domains_are_added_and_can_start_a_workflow(self):
        root = relationships.store.node('organization', 'Acme Corp')
        block = get('add_domains_to_organization')
        items = [domain_item('acme.example', source='test'), domain_item('shop.acme.example', source='test'), domain_item('not a domain', source='test')]
        done = block.begin(CTX, items, block.clean({'organization': str(root.pk)}), ALLOW)
        self.assertEqual(len(done.items), 3)
        self.assertIn('2 added', done.note); self.assertIn('1 could not be added', done.note)
        self.assertIn('2 already there', block.begin(CTX, items[:2], block.clean({'organization': str(root.pk)}), ALLOW).note)
        self.assertEqual((block.takes, block.gives, block.reach), ('domain', 'same', 'local'))
        start = get('organization_domains')
        found = start.begin(CTX, [], start.clean({'organization': str(root.pk)}), ALLOW)
        self.assertEqual((sorted(i['id'] for i in found.items), start.start, start.gives), (['acme.example', 'shop.acme.example'], True, 'domain'))
        with self.assertRaisesMessage(BlockError, 'not active'): start.begin(CTX, [], start.clean({'organization': '00000000-0000-0000-0000-000000000000'}), ALLOW)

    def test_discovery_waits_for_the_job_or_reuses_saved_evidence(self):
        root = relationships.store.node('organization', 'Acme Corp', {'aliases': ['Acme']})
        block = get('discover_organization')
        cleaned = block.clean({'organization': str(root.pk)})
        child = Run.objects.create(kind='asset_enrichment', state='succeeded')
        with patch('leakscope.phase3.asset_jobs.queue_enrichment', return_value={'cached': False, 'run': {'id': str(child.pk)}}) as queue:
            waiting = block.begin(CTX, [], cleaned, ALLOW)
        self.assertIsInstance(waiting, Waiting)
        self.assertEqual((queue.call_args.args, queue.call_args.kwargs), (('organization', 'Acme Corp'), {'organization_id': str(root.pk), 'max_requests': 2, 'aliases': ['Acme'], 'include_netlas': False}))
        self.assertEqual(block.resume(CTX, [], cleaned, waiting.state, {str(child.pk): child}).note, 'Nothing found for Acme Corp')
        with patch('leakscope.phase3.asset_jobs.queue_enrichment', return_value={'cached': True}):
            self.assertIn('Still current', block.begin(CTX, [], cleaned, ALLOW).note)
        failed = Run.objects.create(kind='asset_enrichment', state='failed', error='registry down')
        with self.assertRaisesMessage(BlockError, 'registry down'): block.resume(CTX, [], cleaned, {}, {str(failed.pk): failed})
        self.assertEqual((block.reach, block.gives), ('public', 'same'))

    def test_discovery_says_what_it_saved_and_when_it_stopped_short(self):
        import json
        root = relationships.store.node('organization', 'Acme Corp')
        block = get('discover_organization')
        cleaned = block.clean({'organization': str(root.pk)})
        result = {'partial': True, 'networks': [{'cidr': '192.0.2.0/24', 'matched_organization': False}, {'cidr': '198.51.100.0/24', 'matched_organization': True}, {'cidr': '192.0.2.0/24'}],
                  'asns': [{'asn': 64500, 'matched_organization': False}], 'domains': [{'name': 'acme.example'}]}
        child = Run.objects.create(kind='asset_enrichment', state='succeeded', result=json.dumps(result))
        done = block.resume(CTX, [], cleaned, {'organization': 'Acme Corp'}, {str(child.pk): child})
        self.assertEqual(done.note, '2 networks, 1 ASN, 1 domain saved to Acme Corp as candidates · 1 registered under that exact name · stopped at its request budget, so this is not everything')
        none = Run.objects.create(kind='asset_enrichment', state='succeeded', result=json.dumps({'networks': [{'cidr': '192.0.2.0/24', 'matched_organization': False}]}))
        self.assertEqual(block.resume(CTX, [], cleaned, {'organization': 'Acme Corp'}, {str(none.pk): none}).note, '1 network saved to Acme Corp as candidates · none registered under that exact name')

    def test_discovery_can_also_ask_netlas_which_counts_as_credits(self):
        import json
        root = relationships.store.node('organization', 'Acme Corp')
        block = get('discover_organization')
        free, paid = block.clean({'organization': str(root.pk)}), block.clean({'organization': str(root.pk), 'include_netlas': True})
        self.assertEqual((block.reaches(free), block.cost(free, 1).pages, block.reaches(paid), block.cost(paid, 1).pages), (('public',), 0, ('public', 'credits'), 2))
        child = Run.objects.create(kind='asset_enrichment', state='succeeded', result=json.dumps({'netlas_requests': 2, 'domains': [{'domain': 'acme.example'}]}))
        with patch('leakscope.phase3.asset_jobs.queue_enrichment', return_value={'cached': False, 'run': {'id': str(child.pk)}}) as queue:
            waiting = block.begin(CTX, [], paid, {'pages': 1, 'contacts': 25})
            self.assertEqual((queue.call_args.kwargs['include_netlas'], queue.call_args.kwargs['max_requests']), (True, 1))
            spent = block.begin(CTX, [], paid, {'pages': 0, 'contacts': 25})
            self.assertEqual((queue.call_args.kwargs['include_netlas'], queue.call_args.kwargs['max_requests']), (False, 2))
        self.assertIn('Netlas left out: no pages left in this run', spent.state['skipped'])
        done = block.resume(CTX, [], paid, waiting.state, {str(child.pk): child})
        self.assertEqual((done.pages, done.note[:14]), (2, '1 domain saved'))


class ExpandFromOrganizationTests(EngineCase):
    def expansion(self, *rows):
        from leakscope_app.models import DomainCandidate, DomainExpansion
        expansion = DomainExpansion.objects.create(run=Run.objects.create(kind='domain_expansion', state='succeeded'), scope_key='k')
        for name, confidence in rows: DomainCandidate.objects.create(expansion=expansion, domain=name, root='acme.example', confidence=confidence)
        return expansion

    def test_it_starts_from_an_organization_without_any_domains_and_never_searches_the_web_unasked(self):
        from leakscope.phase3.domain_expansion import DEFAULT_SOURCES
        root = relationships.store.node('organization', 'Acme Corp')
        block = get('expand_domains')
        expansion = self.expansion(('www.acme.example', 90), ('old.acme.example', 20))
        with patch('leakscope.phase3.domain_expansion.start', return_value=expansion) as start:
            waiting = block.begin(CTX, [{'kind': 'value', 'id': 'x', 'fields': {}}], block.clean({'organization': str(root.pk)}), ALLOW)
        self.assertEqual(start.call_args.args[0], {'domains': [], 'sources': list(DEFAULT_SOURCES), 'budgets': {'web': 0, 'netlas': 0}, 'organization_id': str(root.pk)})
        self.assertNotIn('web', start.call_args.args[0]['sources'])
        done = block.resume(CTX, [], block.clean({'organization': str(root.pk)}), waiting.state, {str(expansion.run_id): expansion.run})
        self.assertEqual((sorted(i['id'] for i in done.items), done.note, done.pages), (['old.acme.example', 'www.acme.example'], '2 candidates from the organization', 0))
        self.assertEqual(block.begin(CTX, [], block.clean({}), ALLOW).note, 'No domains to expand: give it domains or choose an organization')
        empty = self.expansion()
        with patch('leakscope.phase3.domain_expansion.start', return_value=empty):
            held = block.begin(CTX, [], block.clean({'organization': str(root.pk)}), ALLOW)
        self.assertIn('0 candidates from the organization · the free sources need at least one domain', block.resume(CTX, [], block.clean({'organization': str(root.pk)}), held.state, {str(empty.run_id): empty.run}).note)
        self.assertEqual((block.takes, block.gives, block.reaches(block.clean({})), block.cost(block.clean({}), 1).pages), ('any', 'domain', ('public',), 0))

    def test_paid_sources_are_opt_in_count_as_credits_and_stay_inside_the_pages_left(self):
        root = relationships.store.node('organization', 'Acme Corp')
        block = get('expand_domains')
        cleaned = block.clean({'organization': str(root.pk), 'web_search': True, 'netlas': True, 'archives': True})
        self.assertEqual((block.reaches(cleaned), block.cost(cleaned, 1).pages), (('public', 'credits'), 5))
        expansion = self.expansion(('www.acme.example', 90))
        with patch('leakscope.phase3.domain_expansion.start', return_value=expansion) as start:
            waiting = block.begin(CTX, [domain_item('acme.example')], cleaned, ALLOW)
            sent = start.call_args.args[0]
            self.assertEqual((sent['budgets'], sent['domains'], sent['sources'][-4:]), ({'web': 3, 'netlas': 2}, ['acme.example'], ['commoncrawl', 'wayback', 'web', 'netlas']))
            short = block.begin(CTX, [], cleaned, {'pages': 3, 'contacts': 25})
            self.assertEqual((start.call_args.args[0]['budgets'], 'netlas' in start.call_args.args[0]['sources']), ({'web': 3, 'netlas': 0}, False))
        done = block.resume(CTX, [], cleaned, waiting.state, {str(expansion.run_id): expansion.run})
        self.assertEqual((done.pages, done.note), (5, '1 candidates from the organization and 1 domains'))
        self.assertIn('Netlas left out: no pages left in this run', block.resume(CTX, [], cleaned, short.state, {str(expansion.run_id): expansion.run}).note)

    def test_provider_queries_are_written_in_each_providers_own_syntax(self):
        from leakscope.intelligence.queries import discovery_queries
        self.assertEqual(discovery_queries('shodan', 'Acme "Corp"', 'Acme.Example'), [('organization', 'ssl:"Acme \\"Corp\\""'), ('domain', 'hostname:"acme.example"')])
        self.assertEqual(discovery_queries('zoomeye', 'Acme (EU)', 'acme.example'), [('organization', 'ssl="Acme \\(EU\\)"'), ('domain', 'hostname="acme.example"')])
        self.assertEqual(discovery_queries('netlas', 'Acme Corp', 'acme.example'), [('organization', '"Acme Corp"'), ('domain', 'host:(acme.example OR *.acme.example)')])
        self.assertEqual(discovery_queries('shodan', '', 'acme.example'), [('domain', 'hostname:"acme.example"')])
        with self.assertRaisesMessage(ValueError, 'Not a domain name'): discovery_queries('netlas', '', 'acme.example) OR port:22')

    def test_provider_searches_run_one_page_each_and_pass_on_the_names_they_saved(self):
        import json
        from leakscope.build.blocks.base import Waiting as W
        root = relationships.store.node('organization', 'Acme Corp')
        block = get('expand_domains')
        cleaned = block.clean({'organization': str(root.pk), 'providers': 'shodan'})
        self.assertEqual((block.reaches(cleaned), block.cost(cleaned, 1).pages, block.cost(block.clean({'providers': 'all'}), 1).pages), (('public', 'credits'), 2, 6))
        expansion = self.expansion(('www.acme.example', 90))
        a, b = observe(1), observe(2)
        items = endpoint_items([str(a.pk), str(b.pk)])[0]
        names = [name for item in items for name in item['fields'].get('names') or []]
        runs = [Run.objects.create(kind='lookup', state='succeeded', result=json.dumps({'endpoint_ids': [str(a.pk), str(b.pk)], 'pages': 1})), Run.objects.create(kind='lookup', state='failed', error='rate limited')]
        asked = []
        def begin(ctx, given, settings, allowance):
            asked.append((settings['provider'], settings['query'], settings['pages'], settings['start']))
            return W([str(runs[(len(asked) - 1) % 2].pk)], {})
        with patch('leakscope.phase3.domain_expansion.start', return_value=expansion), patch.object(type(get('search')), 'begin', side_effect=begin, autospec=False):
            waiting = block.begin(CTX, [domain_item('shop.acme.com')], cleaned, ALLOW)
            short = block.begin(CTX, [domain_item('shop.acme.com')], cleaned, {'pages': 1, 'contacts': 25})
        self.assertEqual(asked[:2], [('shodan', 'ssl:"Acme Corp"', 1, 'beginning'), ('shodan', 'hostname:"acme.com"', 1, 'beginning')])
        self.assertEqual((len(asked), short.state['search_notes']), (3, ['some provider searches left out: no pages left in this run']))
        self.assertEqual(waiting.run_ids, [str(expansion.run_id), str(runs[0].pk), str(runs[1].pk)])
        done = block.resume(CTX, [], cleaned, waiting.state, {str(expansion.run_id): expansion.run, str(runs[0].pk): runs[0], str(runs[1].pk): runs[1]})
        self.assertEqual(done.pages, 1)
        self.assertIn('provider searches: shodan organization ' + str(len(set(names))) + ' names from 2 results, shodan domain search failed', done.note)
        self.assertEqual({i['id'] for i in done.items}, {'www.acme.example', *[n.lower() for n in names if '.' in n]})
