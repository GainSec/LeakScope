from django.db.models import ProtectedError
from django.test import TestCase
from leakscope.build import conditions
from leakscope.build.blocks import base
from leakscope.build.blocks.base import Block, Field, Done, Context, register, get, catalog, unregister
from leakscope.build.kinds import endpoint_items, ENDPOINT_FIELDS
from leakscope.phase3.state import ingest_observation, set_tags
from leakscope_app.models import Workflow, WorkflowRun, Run, ServiceObservation, TagAssignment


def observe(last, technology='jenkins', provider='zoomeye', **extra):
    return ingest_observation(provider, {'address': '192.0.2.%d' % last, 'port': 8080, 'scheme': 'http', 'service_type': technology,
                                         'title': 'Host %d' % last, 'metadata': {'title': 'Host %d' % last}, **extra})


def hide(endpoint):
    ServiceObservation.objects.filter(endpoint=endpoint).update(hidden=True)


CTX = Context(workflow_id='w1', workflow_run_id='r1', workflow_name='Test', origin='workflow:w1')
ALLOW = {'pages': 5, 'contacts': 25}


class DefinitionFormatTests(TestCase):
    def tearDown(self):
        unregister('test_block')

    def make(self, **attrs):
        return type('TestBlock', (Block,), {'key': 'test_block', 'name': 'Test block', 'group': 'logic', 'reach': 'local', 'takes': 'endpoint',
            'fields': (Field('query', 'Query', 'text', required=True), Field('pages', 'Pages', 'number', default=1, minimum=1, maximum=20),
                       Field('mode', 'Mode', 'choice', default='a', choices=(('a', 'First'), ('b', 'Second'))), Field('tags', 'Tags', 'tags')), **attrs})

    def test_register_and_catalog(self):
        register(self.make())
        row = next(item for item in catalog() if item['key'] == 'test_block')
        self.assertEqual((row['name'], row['group'], row['reach'], row['start'], row['takes'], row['gives']), ('Test block', 'logic', 'local', False, ['endpoint'], 'endpoint'))
        self.assertEqual(row['fields'][0], {'key': 'query', 'label': 'Query', 'type': 'text', 'required': True, 'default': None, 'choices': [], 'minimum': None, 'maximum': None, 'help': '', 'options': '', 'suggest': '', 'template': False})
        self.assertEqual(row['fields'][2]['choices'], [['a', 'First'], ['b', 'Second']])
        self.assertIsInstance(get('test_block'), Block)
        with self.assertRaises(ValueError): register(self.make())
        unregister('test_block')
        for bad in ({'reach': 'everywhere'}, {'group': 'misc'}, {'start': True}, {'key': ''}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): register(self.make(**bad))
        with self.assertRaises(KeyError): get('test_block')

    def test_catalog_is_ordered_by_group_then_name(self):
        rows = catalog()
        order = [base.GROUPS.index(row['group']) for row in rows]
        self.assertEqual(order, sorted(order))

    def test_clean_validates_fields(self):
        block = self.make()()
        self.assertEqual(block.clean({'query': '  app="x"  '}), {'query': 'app="x"', 'pages': 1, 'mode': 'a', 'tags': []})
        with self.assertRaisesMessage(ValueError, 'Query is required'): block.clean({})
        for bad in ({'pages': 0}, {'pages': 21}, {'pages': True}, {'pages': '2x'}, {'mode': 'c'}, {'other': 1}, {'tags': ['']}, {'tags': 'one'},
                    {'tags': ['x'] * 21}, {'query': 'x' * 8001}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): block.clean({'query': 'q', **bad})
        self.assertEqual(block.clean({'query': 'q', 'pages': '3', 'tags': [' A ', 'b']})['pages'], 3)
        with self.assertRaises(ValueError): block.clean(['query'])
        flagged = type('Flagged', (Block,), {'key': 'flagged', 'name': 'F', 'group': 'logic', 'reach': 'local', 'takes': 'endpoint', 'fields': (Field('on', 'On', 'flag', default=False),)})()
        self.assertEqual((flagged.clean({}), flagged.clean({'on': True})), ({'on': False}, {'on': True}))
        with self.assertRaises(ValueError): flagged.clean({'on': 'true'})


