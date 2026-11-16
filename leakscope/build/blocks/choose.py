"""Drill down: start from addresses or domains chosen by hand, or keep only chosen ones part-way through."""
import ipaddress
import re
from django.db.models.functions import Lower
from leakscope.build.kinds import DOMAIN_FIELDS, ENDPOINT_FIELDS, domain_item, endpoint_items, refresh, gone
from leakscope.phase3.asset_names import normalize_domain
from leakscope.phase3.state import visible_endpoints
from .base import Block, Field, Cost, Done, register

_PORT = re.compile(r'^(?:\[(?P<v6>[0-9a-fA-F:.]+)\]|(?P<host>[^:\[\]]+)):(?P<port>\d{1,5})$')


def _ip(value):
    try: return str(ipaddress.ip_address(value))
    except ValueError: return ''


def _named(names, limit=5):
    return ', '.join(names[:limit]) + (' and ' + str(len(names) - limit) + ' more' if len(names) > limit else '')


@register
class ChosenResults(Block):
    key, name, group, reach, start, gives = 'chosen_results', 'Chosen saved results', 'trigger', 'local', True, 'endpoint'
    says = 'Starts with the saved results at exactly the addresses you list: one IP address or name, or several.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('addresses', 'Addresses', 'list', required=True, suggest='address', help='IP addresses or names, one per line. Add :port to take one service only.'),)

    def cost(self, settings, items_in):
        return Cost(items_out=min(5000, 50 * len(settings['addresses'])))

    def begin(self, ctx, items, settings, allowance):
        wanted = {}                                  # address -> ports asked for (empty: every port)
        for entry in settings['addresses']:
            found = _PORT.match(entry) if not _ip(entry) else None
            address = (found.group('v6') or found.group('host') if found else entry).lower().rstrip('.')
            ports = wanted.setdefault(_ip(address) or address, set())
            if found: ports.add(int(found.group('port')))
            else: ports.add(None)
        rows = visible_endpoints().annotate(low=Lower('address')).filter(low__in=list(wanted)).order_by('address', 'port').values_list('pk', 'low', 'port')[:20000]
        ids, hit = [], set()
        for pk, address, port in rows:
            if None in wanted[address] or port in wanted[address]: ids.append(str(pk)); hit.add(address)
        found, _ = endpoint_items(ids[:5000])
        missing = [address for address in wanted if address not in hit]
        note = str(len(found)) + ' saved result' + ('' if len(found) == 1 else 's') + ' at ' + str(len(hit)) + ' address' + ('' if len(hit) == 1 else 'es')
        if missing: note += ' · nothing is saved for ' + _named(missing)
        return Done(found, note=note, left_out=max(0, len(ids) - 5000))


@register
class ChosenDomains(Block):
    key, name, group, reach, start, gives = 'chosen_domains', 'Chosen domains', 'trigger', 'local', True, 'domain'
    says = 'Starts with exactly the domains you list: one or several, saved or not.'
    output_fields = DOMAIN_FIELDS
    fields = (Field('domains', 'Domains', 'list', required=True, suggest='domain', help='Domain names, one per line.'),)

    def cost(self, settings, items_in):
        return Cost(items_out=len(settings['domains']))

    def begin(self, ctx, items, settings, allowance):
        names, bad = [], []
        for entry in settings['domains']:
            try: name = normalize_domain(entry)
            except ValueError: bad.append(entry); continue
            if name not in names: names.append(name)
        note = str(len(names)) + ' domain' + ('' if len(names) == 1 else 's')
        if bad: note += ' · ' + str(len(bad)) + ' not a domain name: ' + _named(bad)
        return Done([domain_item(name, source='chosen') for name in names], note=note)


@register
class ChosenAddresses(Block):
    key, name, group, reach, start, gives = 'chosen_addresses', 'Chosen addresses', 'trigger', 'local', True, 'value'
    says = 'Starts with IP addresses or names you list, saved or not, to look up or to use as {{ip}}, {{hostname}} or {{address}}.'
    adds_fixed = ('address', 'ip', 'hostname')
    fields = (Field('addresses', 'Addresses', 'list', required=True, help='IP addresses or names, one per line.'),)

    def cost(self, settings, items_in):
        return Cost(items_out=len(settings['addresses']))

    def begin(self, ctx, items, settings, allowance):
        found, bad = {}, []
        for entry in settings['addresses']:
            address = _ip(entry)
            if not address:
                try: address = normalize_domain(entry)
                except ValueError: bad.append(entry); continue
            found.setdefault(address, {'kind': 'value', 'id': address, 'fields': {'address': address, 'ip': _ip(address), 'hostname': '' if _ip(address) else address}})
        note = str(len(found)) + ' address' + ('' if len(found) == 1 else 'es')
        if bad: note += ' · ' + str(len(bad)) + ' not an IP address or name: ' + _named(bad)
        return Done(list(found.values()), note=note)


@register
class OnlyThese(Block):
    key, name, group, reach, takes, gives = 'only_these', 'Only these', 'logic', 'local', ('endpoint', 'domain'), 'same'
    says = 'Keeps only the results or domains you list by address or name, or everything except them.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('values', 'Addresses or names', 'list', required=True, suggest='address', help='IP addresses, host names or domains, one per line.'),
              Field('keep', 'Keep', 'choice', default='these', choices=(('these', 'Only the listed ones'), ('others', 'Everything except the listed ones'))))

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)
        wanted = {(_ip(value) or value).lower().rstrip('.') for value in settings['values']}
        def listed(item):
            fields = item.get('fields') or {}
            held = {str(fields.get(key) or '').lower() for key in ('address', 'ip', 'hostname', 'domain')} | {str(name).lower() for name in fields.get('names') or []}
            return bool(held & wanted)
        kept = [item for item in items if listed(item) == (settings['keep'] == 'these')]
        return Done(kept, note=str(len(kept)) + ' kept · ' + str(len(items) - len(kept)) + ' dropped' + gone(dropped))
