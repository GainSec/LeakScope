"""Cloud discovery: check names at cloud providers, and start from cloud resources already saved."""
import json
from django.db.models import Q
from leakscope.build.kinds import ENDPOINT_FIELDS, endpoint_items, gone
from leakscope.phase3.cloud import PROVIDERS, plan, validate_spec
from leakscope.phase3.cloud_jobs import queue_cloud
from .base import Block, BlockError, Field, Cost, Done, Waiting, register

PROVIDER_CHOICES = tuple((key, value['label']) for key, value in PROVIDERS.items())
STATES = (('public', 'Public listing'), ('access_denied', 'Access denied'), ('reachable', 'Reachable'), ('service_response', 'Service response'))
GROUP = 10          # the Cloud page checks at most ten names in one run


def _usable(name):
    try: return validate_spec({'names': [name]})['names'][0]
    except ValueError: return None


@register
class CloudDiscovery(Block):
    key, name, group, reach, takes, gives = 'cloud_discovery', 'Cloud discovery', 'find', 'target', 'any', 'endpoint'
    says = 'Checks whether storage or apps with these names exist at cloud providers, as the Cloud page does. It contacts the cloud endpoints directly.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('names', 'Names', 'tags', help='Resource or domain names. Domains that reach this step are checked as well.'),
              Field('provider', 'Provider', 'choice', default='', choices=(('', 'All providers'),) + PROVIDER_CHOICES),
              Field('kinds', 'Look for', 'choice', default='storage', choices=(('storage', 'Storage'), ('applications', 'Applications'), ('both', 'Storage and applications'))),
              Field('permutations', 'Also try common variations (-dev, -prod, -backup…)', 'flag', default=False),
              Field('max_requests', 'Checks per ten names', 'number', default=25, minimum=1, maximum=500),
              Field('pass_on', 'Pass on', 'choice', default='all', choices=(('all', 'Everything that answered'), ('public', 'Only public listings'))))

    def cost(self, settings, items_in):
        groups = max(1, -(-(len(settings['names']) + max(0, items_in - 1)) // GROUP))
        return Cost(contacts=settings['max_requests'] * groups, items_out=settings['max_requests'] * groups)

    def begin(self, ctx, items, settings, allowance):
        wanted = list(settings['names']) + [item['id'] for item in items if item.get('kind') == 'domain']
        names = list(dict.fromkeys(name for name in map(_usable, wanted) if name))
        unusable = len(wanted) - sum(1 for name in wanted if _usable(name))
        if not names: return Done([], note='No names to check' + (' · ' + str(unusable) + ' names cannot be cloud resource names' if unusable else ''))
        room, queued, left_out = allowance['contacts'], {}, 0
        for index in range(0, len(names), GROUP):
            if room <= 0: left_out += 1; continue
            spec = {'names': names[index:index + GROUP], 'providers': [settings['provider']] if settings['provider'] else list(PROVIDERS),
                    'kinds': ['storage', 'applications'] if settings['kinds'] == 'both' else [settings['kinds']],
                    'permutations': settings['permutations'], 'max_requests': min(settings['max_requests'], room)}
            try: planned = plan(spec)
            except ValueError as exc: raise BlockError(str(exc)) from None
            if not planned['candidates']: continue
            queued[str(queue_cloud(spec).pk)] = len(planned['candidates'])
            room -= len(planned['candidates'])
        state = {'queued': queued, 'left_out': left_out, 'unusable': unusable}
        if not queued: return Done([], note='Nothing was checked' + (' (contact ceiling)' if left_out else ''), left_out=left_out)
        return Waiting(list(queued), state)

    def resume(self, ctx, items, settings, state, runs):
        from leakscope_app.models import CloudResource
        ids, attempted = [], 0
        for key, child in runs.items():
            if child.state == 'failed': raise BlockError(child.error or 'Cloud discovery failed')
            try: result = json.loads(child.result or '{}')
            except ValueError: result = {}
            ids += result.get('resource_ids') or []
            attempted += int(result.get('attempted') or 0)
        rows = CloudResource.objects.filter(pk__in=ids).exclude(endpoint=None)
        public = rows.filter(state='public').count()
        if settings['pass_on'] == 'public': rows = rows.filter(state='public')
        found, dropped = endpoint_items(list(dict.fromkeys(str(pk) for pk in rows.values_list('endpoint_id', flat=True))))
        note = str(attempted) + ' checks · ' + str(len(set(ids))) + ' answered · ' + str(public) + ' public'
        if state.get('unusable'): note += ' · ' + str(state['unusable']) + ' names cannot be cloud resource names'
        if state.get('left_out'): note += ' · some names not checked (contact ceiling)'
        return Done(found, contacts=attempted, note=note + gone(dropped), left_out=state.get('left_out', 0))


@register
class SavedCloudResources(Block):
    key, name, group, reach, start, gives = 'saved_cloud_resources', 'Saved cloud resources', 'trigger', 'local', True, 'endpoint'
    says = 'Starts with what Cloud discovery already found: by provider, state or name.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('provider', 'Provider', 'choice', default='', choices=(('', 'Any'),) + PROVIDER_CHOICES),
              Field('state', 'State', 'choice', default='', choices=(('', 'Any'),) + STATES),
              Field('search', 'Name or address contains', 'text'), Field('limit', 'At most', 'number', default=100, minimum=1, maximum=5000))

    def cost(self, settings, items_in):
        return Cost(items_out=settings['limit'])

    def begin(self, ctx, items, settings, allowance):
        from leakscope_app.models import CloudResource
        rows = CloudResource.objects.exclude(endpoint=None)
        if settings['provider']: rows = rows.filter(provider=settings['provider'])
        if settings['state']: rows = rows.filter(state=settings['state'])
        if settings['search']: rows = rows.filter(Q(name__icontains=settings['search']) | Q(url__icontains=settings['search']))
        ids = list(dict.fromkeys(str(pk) for pk in rows.order_by('-last_checked').values_list('endpoint_id', flat=True)[:20000]))
        found, _ = endpoint_items(ids)
        found = found[:settings['limit']]
        return Done(found, note=str(len(found)) + ' saved cloud resource' + ('' if len(found) == 1 else 's'))
