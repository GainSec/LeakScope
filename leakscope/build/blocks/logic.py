from leakscope.build import conditions
from leakscope.build.kinds import refresh, gone
from .base import Block, BlockError, Field, Cost, Done, Waiting, register
from leakscope.build.kinds import ENDPOINT_FIELDS


@register
class Filter(Block):
    key, name, group, reach, takes, gives = 'filter', 'Filter', 'logic', 'local', ('endpoint', 'domain'), 'same'
    says = 'Keeps results whose fields match: technology is, port is one of, title contains.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('conditions', 'Keep when', 'conditions', required=True),)

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)
        kept = [item for item in items if conditions.matches(item, settings['conditions'])]
        return Done(kept, note=str(len(kept)) + ' kept · ' + str(len(items) - len(kept)) + ' dropped' + gone(dropped))


@register
class If(Block):
    key, name, group, reach, takes, gives, ports = 'if', 'If', 'logic', 'local', ('endpoint', 'domain'), 'same', ('yes', 'no')
    says = 'Splits results in two: the ones that match go down Yes, the rest go down No.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('conditions', 'If', 'conditions', required=True),)

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)
        yes = [item for item in items if conditions.matches(item, settings['conditions'])]
        no = [item for item in items if not conditions.matches(item, settings['conditions'])]
        return Done(yes + no, branches={'yes': yes, 'no': no}, note=str(len(yes)) + ' yes · ' + str(len(no)) + ' no' + gone(dropped))


@register
class KeepNew(Block):
    key, name, group, reach, takes, gives = 'keep_new', 'Keep only new results', 'logic', 'local', 'endpoint', 'endpoint'
    says = 'Drops results that were captured before, so later steps never repeat work.'
    output_fields = ENDPOINT_FIELDS

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)
        kept = [item for item in items if item['fields']['times_captured'] <= 1]
        return Done(kept, note=str(len(kept)) + ' new · ' + str(len(items) - len(kept)) + ' captured before' + gone(dropped))


@register
class Limit(Block):
    key, name, group, reach, takes, gives = 'limit', 'Limit', 'logic', 'local', ('endpoint', 'domain'), 'same'
    says = 'Passes on only the first results, to keep a run small.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('count', 'Keep the first', 'number', required=True, default=10, minimum=1, maximum=5000),)

    def cost(self, settings, items_in):
        return Cost(items_out=min(items_in, settings['count']))

    def begin(self, ctx, items, settings, allowance):
        kept = items[:settings['count']]
        return Done(kept, note=str(len(kept)) + ' kept · ' + str(len(items) - len(kept)) + ' dropped')


@register
class Sort(Block):
    key, name, group, reach, takes, gives = 'sort', 'Sort', 'logic', 'local', ('endpoint', 'domain'), 'same'
    says = 'Orders what flows through by a field: name A to Z, date added, confidence. A Limit after it keeps the ones you want.'
    fields = (Field('by', 'Sort by', 'text', required=True, suggest='fields'),
              Field('order', 'Order', 'choice', default='desc', choices=(('asc', 'A to Z · smallest · oldest first'), ('desc', 'Z to A · largest · newest first'))))

    def begin(self, ctx, items, settings, allowance):
        from leakscope.build.kinds import text_of
        def rank(item):
            value = (item.get('fields') or {}).get(settings['by'])
            number = isinstance(value, (int, float)) and not isinstance(value, bool)
            return (0, value, '') if number else (1, 0, text_of(value).casefold())
        # Items that lack the field go last whichever way the rest are ordered.
        have = [item for item in items if rank(item) != (1, 0, '')]
        ordered = sorted(have, key=rank, reverse=settings['order'] == 'desc') + [item for item in items if rank(item) == (1, 0, '')]
        text = any(rank(item)[0] for item in have)
        date = settings['by'] in ('added', 'first_seen', 'last_seen', 'expires')
        way = ('newest first' if date else 'Z to A' if text else 'largest first') if settings['order'] == 'desc' else ('oldest first' if date else 'A to Z' if text else 'smallest first')
        return Done(ordered, note='Sorted by ' + settings['by'] + ', ' + way)


