"""Drill down: start from chosen addresses or domains, keep only chosen ones mid-flow, and ask providers exact queries."""
import json
from unittest.mock import patch
from leakscope.build.blocks.base import Block, Field, get
from leakscope.build.kinds import domain_item, endpoint_items, render
from leakscope.phase3.state import ingest_observation
from leakscope_app.models import DomainCandidate, DomainExpansion, Run, SavedLookup
from tests.test_build_blocks import observe, CTX
from tests.test_build_engine import EngineCase, step
from leakscope.build import engine

ALLOW = {'pages': 5, 'contacts': 25}


def host(name, port=443):
    return ingest_observation('zoomeye', {'address': name, 'port': port, 'scheme': 'https', 'service_type': 'nginx', 'metadata': {}})


class ListFieldTests(EngineCase):
    def test_a_list_setting_takes_lines_or_a_list_and_has_limits(self):
        block = type('Listed', (Block,), {'key': 'listed', 'name': 'L', 'group': 'logic', 'reach': 'local', 'takes': 'endpoint', 'fields': (Field('names', 'Names', 'list', required=True),)})()
        self.assertEqual(block.clean({'names': ' a.example \n\n192.0.2.1, b.example\na.example'})['names'], ['a.example', '192.0.2.1', 'b.example'])
        self.assertEqual(block.clean({'names': ['x', ' y ']})['names'], ['x', 'y'])
        for bad in ({}, {'names': []}, {'names': 5}, {'names': ['x' * 254]}, {'names': [str(n) for n in range(501)]}, {'names': ['a b']}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): block.clean(bad)


class ChosenStartTests(EngineCase):
    def test_chosen_results_are_exactly_the_saved_results_at_those_addresses(self):
        a, b, other = observe(1), host('app.example.com'), observe(3)
        second = ingest_observation('zoomeye', {'address': '192.0.2.1', 'port': 9090, 'scheme': 'http', 'service_type': 'grafana', 'metadata': {}})
        block = get('chosen_results')
        done = block.begin(CTX, [], block.clean({'addresses': ['192.0.2.1', 'APP.example.com', '203.0.113.9']}), ALLOW)
        self.assertEqual(sorted(i['id'] for i in done.items), sorted(str(row.pk) for row in (a, b, second)))
        self.assertIn('3 saved results at 2 addresses', done.note); self.assertIn('nothing is saved for 203.0.113.9', done.note)
        one = block.begin(CTX, [], block.clean({'addresses': ['192.0.2.1:9090']}), ALLOW)
        self.assertEqual([i['id'] for i in one.items], [str(second.pk)])
        self.assertEqual((block.start, block.gives, block.reach, block.cost(block.clean({'addresses': ['a', 'b']}), 0).items_out), (True, 'endpoint', 'local', 100))

    def test_chosen_domains_and_addresses_need_not_be_saved(self):
        block = get('chosen_domains')
        done = block.begin(CTX, [], block.clean({'domains': ['Example.COM.', 'shop.example.com', 'bad_name!', 'example.com']}), ALLOW)
        self.assertEqual(([i['id'] for i in done.items], done.items[0]['kind']), (['example.com', 'shop.example.com'], 'domain'))
        self.assertIn('1 not a domain name: bad_name!', done.note)
        block = get('chosen_addresses')
        done = block.begin(CTX, [], block.clean({'addresses': ['203.0.113.9', 'App.Example.com', '2001:db8::1']}), ALLOW)
        self.assertEqual([(i['kind'], i['id'], i['fields']['ip'], i['fields']['hostname']) for i in done.items],
                         [('value', '203.0.113.9', '203.0.113.9', ''), ('value', 'app.example.com', '', 'app.example.com'), ('value', '2001:db8::1', '2001:db8::1', '')])
        flow = {'steps': [step('s1', 'chosen_addresses', addresses=['203.0.113.9']), step('s2', 'search', provider='shodan', query='ip:{{ip}}')]}
        self.assertEqual(engine.validate(flow)['steps'][1]['settings']['query'], 'ip:{{ip}}')

    def test_only_these_keeps_or_drops_chosen_results_and_domains(self):
        a, b, c = observe(1), host('app.example.com'), observe(3)
        items = endpoint_items([str(row.pk) for row in (a, b, c)])[0]
        block = get('only_these')
        kept = block.begin(CTX, items, block.clean({'values': ['192.0.2.1', 'app.example.com']}), ALLOW)
        self.assertEqual(([i['id'] for i in kept.items], kept.note), ([str(a.pk), str(b.pk)], '2 kept · 1 dropped'))
        rest = block.begin(CTX, items, block.clean({'values': ['192.0.2.1'], 'keep': 'others'}), ALLOW)
        self.assertEqual([i['id'] for i in rest.items], [str(b.pk), str(c.pk)])
        domains = [domain_item('example.com'), domain_item('shop.example.com')]
        self.assertEqual([i['id'] for i in block.begin(CTX, domains, block.clean({'values': ['SHOP.example.com']}), ALLOW).items], ['shop.example.com'])