class KindTests(TestCase):
    def test_endpoint_items_keep_order_drop_hidden_and_count_captures(self):
        first, second, third = observe(1), observe(2, 'grafana'), observe(3)
        observe(2, 'grafana'); hide(third)
        set_tags('endpoint', second.pk, ['beta', 'alpha'])
        ids = [str(second.pk), 'not-a-uuid', str(third.pk), str(first.pk)]
        with self.assertNumQueries(4):
            items, dropped = endpoint_items(ids)
        self.assertEqual([item['id'] for item in items], [str(second.pk), str(first.pk)])
        self.assertEqual(dropped, 2)
        fields = items[0]['fields']
        self.assertEqual(items[0]['kind'], 'endpoint')
        self.assertEqual((fields['address'], fields['port'], fields['scheme'], fields['technology'], fields['title'], fields['times_captured'], fields['tags']),
                         ('192.0.2.2', 8080, 'http', 'grafana', 'Host 2', 2, ['alpha', 'beta']))
        self.assertEqual(set(fields), set(ENDPOINT_FIELDS))
        self.assertEqual((fields['ip'], fields['hostname'], fields['protocol'], fields['sources']), ('192.0.2.2', '', 'http', ['zoomeye']))
        named = ingest_observation('shodan', {'address': 'login.example.com', 'port': 22, 'transport': 'tcp', 'service_type': 'ssh', 'metadata': {'hostnames': ['alt.example.com']}})
        host = endpoint_items([str(named.pk)])[0][0]['fields']
        self.assertEqual((host['ip'], host['hostname'], host['protocol']), ('', 'login.example.com', 'tcp'))
        self.assertIn('login.example.com', host['names'])
        self.assertEqual(endpoint_items([]), ([], 0))

    def test_models_round_trip(self):
        definition = {'steps': [{'id': 's1', 'block': 'run_by_hand', 'settings': {}}], 'limits': {'pages': 5}}
        workflow = Workflow.objects.create(name='One', definition=definition)
        self.assertEqual(Workflow.objects.get(pk=workflow.pk).definition, definition)
        run = Run.objects.create(kind='workflow')
        WorkflowRun.objects.create(workflow=workflow, run=run, definition=definition)
        with self.assertRaises(ProtectedError): workflow.delete()


