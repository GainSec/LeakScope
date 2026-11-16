"""Blocks that start one existing job per result and wait for them."""
from django.core.exceptions import ObjectDoesNotExist
from leakscope.build.kinds import ENDPOINT_FIELDS, endpoint_items, refresh, gone
from leakscope_app.models import ServiceEndpoint
from .base import Block, Field, Cost, Done, Waiting, register


class PerResultJob(Block):
    """Queues `launch(ctx, item, settings)` for each result, up to the remaining allowance of `spends`."""
    takes, gives = 'endpoint', 'endpoint'
    output_fields = ENDPOINT_FIELDS
    spends = None           # 'pages', 'contacts' or None
    done_word = 'done'

    def cost(self, settings, items_in):
        return Cost(**({self.spends: items_in} if self.spends else {}))

    def launch(self, ctx, item, settings):
        """Return the id of a queued Run, True when saved evidence already answers it, or None when it cannot run."""
        raise NotImplementedError

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)
        room = allowance[self.spends] if self.spends else len(items)
        chosen, left_out = items[:room], max(0, len(items) - room)
        queued, ready, missed = {}, [], 0
        for item in chosen:
            try: outcome = self.launch(ctx, item, settings)
            except (ValueError, ObjectDoesNotExist): outcome = None
            if outcome is True: ready.append(item['id'])
            elif outcome: queued[str(outcome)] = item['id']
            else: missed += 1
        state = {'endpoints': queued, 'ready': ready, 'missed': missed, 'left_out': left_out, 'dropped': dropped}
        if not queued: return self.finish(state, {}, items)
        return Waiting(list(queued), state)

    def resume(self, ctx, items, settings, state, runs):
        return self.finish(state, runs, items)

    def results(self, state, good, runs):
        """The endpoint ids this step passes on. By default: the results the job succeeded for."""
        return state['ready'] + good

    def finish(self, state, runs, items=()):
        good = [state['endpoints'][key] for key, run in runs.items() if run.state == 'succeeded' and key in state['endpoints']]
        wanted = list(dict.fromkeys(self.results(state, good, runs)))
        if items and items[0].get('kind') == 'domain': found, dropped = [item for item in items if item['id'] in set(wanted)], 0
        else: found, dropped = endpoint_items(wanted)
        note = str(len(good) + len(state['ready'])) + ' ' + self.done_word
        if state['ready']: note += ' (' + str(len(state['ready'])) + ' already saved)'
        if state['missed']: note += ' · ' + str(state['missed']) + ' could not run'
        if len(runs) - len(good): note += ' · ' + str(len(runs) - len(good)) + ' failed'
        spent = len(state['endpoints']) if self.spends else 0
        return Done(found, note=note + gone(dropped + state.get('dropped', 0)), left_out=state['left_out'], **({self.spends: spent} if self.spends else {}))


@register
class NetlasEnrich(PerResultJob):
    key, name, group, reach, spends, done_word = 'netlas_enrich', 'Netlas enrichment', 'enrich', 'credits', 'pages', 'enriched'
    says = 'Asks Netlas what it holds for each result: host, DNS and certificates. One provider request set per result.'
    fields = (Field('collections', 'Collections', 'choice', default='all', choices=(('all', 'Host, DNS and certificates'), ('host', 'Host only'))),)

    def launch(self, ctx, item, settings):
        from leakscope.phase3.jobs import enrich_endpoint
        from leakscope.services.runs import enqueue
        collections = ['host', 'dns', 'certificates'] if settings['collections'] == 'all' else ['host']
        values = {'endpoint_id': item['id'], 'collections': collections}
        return enqueue(enrich_endpoint, kind='enrich', type=item['fields']['technology'], provider='netlas', parameters=values, kwargs=values).pk


class AssetLookup(PerResultJob):
    """Public lookups from the Names, DNS and netblock tool. Saved evidence is reused while it is still valid."""
    group, reach = 'enrich', 'public'
    kind, done_word = '', 'looked up'

    def target(self, item):
        return item['fields']['address']

    def launch(self, ctx, item, settings):
        from leakscope.phase3.asset_jobs import queue_enrichment
        target = self.target(item)
        if not target: return None
        outcome = queue_enrichment(self.kind, target)
        return True if outcome.get('cached') else outcome['run']['id']


@register
class NamesForAddress(AssetLookup):
    key, name, kind = 'names_for_address', 'Names for the address', 'resolve'
    says = 'Reverse lookup and names found in saved evidence, for each IP address.'

    def target(self, item):
        return item['fields']['ip']


@register
class NetworkOwner(AssetLookup):
    key, name, kind = 'network_owner', 'Network owner', 'netblock'
    says = 'Registry record for the network each IP address belongs to.'

    def target(self, item):
        return item['fields']['ip']


