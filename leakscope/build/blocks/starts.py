from datetime import timedelta
from uuid import UUID
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from leakscope.build import conditions
from leakscope.build.kinds import VALUE, ENDPOINT_FIELDS, endpoint_items
from leakscope.phase3.state import visible_endpoints
from leakscope_app.models import ServiceObservation, TagAssignment
from .base import Block, BlockError, Field, Cost, Done, register

SCAN = 20000



@register
class RunByHand(Block):
    key, name, group, reach, start, gives = 'run_by_hand', 'Run by hand', 'trigger', 'local', True, 'value'
    says = 'Starts with nothing. Follow it with a search.'

    def cost(self, settings, items_in):
        return Cost(items_out=1)

    def begin(self, ctx, items, settings, allowance):
        return Done([dict(VALUE)])


@register
class SavedResults(Block):
    key, name, group, reach, start, gives = 'saved_results', 'Saved results that match', 'trigger', 'local', True, 'endpoint'
    says = 'Starts with observations LeakScope already holds: by technology, tag, source, organization, port, age, or any field condition.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('search', 'Address or title contains', 'text'),
              Field('technology', 'Technology', 'text', suggest='technologies', help='Exact technology name, for example jenkins.'),
              Field('tag', 'Tag', 'text', suggest='tags'),
              Field('source', 'Source', 'choice', default='', choices=(('', 'Any source'), ('shodan', 'Shodan'), ('zoomeye', 'ZoomEye'), ('netlas', 'Netlas'), ('direct', 'Direct capture'))),
              Field('organization', 'Organization', 'choice', options='organizations'),
              Field('port', 'Port', 'number', minimum=1, maximum=65535),
              Field('seen_days', 'Seen in the last (days)', 'number', minimum=1, maximum=3650),
              Field('conditions', 'Only when', 'conditions', help='Any field: IP, hostname, names, protocol, title, sources, tags. Contains, does not contain, matches, does not match.'),
              Field('limit', 'At most', 'number', default=100, minimum=1, maximum=5000))

    def cost(self, settings, items_in):
        return Cost(items_out=settings['limit'])

    def begin(self, ctx, items, settings, allowance):
        rows = visible_endpoints()
        if settings['technology']: rows = rows.filter(service_type__iexact=settings['technology'])
        if settings['search']: rows = rows.filter(Q(address__icontains=settings['search']) | Q(title__icontains=settings['search']))
        if settings['port']: rows = rows.filter(port=settings['port'])
        if settings['seen_days']: rows = rows.filter(last_seen__gte=timezone.now() - timedelta(days=settings['seen_days']))
        if settings['source']:
            seen = ServiceObservation.objects.filter(endpoint_id=OuterRef('pk'), hidden=False)
            seen = seen.filter(Q(provider__startswith='fetch:') | Q(provider__startswith='native:')) if settings['source'] == 'direct' else seen.filter(provider=settings['source'])
            rows = rows.annotate(build_source=Exists(seen)).filter(build_source=True)
        if settings['organization']:
            from leakscope_app.workspace import _scope, _member_records
            try: members = _scope(str(UUID(settings['organization'])))
            except (ValueError, ObjectDoesNotExist): raise BlockError('The chosen organization no longer exists') from None
            rows = _member_records(rows, members, 'service', 'endpoint_id')
        if settings['tag']:
            # Materialized so UUID conversion is right on both SQLite and PostgreSQL.
            keys = list(TagAssignment.objects.filter(target_kind='endpoint', tag__name=settings['tag'].casefold()).values_list('target_key', flat=True))
            rows = rows.filter(pk__in=keys)
        ordered = rows.order_by('-last_seen', '-pk').values_list('pk', flat=True)
        if not settings['conditions']:
            found, _ = endpoint_items(list(ordered[:settings['limit']]))
            return Done(found, note=str(len(found)) + ' saved result' + ('' if len(found) == 1 else 's'))
        # Conditions read fields that only exist on items (names, sources), so they are applied in batches
        # over at most 20,000 of the newest matching records.
        found, looked, keys = [], 0, list(ordered[:SCAN])
        for start in range(0, len(keys), 500):
            batch, _ = endpoint_items(keys[start:start + 500])
            looked += len(batch)
            found += [item for item in batch if conditions.matches(item, settings['conditions'])]
            if len(found) >= settings['limit']: break
        found = found[:settings['limit']]
        return Done(found, note=str(len(found)) + ' of ' + str(looked) + ' saved results matched' + (' · only the newest ' + str(SCAN) + ' were checked' if len(keys) == SCAN else ''))