class ConditionTests(TestCase):
    item = {'kind': 'endpoint', 'id': 'x', 'fields': {'technology': 'Jenkins', 'port': 8080, 'title': 'Build Server', 'tags': ['prod', 'ci'], 'scheme': ''}}

    def check(self, field, op, value, expected):
        cleaned = conditions.clean({'match': 'all', 'rules': [{'field': field, 'op': op, 'value': value}]})
        self.assertEqual(conditions.matches(self.item, cleaned), expected, (field, op, value))

    def test_conditions(self):
        for row in [('technology', 'is', 'jenkins', True), ('technology', 'is_not', 'jenkins', False), ('technology', 'one_of', 'grafana, JENKINS', True),
                    ('technology', 'one_of', 'grafana,kibana', False), ('title', 'contains', 'server', True), ('title', 'starts_with', 'build', True),
                    ('title', 'starts_with', 'server', False), ('scheme', 'empty', '', True), ('scheme', 'not_empty', '', False), ('missing', 'empty', '', True),
                    ('port', 'gt', '8000', True), ('port', 'lt', '8000', False), ('port', 'is', '8080', True), ('title', 'gt', '5', False), ('port', 'gt', 'abc', False),
                    ('title', 'not_contains', 'server', False), ('title', 'not_contains', 'login', True), ('title', 'ends_with', 'SERVER', True),
                    ('title', 'matches', '^build\\s+serv', True), ('title', 'not_matches', 'serv.r$', False), ('technology', 'matches', 'jen|graf', True),
                    ('tags', 'matches', '^pro', True), ('tags', 'not_contains', 'ci', False), ('missing', 'not_matches', 'x', True),
                    ('tags', 'is', 'CI', True), ('tags', 'one_of', 'dev,prod', True), ('tags', 'contains', 'ro', True), ('tags', 'is', 'dev', False), ('tags', 'empty', '', False)]:
            self.check(*row)
        both = {'rules': [{'field': 'technology', 'op': 'is', 'value': 'jenkins'}, {'field': 'port', 'op': 'is', 'value': '1'}]}
        self.assertFalse(conditions.matches(self.item, conditions.clean({**both, 'match': 'all'})))
        self.assertTrue(conditions.matches(self.item, conditions.clean({**both, 'match': 'any'})))
        self.assertEqual(conditions.clean(both)['match'], 'all')
        rule = {'field': 'a', 'op': 'is', 'value': 'b'}
        for bad in ({'rules': []}, {'rules': [rule] * 11}, {'rules': [{**rule, 'op': 'like'}]}, {'rules': [{**rule, 'value': 'x' * 501}]}, {'rules': [{**rule, 'field': ''}]},
                    {'rules': [{**rule, 'op': 'matches', 'value': '('}]}, {'rules': [{**rule, 'op': 'matches', 'value': '(a+)+$'}]}, {'rules': [{**rule, 'op': 'matches', 'value': ''}]},
                    {'rules': [{**rule, 'op': 'not_matches', 'value': 'x' * 201}]}, {'rules': [rule], 'match': 'some'}, {'rules': 'x'}, 'x', {'rules': [{**rule, 'value': 5}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): conditions.clean(bad)


class LocalBlockTests(TestCase):
    def test_run_by_hand_gives_one_empty_value(self):
        block = get('run_by_hand')
        self.assertTrue(block.start)
        self.assertEqual(block.begin(CTX, [], block.clean({}), ALLOW).items, [{'kind': 'value', 'id': '', 'fields': {}}])

    def test_saved_results_filters_and_limits(self):
        old, new, other, gone = observe(1), observe(2), observe(3, 'grafana'), observe(4)
        hide(gone); set_tags('endpoint', old.pk, ['prod'])
        block = get('saved_results')
        run = lambda **settings: [item['id'] for item in block.begin(CTX, [], block.clean(settings), ALLOW).items]
        self.assertEqual(run(), [str(other.pk), str(new.pk), str(old.pk)])
        self.assertEqual(run(technology='Jenkins'), [str(new.pk), str(old.pk)])
        self.assertEqual(run(tag='PROD'), [str(old.pk)])
        self.assertEqual(run(search='192.0.2.3'), [str(other.pk)])
        self.assertEqual(run(limit=1), [str(other.pk)])
        self.assertEqual(block.cost(block.clean({'limit': 7}), 0).items_out, 7)
        self.assertEqual(block.clean({})['limit'], 100)

    def test_filter_and_keep_new(self):
        a, b, c = observe(1), observe(2, 'grafana'), observe(3)
        observe(3)
        items, _ = endpoint_items([str(a.pk), str(b.pk), str(c.pk)])
        block = get('filter')
        result = block.begin(CTX, items, block.clean({'conditions': {'rules': [{'field': 'technology', 'op': 'is', 'value': 'jenkins'}]}}), ALLOW)
        self.assertEqual([item['id'] for item in result.items], [str(a.pk), str(c.pk)])
        self.assertEqual(result.note, '2 kept · 1 dropped')
        with self.assertRaises(ValueError): block.clean({})
        keep = get('keep_new')
        result = keep.begin(CTX, items, keep.clean({}), ALLOW)
        self.assertEqual([item['id'] for item in result.items], [str(a.pk), str(b.pk)])
        self.assertEqual(result.note, '2 new · 1 captured before')

    def test_tag_uses_the_workflow_origin_and_keeps_manual_tags(self):
        endpoint = observe(1)
        set_tags('endpoint', endpoint.pk, ['manual'])
        items, _ = endpoint_items([str(endpoint.pk)])
        block = get('tag')
        result = block.begin(CTX, items, block.clean({'tags': ['Acme-New', 'from-build']}), ALLOW)
        rows = sorted(TagAssignment.objects.filter(target_key=str(endpoint.pk)).values_list('tag__name', 'origin'))
        self.assertEqual(rows, [('acme-new', 'workflow:w1'), ('from-build', 'workflow:w1'), ('manual', 'manual')])
        self.assertEqual(result.items[0]['fields']['tags'], ['acme-new', 'from-build', 'manual'])
        block.begin(CTX, items, block.clean({'tags': ['second']}), ALLOW)
        self.assertEqual(sorted(TagAssignment.objects.filter(target_key=str(endpoint.pk)).values_list('tag__name', flat=True)), ['manual', 'second'])
        with self.assertRaises(ValueError): block.clean({'tags': []})

    def test_item_hidden_between_steps_is_dropped_and_counted(self):
        a, b = observe(1), observe(2)
        items, _ = endpoint_items([str(a.pk), str(b.pk)])
        hide(b)
        block = get('tag')
        result = block.begin(CTX, items, block.clean({'tags': ['x']}), ALLOW)
        self.assertEqual([item['id'] for item in result.items], [str(a.pk)])
        self.assertIn('1 no longer available', result.note)
        self.assertFalse(TagAssignment.objects.filter(target_key=str(b.pk)).exists())


class RicherSavedResultsTests(TestCase):
    def run_block(self, **settings):
        block = get('saved_results')
        return block.begin(CTX, [], block.clean(settings), ALLOW)

    def ids(self, **settings):
        return [item['fields']['address'] for item in self.run_block(**settings).items]

    def test_source_port_age_and_conditions(self):
        from django.utils import timezone
        from datetime import timedelta
        from leakscope_app.models import ServiceEndpoint
        observe(1); observe(2, provider='shodan')
        ingest_observation('netlas', {'address': 'vpn.example.com', 'port': 443, 'scheme': 'https', 'service_type': 'nginx', 'title': 'Remote access', 'metadata': {}})
        ServiceEndpoint.objects.filter(address='192.0.2.1').update(last_seen=timezone.now() - timedelta(days=40))
        self.assertEqual(self.ids(source='shodan'), ['192.0.2.2'])
        self.assertEqual(self.ids(port=443), ['vpn.example.com'])
        self.assertEqual(sorted(self.ids(seen_days=30)), ['192.0.2.2', 'vpn.example.com'])
        rules = lambda field, op, value: {'conditions': {'rules': [{'field': field, 'op': op, 'value': value}]}}
        self.assertEqual(self.ids(**rules('ip', 'starts_with', '192.0.2.')), ['192.0.2.2', '192.0.2.1'])
        self.assertEqual(self.ids(**rules('hostname', 'matches', r'^vpn\.')), ['vpn.example.com'])
        self.assertEqual(sorted(self.ids(**rules('names', 'not_contains', 'example.com'))), ['192.0.2.1', '192.0.2.2'])
        self.assertEqual(self.ids(**rules('protocol', 'is', 'https')), ['vpn.example.com'])
        self.assertEqual(self.ids(**rules('sources', 'is', 'netlas')), ['vpn.example.com'])
        result = self.run_block(limit=1, **rules('ip', 'not_empty', ''))
        self.assertEqual((len(result.items), result.note), (1, '1 of 3 saved results matched'))
        with self.assertRaises(ValueError): get('saved_results').clean({'port': 70000})
        with self.assertRaises(ValueError): get('saved_results').clean({'source': 'censys'})

    def test_organization_filter_and_add_to_organization(self):
        from leakscope.build.blocks.base import BlockError
        from leakscope_app.models import AssetNode
        inside, outside = observe(1), observe(2)
        organization = AssetNode.objects.create(kind='organization', value='Acme', organization_status='active')
        items, _ = endpoint_items([str(inside.pk)])
        block = get('add_to_organization')
        settings = block.clean({'organization': str(organization.pk)})
        self.assertEqual(block.begin(CTX, items, settings, ALLOW).note, '1 added · 0 already there')
        self.assertEqual(block.begin(CTX, items, settings, ALLOW).note, '0 added · 1 already there')
        self.assertEqual(self.ids(organization=str(organization.pk)), ['192.0.2.1'])
        with self.assertRaisesMessage(BlockError, 'no longer exists'): self.run_block(organization='00000000-0000-0000-0000-000000000000')
        with self.assertRaisesMessage(BlockError, 'not active'): block.begin(CTX, items, block.clean({'organization': '00000000-0000-0000-0000-000000000000'}), ALLOW)
        with self.assertRaises(ValueError): block.clean({})

    def test_limit(self):
        items, _ = endpoint_items([str(observe(n).pk) for n in range(1, 5)])
        block = get('limit')
        result = block.begin(CTX, items, block.clean({'count': 3}), ALLOW)
        self.assertEqual(([item['id'] for item in result.items], result.note), ([item['id'] for item in items[:3]], '3 kept · 1 dropped'))
        self.assertEqual(block.cost(block.clean({'count': 3}), 10).items_out, 3)