@register
class DnsRecords(AssetLookup):
    key, name, kind = 'dns_records', 'DNS records', 'dns'
    says = 'A, AAAA, CNAME, MX, NS, TXT and more, for each domain, or each result that is a hostname.'

    takes, gives = ('endpoint', 'domain'), 'same'

    def target(self, item):
        return item['fields'].get('hostname') or item['fields'].get('domain')


@register
class DomainRegistration(AssetLookup):
    key, name, kind = 'domain_registration', 'Domain registration', 'registration'
    says = 'Registry record for each domain, or each result that is a hostname.'
    takes, gives = ('endpoint', 'domain'), 'same'

    def target(self, item):
        return item['fields'].get('hostname') or item['fields'].get('domain')


@register
class TenantLookup(AssetLookup):
    key, name, kind = 'tenant_lookup', 'Microsoft tenant lookup', 'identity_metadata'
    says = 'Asks Microsoft’s public sign-in metadata whether each domain belongs to an Entra tenant, as Organizations does. One request per domain.'
    takes, gives = ('endpoint', 'domain'), 'same'

    def target(self, item):
        return item['fields'].get('hostname') or item['fields'].get('domain')

    def launch(self, ctx, item, settings):
        from leakscope.phase3.asset_jobs import queue_enrichment
        target = self.target(item)
        if not target: return None
        outcome = queue_enrichment(self.kind, target, max_requests=1)
        return True if outcome.get('cached') else outcome['run']['id']


@register
class RelatedServices(PerResultJob):
    key, name, group, reach, spends, done_word = 'related_services', 'Related services on the host', 'find', 'credits', 'pages', 'hosts searched'
    says = 'Asks a provider what else it indexed on the same address, and passes those services on.'
    fields = (Field('provider', 'Provider', 'choice', required=True, choices=(('shodan', 'Shodan'), ('zoomeye', 'ZoomEye'), ('netlas', 'Netlas'))),
              Field('max_results', 'At most per host', 'number', default=100, minimum=1, maximum=100))

    def cost(self, settings, items_in):
        return Cost(pages=items_in, items_out=items_in * settings['max_results'])

    def launch(self, ctx, item, settings):
        from leakscope.phase3.ics_jobs import queue_related
        return queue_related(ServiceEndpoint.objects.get(pk=item['id']), settings['provider'], 'passive', settings['max_results']).pk

    def results(self, state, good, runs):
        import json
        found = []
        for key, run in runs.items():
            if run.state != 'succeeded': continue
            try: found += json.loads(run.result or '{}').get('endpoint_ids') or []
            except ValueError: continue
        return found


@register
class Location(Block):
    key, name, group, reach, takes, gives = 'location', 'Location', 'enrich', 'local', 'endpoint', 'endpoint'
    says = 'Adds country and city from the local location database, usable as {{country}} and in conditions.'
    output_fields = ENDPOINT_FIELDS

    adds_fixed = ('country', 'country_code', 'city')

    def begin(self, ctx, items, settings, allowance):
        from leakscope.phase3.geography import lookup_ips
        items, dropped = refresh(items)
        addresses = [item['fields']['ip'] for item in items if item['fields']['ip']]
        try: found = lookup_ips(addresses[:5000]) if addresses else {}
        except ValueError: found = {}
        located = 0
        for item in items:
            record = found.get(item['fields']['ip']) or {}
            item['fields'].update(country=record.get('country_name') or '', country_code=record.get('country_code') or '', city=record.get('city') or '')
            located += bool(record.get('country_code'))
        return Done(items, note=str(located) + ' located · ' + str(len(items) - located) + ' not in the local database' + gone(dropped))


@register
class CloudNetwork(Block):
    key, name, group, reach, takes, gives = 'cloud_network', 'Cloud network', 'enrich', 'local', 'endpoint', 'endpoint'
    says = 'Adds the cloud provider, service and region for addresses inside a published cloud range, usable as {{cloud_provider}} and in conditions.'
    output_fields = ENDPOINT_FIELDS
    adds_fixed = ('cloud_provider', 'cloud_service', 'cloud_region')

    def begin(self, ctx, items, settings, allowance):
        from leakscope.phase3 import cloud_ranges
        items, dropped = refresh(items)
        addresses = list(dict.fromkeys(item['fields']['ip'] for item in items if item['fields']['ip']))
        found = {}
        # The lookup takes 2,000 addresses at a time.
        for start in range(0, len(addresses), 2000):
            for row in cloud_ranges.lookup(addresses[start:start + 2000]): found.setdefault(row['ip'], row)
        inside = 0
        for item in items:
            row = found.get(item['fields']['ip']) or {}
            item['fields'].update(cloud_provider=row.get('provider_label') or '', cloud_service=', '.join(sorted(row.get('services') or [])), cloud_region=', '.join(row.get('regions') or []))
            inside += bool(row)
        return Done(items, note=str(inside) + ' in a published cloud range · ' + str(len(items) - inside) + ' not' + gone(dropped))
