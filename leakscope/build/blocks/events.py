"""Starts that fire by themselves when new records appear. Run by hand, they use the last day."""
from datetime import datetime, timedelta
from django.utils import timezone
from leakscope.build import conditions
from leakscope.build.kinds import DOMAIN_FIELDS, ENDPOINT_FIELDS, domain_item, endpoint_items
from leakscope.phase3.state import visible_endpoints
from .base import Block, Field, Cost, Done, register

LIMIT = 500


def _since(value):
    try: return datetime.fromisoformat(value) if value else timezone.now() - timedelta(days=1)
    except ValueError: return timezone.now() - timedelta(days=1)


class Event(Block):
    group, reach, start, event = 'trigger', 'local', True, True

    def cost(self, settings, items_in):
        return Cost(items_out=LIMIT)

    def found(self, settings, since):
        raise NotImplementedError

    def pending(self, settings, since):
        return len(self.found(settings, since))

    def begin(self, ctx, items, settings, allowance):
        found = self.found(settings, _since(ctx.since))
        return Done(found, note=str(len(found)) + ' new since ' + ('the last check' if ctx.since else 'a day ago'))


@register
class NewObservations(Event):
    key, name, gives = 'new_observations', 'When new results are saved', 'endpoint'
    says = 'Starts by itself when LeakScope saves results it has not seen before, from any search, lookup or stream.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('technology', 'Technology', 'text', suggest='technologies'), Field('conditions', 'Only when', 'conditions'))

    def found(self, settings, since):
        rows = visible_endpoints().filter(first_seen__gt=since)
        if settings['technology']: rows = rows.filter(service_type__iexact=settings['technology'])
        items, _ = endpoint_items(list(rows.order_by('first_seen').values_list('pk', flat=True)[:LIMIT]))
        return [item for item in items if not settings['conditions'] or conditions.matches(item, settings['conditions'])]


@register
class ChangedObservations(Event):
    key, name, gives = 'changed_observations', 'When a result changes', 'endpoint'
    says = 'Starts by itself when a provider or a capture reports different content for a saved result.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('conditions', 'Only when', 'conditions'),)

    def found(self, settings, since):
        from leakscope_app.models import ObservationChange
        keys = list(dict.fromkeys(ObservationChange.objects.filter(collected_at__gt=since).order_by('collected_at').values_list('endpoint_id', flat=True)[:LIMIT * 4]))[:LIMIT]
        items, _ = endpoint_items(keys)
        return [item for item in items if not settings['conditions'] or conditions.matches(item, settings['conditions'])]


@register
class CertificateMatches(Event):
    key, name, gives = 'certificate_matches', 'When a certificate matches a watch', 'domain'
    says = 'Starts by itself when the certificate stream finds names that match one of your watches.'
    output_fields = DOMAIN_FIELDS
    fields = (Field('watch', 'Watch', 'choice', options='watches'),)

    def found(self, settings, since):
        from leakscope_app.models import CertificateWatchMatch
        rows = CertificateWatchMatch.objects.filter(first_seen__gt=since)
        if settings['watch']: rows = rows.filter(watch_key=settings['watch'])
        seen, found = set(), []
        for names, watch in rows.order_by('first_seen').values_list('matched_names', 'watch_name')[:LIMIT]:
            for name in names if isinstance(names, list) else []:
                item = domain_item(str(name).lstrip('*.'), source='certificate watch: ' + watch)
                if item['id'] and item['id'] not in seen and len(found) < LIMIT:
                    seen.add(item['id']); found.append(item)
        return found


@register
class LookupFinished(Event):
    key, name, gives = 'lookup_finished', 'When a search or lookup finishes', 'endpoint'
    says = 'Starts by itself when a search, scheduled lookup or custom query finishes, with the results it saved. Searches made by workflows do not count.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('lookup', 'Only this lookup', 'choice', options='lookups'), Field('conditions', 'Only when', 'conditions'))

    def found(self, settings, since):
        import json
        from leakscope_app.models import Run, SavedLookup
        keys = []
        for parameters, result in Run.objects.filter(kind='lookup', state='succeeded', finished_at__gt=since).order_by('finished_at').values_list('parameters', 'result')[:200]:
            try: lookup, found = str(json.loads(parameters or '{}').get('lookup_id') or ''), json.loads(result or '{}').get('endpoint_ids') or []
            except (ValueError, AttributeError): continue
            if settings['lookup'] and lookup != settings['lookup']: continue
            keys.append((lookup, [str(key) for key in found if isinstance(key, str)]))
        # A search a workflow made must not start workflows: that could run in a circle.
        def real(value):
            import uuid
            try: uuid.UUID(value); return True
            except ValueError: return False
        made = {str(pk) for pk in SavedLookup.objects.filter(pk__in=[lookup for lookup, _ in keys if real(lookup)], name__startswith='Workflow · ').values_list('pk', flat=True)}
        wanted = list(dict.fromkeys(key for lookup, found in keys if lookup not in made for key in found))[:LIMIT]
        items, _ = endpoint_items(wanted)
        return [item for item in items if not settings['conditions'] or conditions.matches(item, settings['conditions'])]
