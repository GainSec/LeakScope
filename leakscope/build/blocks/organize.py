from leakscope.build.kinds import refresh, gone, render
from leakscope.phase3.state import set_tags
from .base import Block, BlockError, Field, Cost, Done, Waiting, register
from leakscope.build.kinds import ENDPOINT_FIELDS, DOMAIN_FIELDS, domain_item


@register
class Tag(Block):
    key, name, group, reach, takes, gives = 'tag', 'Tag', 'organize', 'local', 'endpoint', 'endpoint'
    says = 'Adds tags to every result that reaches this step.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('tags', 'Tags', 'tags', required=True, template=True),)

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)
        # Each step has its own origin: manual tags, other sources' tags and other Tag steps are left alone.
        origin = ctx.origin + (':' + ctx.step_id if ctx.step_id else '')
        for item in items:
            # A tag built from a value that is empty for this result is left out.
            tags = [tag for tag in (render(name, item) for name in settings['tags']) if tag]
            set_tags('endpoint', item['id'], sorted({tag[:64] for tag in tags}), origin=origin)
        items, _ = refresh(items)
        return Done(items, note=str(len(items)) + ' tagged' + gone(dropped))


@register
class RemoveTags(Block):
    key, name, group, reach, takes, gives = 'remove_tags', 'Remove tags', 'organize', 'local', 'endpoint', 'endpoint'
    says = 'Takes the named tags off every result that reaches this step, whoever added them. Other tags stay.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('tags', 'Tags to remove', 'tags', required=True, template=True),)

    def begin(self, ctx, items, settings, allowance):
        from leakscope_app.models import TagAssignment
        items, dropped = refresh(items)
        changed = 0
        for item in items:
            names = {tag.strip().casefold()[:64] for tag in (render(name, item) for name in settings['tags']) if tag.strip()}
            removed, _ = TagAssignment.objects.filter(target_kind='endpoint', target_key=str(item['id']), tag__name__in=names).delete()
            changed += bool(removed)
        total = len(items)
        items, _ = refresh(items)
        return Done(items, note='Removed from ' + str(changed) + ' of ' + str(total) + gone(dropped))


ORGANIZATION = (Field('organization', 'Organization', 'choice', options='organizations'),
                Field('new_name', 'Or a new organization named', 'text', help='Used when no organization is chosen. An organization with this name is reused.'))


def _organization(settings, lock=False):
    """The chosen organization, or the one named (created when it does not exist yet). Call inside a transaction when locking."""
    from django.core.exceptions import ObjectDoesNotExist
    from leakscope_app import relationships
    if settings.get('organization'):
        try: return relationships.lifecycle.organization(settings['organization'], active=True, lock=lock)
        except (ValueError, ObjectDoesNotExist): raise BlockError('The chosen organization is not active') from None
    from leakscope.phase3.asset_jobs import _spec
    try: name = _spec('organization', settings['new_name'][:200], aliases=[])['target']
    except ValueError as exc: raise BlockError(str(exc)) from None
    root = relationships.store.node('organization', name, {'aliases': []})
    if root.organization_status != 'active': raise BlockError('Organization “' + name + '” is ' + root.organization_status + '; restore it first')
    return root


class OrganizationBlock(Block):
    fields = ORGANIZATION

    def clean(self, settings):
        cleaned = super().clean(settings)
        if not cleaned['organization'] and not cleaned['new_name']: raise ValueError('Choose an organization or name a new one')
        return cleaned


@register
class AddToOrganization(OrganizationBlock):
    key, name, group, reach, takes, gives = 'add_to_organization', 'Add to organization', 'organize', 'local', 'endpoint', 'endpoint'
    says = 'Files every result under an organization, as the Observations page does. It can create the organization.'
    output_fields = ENDPOINT_FIELDS

    def begin(self, ctx, items, settings, allowance):
        from django.core.exceptions import ObjectDoesNotExist
        from django.db import transaction
        from leakscope_app import relationships
        items, dropped = refresh(items)
        added = 0
        with transaction.atomic():
            root = _organization(settings, lock=True)
            for item in items:
                reference = {'type': 'endpoint', 'id': item['id']}
                try: added += bool(relationships._link_scope(root, relationships._reference_node(reference), reference, 'workflow'))
                except ObjectDoesNotExist: dropped += 1
        return Done(items, note=str(added) + ' added · ' + str(len(items) - added) + ' already there' + gone(dropped))


@register
class AddDomainsToOrganization(OrganizationBlock):
    key, name, group, reach, takes, gives = 'add_domains_to_organization', 'Add domains to organization', 'organize', 'local', 'domain', 'same'
    says = 'Adds every domain to an organization’s scope, as the Organizations page does. It can create the organization.'
    output_fields = DOMAIN_FIELDS

    def begin(self, ctx, items, settings, allowance):
        from django.db import transaction
        from leakscope_app import relationships
        added = already = bad = 0
        with transaction.atomic():
            root = _organization(settings, lock=True)
            for name in dict.fromkeys(item['id'] for item in items):
                try: value = relationships._scope_domain(name)
                except ValueError: bad += 1; continue
                if relationships._link_scope(root, relationships.store.node('domain', value), None, 'workflow'): added += 1
                else: already += 1
        note = str(added) + ' added · ' + str(already) + ' already there' + (' · ' + str(bad) + ' could not be added (not a registrable domain)' if bad else '')
        return Done(items, note=note)


