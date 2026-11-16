"""Branches: a step can come after any earlier step, If has a Yes and a No side, and paths can rejoin."""
from leakscope.build import engine
from leakscope.build.blocks.base import get
from leakscope_app.models import TagAssignment
from tests.test_build_blocks import observe
from tests.test_build_engine import EngineCase, step, JENKINS, LOCAL


def after(identifier, block, sources, **settings):
    return {**step(identifier, block, **settings), 'after': sources}


def branched():
    return {'steps': [step('s1', 'saved_results', limit=50), step('if1', 'if', conditions=JENKINS),
                      after('yes', 'tag', ['if1:yes'], tags=['is-jenkins']), after('no', 'tag', ['if1:no'], tags=['other']),
                      after('join', 'tag', ['yes', 'no'], tags=['seen'])]}


class BranchValidationTests(EngineCase):
    def problems(self, definition):
        with self.assertRaises(ValueError) as caught: engine.validate(definition)
        return str(caught.exception)

    def test_a_branched_workflow_is_valid_and_the_default_order_is_not_stored(self):
        cleaned = engine.validate(branched())
        self.assertEqual([row.get('after') for row in cleaned['steps']], [None, None, ['if1:yes'], ['if1:no'], ['yes', 'no']])
        explicit = {'steps': [LOCAL['steps'][0], {**LOCAL['steps'][1], 'after': ['s1']}, LOCAL['steps'][2]]}
        self.assertEqual(engine.estimate(explicit)['token'], engine.estimate(LOCAL)['token'])

    def test_connection_problems_are_named(self):
        start, split = step('s1', 'saved_results'), step('if1', 'if', conditions=JENKINS)
        self.assertIn('Step 3: choose the Yes or the No side of If', self.problems({'steps': [start, split, step('t', 'tag', tags=['x'])]}))
        self.assertIn('Step 3: choose the Yes or the No side of If', self.problems({'steps': [start, split, after('t', 'tag', ['if1'], tags=['x'])]}))
        self.assertIn('Step 2: Saved results that match has no "yes" side', self.problems({'steps': [start, after('t', 'tag', ['s1:yes'], tags=['x'])]}))
        self.assertIn('Step 2: comes after "later", which is not an earlier step', self.problems({'steps': [start, after('t', 'tag', ['later'], tags=['x']), step('later', 'limit', count=1)]}))
        self.assertIn('Step 1: a start block cannot come after another step', self.problems({'steps': [after('s1', 'saved_results', ['x'])]}))
        self.assertIn('Step 2: Tag is not connected to an earlier step', self.problems({'steps': [start, after('t', 'tag', [], tags=['x'])]}))
        self.assertIn('Step 2: comes after must list one to eight earlier steps', self.problems({'steps': [start, after('t', 'tag', 's1', tags=['x'])]}))
        mixed = {'steps': [start, step('n', 'names_of_results'), after('t', 'filter', ['s1', 'n'], conditions=JENKINS)]}
        self.assertIn('Step 3: joins endpoint and domain items', self.problems(mixed))
        guarded = {'steps': [start, {**split, 'when': JENKINS}]}
        self.assertIn('Step 2: If cannot be limited to some results', self.problems(guarded))

    def test_estimate_adds_up_what_joins(self):
        numbers = engine.estimate(branched())
        self.assertEqual([(row['id'], row['items_in'], row['items_out']) for row in numbers['steps']],
                         [('s1', 0, 50), ('if1', 50, 50), ('yes', 50, 50), ('no', 50, 50), ('join', 100, 100)])
        self.assertEqual(get('if').ports, ('yes', 'no'))


class BranchRunTests(EngineCase):
    def tags(self, endpoint):
        return sorted(TagAssignment.objects.filter(target_kind='endpoint', target_key=str(endpoint.pk)).values_list('tag__name', flat=True))

    def test_both_sides_run_and_rejoin(self):
        a, b, c = observe(1), observe(2, 'grafana'), observe(3)
        _, run = self.launch(branched())
        data = self.slice(run)
        self.assertEqual(data['state'], 'succeeded')
        steps = {row['id']: row for row in data['result']['steps']}
        self.assertEqual([(key, steps[key]['items_in'], steps[key]['items_out']) for key in ('if1', 'yes', 'no', 'join')], [('if1', 3, 3), ('yes', 2, 2), ('no', 1, 1), ('join', 3, 3)])
        self.assertEqual((steps['if1']['branches'], steps['if1']['note']), ({'yes': 2, 'no': 1}, '2 yes · 1 no'))
        self.assertEqual((self.tags(a), self.tags(b), self.tags(c)), (['is-jenkins', 'seen'], ['other', 'seen'], ['is-jenkins', 'seen']))
        self.assertEqual((data['result']['items'], sorted(data['result']['endpoint_ids'])), (3, sorted(str(row.pk) for row in (a, b, c))))

    def test_a_side_nothing_reaches_does_not_run(self):
        observe(1), observe(3)
        _, run = self.launch(branched())
        steps = {row['id']: row for row in self.slice(run)['result']['steps']}
        self.assertEqual((steps['no']['state'], steps['no']['note'], steps['join']['items_in']), ('skipped', 'No results reached this step', 2))

    def test_two_steps_can_follow_the_same_step_and_both_ends_are_kept(self):
        a, b = observe(1), observe(2, 'grafana')
        flow = {'steps': [step('s1', 'saved_results', limit=50), step('f1', 'filter', conditions=JENKINS),
                          after('f2', 'filter', ['s1'], conditions={'rules': [{'field': 'technology', 'op': 'is', 'value': 'grafana'}]})]}
        _, run = self.launch(flow)
        data = self.slice(run)
        self.assertEqual([(row['id'], row['items_in'], row['items_out']) for row in data['result']['steps']], [('s1', 0, 2), ('f1', 2, 1), ('f2', 2, 1)])
        self.assertEqual(sorted(data['result']['endpoint_ids']), sorted([str(a.pk), str(b.pk)]))