@register
class RemoveDuplicates(Block):
    key, name, group, reach, takes, gives = 'remove_duplicates', 'Remove duplicates', 'logic', 'local', ('endpoint', 'domain'), 'same'
    says = 'Keeps the first item for each value of a field: one per IP, one per root domain.'
    fields = (Field('by', 'One per', 'text', required=True, suggest='fields'),)

    def begin(self, ctx, items, settings, allowance):
        from leakscope.build.kinds import text_of
        seen, kept = set(), []
        for item in items:
            value = text_of((item.get('fields') or {}).get(settings['by'])).casefold()
            if value in seen: continue
            seen.add(value); kept.append(item)
        return Done(kept, note=str(len(kept)) + ' kept · ' + str(len(items) - len(kept)) + ' duplicates dropped')


@register
class StopIf(Block):
    key, name, group, reach, takes, gives = 'stop_if', 'Stop if', 'logic', 'local', ('endpoint', 'domain'), 'same'
    says = 'A guard before an expensive step: passes everything on, or nothing when there are too many or too few. Steps after a stop are skipped.'
    fields = (Field('when', 'Stop when there are', 'choice', default='more_than', choices=(('more_than', 'More than'), ('fewer_than', 'Fewer than'))),
              Field('count', 'This many', 'number', required=True, default=100, minimum=1, maximum=5000))

    def begin(self, ctx, items, settings, allowance):
        more = settings['when'] == 'more_than'
        if (len(items) > settings['count']) if more else (len(items) < settings['count']):
            return Done([], note='Stopped: ' + str(len(items)) + ' is ' + ('more' if more else 'fewer') + ' than ' + str(settings['count']))
        return Done(items, note=str(len(items)) + ' passed on')


@register
class Count(Block):
    key, name, group, reach, takes, gives = 'count', 'Count', 'logic', 'local', ('endpoint', 'domain'), 'value'
    says = 'Counts what flows through per value of a field: results per technology, domains per status. Export file can save the counts.'
    adds_fixed = ('value', 'count')
    fields = (Field('by', 'Count per', 'text', required=True, suggest='fields'),)

    def cost(self, settings, items_in):
        return Cost(items_out=items_in)

    def begin(self, ctx, items, settings, allowance):
        totals = {}
        for item in items:
            value = (item.get('fields') or {}).get(settings['by'])
            # A list (tags, names, sources) counts once for each entry.
            for part in (value if isinstance(value, (list, tuple)) and value else [value]):
                name = ' '.join(str(part).split())[:200] if part not in (None, '', [], ()) else '(none)'
                totals[name] = totals.get(name, 0) + 1
        ranked = sorted(totals.items(), key=lambda pair: (pair[0] == '(none)', -pair[1], pair[0].casefold()))
        found = [{'kind': 'value', 'id': name, 'fields': {'value': name, 'count': total}} for name, total in ranked]
        top = ''.join(' · ' + name + ' ' + str(total) for name, total in ranked[:8])
        return Done(found, note=str(len(found)) + ' value' + ('' if len(found) == 1 else 's') + ' of ' + settings['by'] + top + (' · …' if len(ranked) > 8 else ''))


@register
class Wait(Block):
    key, name, group, reach, takes, gives = 'wait', 'Wait', 'logic', 'local', ('endpoint', 'domain'), 'same'
    says = 'Holds the results for some minutes before the next step, for example to let a target settle between two checks.'
    fields = (Field('minutes', 'Minutes', 'number', required=True, default=5, minimum=1, maximum=60),)

    def begin(self, ctx, items, settings, allowance):
        from datetime import timedelta
        from django.utils import timezone
        return Waiting([], {'until': (timezone.now() + timedelta(minutes=settings['minutes'])).isoformat()})

    def resume(self, ctx, items, settings, state, runs):
        from datetime import datetime
        from django.utils import timezone
        if timezone.now() < datetime.fromisoformat(state['until']): return Waiting([], state)
        return Done(items, note='Waited ' + str(settings['minutes']) + ' minute' + ('' if settings['minutes'] == 1 else 's'))