class ExactQueryTests(EngineCase):
    def test_values_put_into_a_query_cannot_change_it(self):
        item = {'kind': 'value', 'id': 'x', 'fields': {'title': 'a" OR port:22 \\', 'ip': '192.0.2.1'}}
        self.assertEqual(render('title:"{{title}}" ip:{{ip}}', item, quoted=True), 'title:"a\\" OR port:22 \\\\" ip:192.0.2.1')
        self.assertEqual(render('{{title}}', item), 'a" OR port:22 \\')
        block = get('search')
        with patch('leakscope.build.blocks.find.queue_lookup', return_value=Run.objects.create(kind='lookup', state='queued')):
            block.begin(CTX, [item], block.clean({'provider': 'shodan', 'query': 'http.title:"{{title}}"', 'start': 'beginning'}), ALLOW)
        self.assertEqual(SavedLookup.objects.get().query, 'http.title:"a\\" OR port:22 \\\\"')

    def lookup(self, items, **settings):
        block = get('search_address')
        cleaned = block.clean({'provider': 'shodan', 'start': 'beginning', **settings})
        children = [Run.objects.create(kind='lookup', state='queued') for _ in items]
        with patch('leakscope.build.blocks.find.queue_lookup', side_effect=children):
            outcome = block.begin(CTX, items, cleaned, ALLOW)
        return block, cleaned, outcome, children

    def test_each_address_gets_its_own_exact_query_per_provider(self):
        items = endpoint_items([str(observe(1).pk), str(host('app.example.com').pk)])[0] + [domain_item('example.org')]
        self.lookup(items)
        self.assertEqual(sorted(SavedLookup.objects.values_list('query', flat=True)), ['hostname:"app.example.com"', 'hostname:"example.org"', 'ip:"192.0.2.1"'])
        SavedLookup.objects.all().delete()
        self.lookup(items[:2], provider='zoomeye')
        self.assertEqual(sorted(SavedLookup.objects.values_list('query', flat=True)), ['hostname="app.example.com"', 'ip="192.0.2.1"'])
        SavedLookup.objects.all().delete()
        self.lookup(items[:2], provider='netlas')
        self.assertEqual(sorted(SavedLookup.objects.values_list('query', flat=True)), ['host:"app.example.com"', 'ip:"192.0.2.1"'])
        block = get('search_address')
        self.assertEqual((block.reach, block.cost(block.clean({'provider': 'shodan', 'pages': 2}), 3).pages), ('credits', 6))

    def test_netlas_asks_for_subdomains_by_pattern_because_its_host_filter_is_exact(self):
        self.lookup([domain_item('example.org')], provider='netlas', match='subdomains')
        self.assertEqual(list(SavedLookup.objects.values_list('query', flat=True)), ['host:(example.org OR *.example.org)'])
        SavedLookup.objects.all().delete()
        self.lookup([domain_item('example.org')], provider='netlas')
        self.assertEqual(list(SavedLookup.objects.values_list('query', flat=True)), ['host:"example.org"'])

    def test_only_results_at_the_asked_address_are_passed_on_and_the_queries_are_shown(self):
        asked = endpoint_items([str(observe(1).pk)])[0]
        block, cleaned, waiting, children = self.lookup(asked)
        exact = ingest_observation('shodan', {'address': '192.0.2.1', 'port': 22, 'scheme': 'ssh', 'service_type': 'ssh', 'metadata': {}})
        stray = observe(77)
        children[0].state, children[0].result = 'succeeded', json.dumps({'endpoint_ids': [str(exact.pk), str(stray.pk)], 'pages': 1, 'new_results': 2})
        done = block.resume(CTX, asked, cleaned, waiting.state, {str(children[0].pk): children[0]})
        self.assertEqual([i['id'] for i in done.items], [str(exact.pk)])
        self.assertIn('1 at a different address left out', done.note)
        self.assertIn('Asked: ip:"192.0.2.1"', done.note)

    def test_a_name_lookup_can_include_subdomains(self):
        asked = [domain_item('example.com')]
        block, cleaned, waiting, children = self.lookup(asked, match='subdomains')
        exact, sub, stray = host('example.com'), host('shop.example.com'), host('badexample.com')
        children[0].state, children[0].result = 'succeeded', json.dumps({'endpoint_ids': [str(row.pk) for row in (exact, sub, stray)], 'pages': 1})
        done = block.resume(CTX, asked, cleaned, waiting.state, {str(children[0].pk): children[0]})
        self.assertEqual([i['id'] for i in done.items], [str(exact.pk), str(sub.pk)])
        strict = block.resume(CTX, asked, block.clean({'provider': 'shodan'}), waiting.state, {str(children[0].pk): children[0]})
        self.assertEqual([i['id'] for i in strict.items], [str(exact.pk)])


class PickerTests(EngineCase):
    def test_saved_addresses_and_domains_can_be_searched_for_the_picker(self):
        observe(1); host('app.example.com'); host('app.example.com', 8443)
        expansion = DomainExpansion.objects.create(run=Run.objects.create(kind='domain_expansion', state='succeeded'), scope_key='k', spec={})
        DomainCandidate.objects.create(expansion=expansion, domain='shop.example.com', root='example.com')
        found = self.client.get('/api/build/pick?kind=address&q=example').json()['results']
        self.assertEqual(found, [{'value': 'app.example.com', 'note': '2 saved results'}])
        self.assertEqual(self.client.get('/api/build/pick?kind=address&q=192.0.2').json()['results'], [{'value': '192.0.2.1', 'note': '1 saved result'}])
        self.assertEqual([row['value'] for row in self.client.get('/api/build/pick?kind=domain&q=example').json()['results']], ['app.example.com', 'shop.example.com'])
        self.assertEqual(self.client.get('/api/build/pick?kind=address&q=x').json()['results'], [])
        self.assertEqual(self.client.get('/api/build/pick?kind=other&q=example').status_code, 400)