@register
class OrganizationDomains(Block):
    key, name, group, reach, start, gives = 'organization_domains', 'Domains of an organization', 'trigger', 'local', True, 'domain'
    says = 'Starts with the domains in an organization’s scope.'
    output_fields = DOMAIN_FIELDS
    fields = (Field('organization', 'Organization', 'choice', required=True, options='organizations'), Field('limit', 'At most', 'number', default=100, minimum=1, maximum=5000))

    def cost(self, settings, items_in):
        return Cost(items_out=settings['limit'])

    def begin(self, ctx, items, settings, allowance):
        from leakscope_app import relationships
        root = _organization(settings)
        names = [row['name'] for row in relationships._organization_detail(root)['domains'] if not row['name'].startswith('*.')]
        added = {}
        for name, seen in root.outgoing.filter(relation='user_scope', target__kind='domain').order_by('-first_seen').values_list('target__value', 'first_seen'): added[name] = seen.isoformat()
        found = [domain_item(name, source='organization', added=added.get(name)) for name in names[:settings['limit']]]
        return Done(found, note=str(len(found)) + ' domain' + ('' if len(found) == 1 else 's') + ' in ' + root.value)


@register
class DiscoverOrganization(Block):
    key, name, group, reach, takes, gives = 'discover_organization', 'Discover an organization', 'find', 'public', 'any', 'same'
    says = ('Searches public registry records (ARIN and RIPE) for an organization’s networks, as its Discover button does, and saves them to it as candidates. '
            'Registries cannot list an organization’s domains: use Expand domains for those.')
    NETLAS_REQUESTS = 2
    fields = (Field('organization', 'Organization', 'choice', required=True, options='organizations'),
              Field('include_netlas', 'Also ask Netlas', 'flag', default=False, help='Up to 2 Netlas requests, which can also return domains. Spends Netlas credits.'))

    def reaches(self, settings):
        return ('public', 'credits') if settings.get('include_netlas') else ('public',)

    def cost(self, settings, items_in):
        return Cost(pages=self.NETLAS_REQUESTS if settings.get('include_netlas') else 0)

    @staticmethod
    def _found(result, name):
        """What a discovery saved, in words: counts per kind, and whether it stopped short."""
        result = result if isinstance(result, dict) else {}
        def count(key, field):
            return len({str(next((row[name] for name in field.split('|') if row.get(name)), '')) for row in result.get(key) or [] if isinstance(row, dict)} - {''})
        parts = [str(number) + ' ' + (one if number == 1 else many) for number, one, many in
                 ((count('networks', 'cidr'), 'network', 'networks'), (count('asns', 'asn'), 'ASN', 'ASNs'), (count('domains', 'domain|name'), 'domain', 'domains')) if number]
        exact = sum(1 for key in ('networks', 'asns', 'domains') for row in result.get(key) or [] if isinstance(row, dict) and row.get('matched_organization'))
        note = (', '.join(parts) + ' saved to ' + name + ' as candidates') if parts else 'Nothing found for ' + name
        if parts: note += ' · ' + (str(exact) + ' registered under that exact name' if exact else 'none registered under that exact name')
        if result.get('partial'): note += ' · stopped at its request budget, so this is not everything'
        return note

    def begin(self, ctx, items, settings, allowance):
        from leakscope.phase3 import asset_jobs
        root = _organization(settings)
        paid = bool(settings['include_netlas']) and allowance.get('pages', 0) > 0
        try: outcome = asset_jobs.queue_enrichment('organization', root.value, organization_id=str(root.pk), max_requests=min(self.NETLAS_REQUESTS, allowance.get('pages', 0)) if paid else 2,
                                                   aliases=root.attributes.get('aliases', []), include_netlas=paid)
        except ValueError as exc: raise BlockError(str(exc)) from None
        skipped = ' · Netlas left out: no pages left in this run' if settings['include_netlas'] and not paid else ''
        if outcome.get('cached'): return Done(items, note='Still current, not asked again: ' + self._found(outcome.get('result'), root.value) + skipped)
        return Waiting([outcome['run']['id']], {'organization': root.value, 'skipped': skipped})

    def resume(self, ctx, items, settings, state, runs):
        import json
        child = next(iter(runs.values()))
        if child.state != 'succeeded': raise BlockError(child.error or 'Organization discovery did not finish (' + child.state + ')')
        try: result = json.loads(child.result or '{}') if isinstance(child.result, str) else child.result
        except ValueError: result = {}
        result = result if isinstance(result, dict) else {}
        return Done(items, pages=int(result.get('netlas_requests') or 0), note=self._found(result, state.get('organization', 'the organization')) + state.get('skipped', ''))