@register
class RunWorkflow(Block):
    key, name, group, reach, takes, gives = 'run_workflow', 'Run another workflow', 'logic', 'local', ('endpoint', 'domain'), 'same'
    says = 'Runs a saved workflow as one step and waits for it. What it can spend is added to this workflow’s confirmation.'
    fields = (Field('workflow', 'Workflow', 'choice', required=True, options='workflows'),
              Field('start', 'It starts with', 'choice', default='given', choices=(('given', 'The results that reach this step'), ('own', 'Its own start, as usual'))))

    def _child(self, settings):
        from django.core.exceptions import ValidationError
        from leakscope_app.models import Workflow
        try: return Workflow.objects.filter(pk=settings['workflow']).first()
        except (ValueError, ValidationError): return None

    def clean(self, settings):
        cleaned = super().clean(settings)
        child = self._child(cleaned)
        if child is None: raise ValueError('The chosen workflow no longer exists')
        steps = child.definition.get('steps') if isinstance(child.definition, dict) else None
        if any(isinstance(step, dict) and step.get('block') == self.key for step in steps or []):
            raise ValueError('“' + child.name + '” itself runs another workflow; only one level is allowed')
        return cleaned

    def cost(self, settings, items_in):
        from leakscope.build import engine
        child = self._child(settings)
        try: numbers = engine.estimate(child.definition)
        except (ValueError, AttributeError): return Cost()
        return Cost(pages=numbers['pages'], contacts=numbers['contacts'])

    def _numbers(self, settings):
        from leakscope.build import engine
        child = self._child(settings)
        try: return engine.estimate(child.definition)
        except (ValueError, AttributeError): return None

    def reaches(self, settings):
        numbers = self._numbers(settings)
        return (self.reach,) + tuple(numbers['reaches'] if numbers else ())

    def signature(self, settings):
        numbers = self._numbers(settings)
        return numbers['token'] if numbers else 'missing'

    def begin(self, ctx, items, settings, allowance):
        from leakscope.build import engine
        from .base import get
        child = self._child(settings)
        if child is None: raise BlockError('The chosen workflow no longer exists')
        if str(child.pk) == str(ctx.workflow_id): raise BlockError('A workflow cannot run itself')
        label = '“' + child.name + '”'
        try: numbers = engine.estimate(child.definition)
        except ValueError: raise BlockError(label + ' has problems to fix before it can run') from None
        given = settings['start'] == 'given'
        if given and items:
            first = get(engine.validate(child.definition)['steps'][0]['block'])
            if first.gives != items[0].get('kind'):
                raise BlockError(label + ' starts with ' + str(first.gives) + ' items, but this step was given ' + str(items[0].get('kind')) + ' items')
        try: run = engine.start(child, numbers['token'], seed=items if given else None, ceilings=allowance, given_by=ctx.workflow_name)
        except ValueError as exc:
            raise BlockError(label + ' is already running' if 'already active' in str(exc) else label + ': ' + str(exc)) from None
        return Waiting([str(run.pk)], {'name': child.name})

    def resume(self, ctx, items, settings, state, runs):
        import json
        child, label = next(iter(runs.values())), '“' + state.get('name', '') + '”'
        try: result = json.loads(child.result or '{}')
        except ValueError: result = {}
        result = result if isinstance(result, dict) else {}
        if child.state != 'succeeded': raise BlockError(label + ' ' + ('failed: ' + (child.error or 'no reason given') if child.state == 'failed' else 'ended as ' + child.state))
        spent = result.get('spent') or {}
        note = 'Ran ' + label + ': finished with ' + str(result.get('items', 0)) + ' at its end' + (' · it stopped early at a ceiling' if result.get('partial') else '')
        return Done(items, pages=int(spent.get('pages') or 0), contacts=int(spent.get('contacts') or 0), note=note)
