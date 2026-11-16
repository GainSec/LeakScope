"""Domains and certificates: starts from saved names, conversion to and from results, expansion and watches."""
from datetime import timedelta
from django.db.models import Q
from django.utils import timezone
from leakscope.build import conditions
from leakscope.build.kinds import DOMAIN_FIELDS, ENDPOINT_FIELDS, domain_item, endpoint_items, refresh, gone
from leakscope.phase3.state import visible_endpoints
from .base import Block, BlockError, Field, Cost, Done, Waiting, register


def _unique(items):
    seen, kept = set(), []
    for item in items:
        if item['id'] and item['id'] not in seen:
            seen.add(item['id']); kept.append(item)
    return kept


@register
class SavedDomains(Block):
    key, name, group, reach, start, gives = 'saved_domains', 'Saved domains that match', 'trigger', 'local', True, 'domain'
    says = 'Starts with domains found by domain expansion: by text, review status, or any field condition.'
    output_fields = DOMAIN_FIELDS
    fields = (Field('search', 'Domain contains', 'text'),
              Field('status', 'Review status', 'choice', default='', choices=(('', 'Any'), ('accepted', 'Accepted'), ('pending', 'Pending'), ('rejected', 'Rejected'))),
              Field('conditions', 'Only when', 'conditions'), Field('limit', 'At most', 'number', default=100, minimum=1, maximum=5000))

    def cost(self, settings, items_in):
        return Cost(items_out=settings['limit'])

    def begin(self, ctx, items, settings, allowance):
        from leakscope_app.models import DomainCandidate
        rows = DomainCandidate.objects.all()
        if settings['search']: rows = rows.filter(domain__icontains=settings['search'])
        if settings['status']: rows = rows.filter(status=settings['status'])
        rows = list(rows.order_by('-updated_at')[:20000])
        first = {}
        for row in rows: first[row.domain] = min(first.get(row.domain, row.created_at), row.created_at)
        found = _unique(domain_item(row.domain, source='domain expansion', status=row.status, confidence=row.confidence, category=row.category,
                                    relation=row.relation, dns_status=row.dns_status, added=first[row.domain].isoformat()) for row in rows)
        if settings['conditions']: found = [item for item in found if conditions.matches(item, settings['conditions'])]
        found = found[:settings['limit']]
        return Done(found, note=str(len(found)) + ' saved domain' + ('' if len(found) == 1 else 's'))


@register
class CertificateNames(Block):
    key, name, group, reach, start, gives = 'certificate_names', 'Names on saved certificates', 'trigger', 'local', True, 'domain'
    says = 'Starts with names from certificates LeakScope has saved, such as watch matches from the certificate stream.'
    output_fields = DOMAIN_FIELDS
    fields = (Field('search', 'Name contains', 'text'), Field('seen_days', 'Seen in the last (days)', 'number', minimum=1, maximum=3650),
              Field('matches', 'Certificates', 'choice', default='watched', choices=(('watched', 'Only ones that matched a watch'), ('all', 'All saved certificates'))),
              Field('limit', 'At most', 'number', default=100, minimum=1, maximum=5000))

    def cost(self, settings, items_in):
        return Cost(items_out=settings['limit'])

    def begin(self, ctx, items, settings, allowance):
        from leakscope_app.models import CertificateName
        rows = CertificateName.objects.exclude(name__startswith='*.')
        if settings['search']: rows = rows.filter(name__icontains=settings['search'])
        if settings['seen_days']: rows = rows.filter(certificate__last_seen__gte=timezone.now() - timedelta(days=settings['seen_days']))
        if settings['matches'] == 'watched': rows = rows.filter(certificate__watch_matches__isnull=False)
        rows = rows.order_by('-certificate__last_seen').values_list('name', 'certificate__issuer', 'certificate__not_after', 'certificate__first_seen')[:20000]
        found = _unique(domain_item(name, source='certificate', issuer=(issuer or {}).get('O') or (issuer or {}).get('CN') if isinstance(issuer, dict) else '',
                                    expires=expires.date().isoformat() if expires else '', added=seen.isoformat() if seen else '') for name, issuer, expires, seen in rows)[:settings['limit']]
        return Done(found, note=str(len(found)) + ' certificate name' + ('' if len(found) == 1 else 's'))


@register
class NamesOfResults(Block):
    key, name, group, reach, takes, gives = 'names_of_results', 'Names of each result', 'data', 'local', 'endpoint', 'domain'
    says = 'Turns results into the domain names saved for them, so domain blocks can follow.'
    output_fields = DOMAIN_FIELDS

    def cost(self, settings, items_in):
        return Cost(items_out=items_in * 50)

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)
        found = _unique(domain_item(name, source='saved result') for item in items for name in item['fields'].get('names') or [] if '.' in name and not name.startswith('*.'))
        return Done(found, note=str(len(found)) + ' names from ' + str(len(items)) + ' results' + gone(dropped))


@register
class ResultsForDomains(Block):
    key, name, group, reach, takes, gives = 'results_for_domains', 'Saved results for each domain', 'data', 'local', 'domain', 'endpoint'
    says = 'Turns domains into the observations already saved at those names, so result blocks can follow.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('subdomains', 'Include', 'choice', default='exact', choices=(('exact', 'Only the exact name'), ('subdomains', 'The name and its subdomains'))),)

    def cost(self, settings, items_in):
        return Cost(items_out=5000)

    def begin(self, ctx, items, settings, allowance):
        names = [item['id'] for item in items][:2000]
        match = Q(address__in=names)
        if settings['subdomains'] == 'subdomains':
            for name in names[:200]: match |= Q(address__iendswith='.' + name)
        found, _ = endpoint_items(list(visible_endpoints().filter(match).order_by('-last_seen').values_list('pk', flat=True)[:5000]))
        return Done(found, note=str(len(found)) + ' saved results at ' + str(len(names)) + ' domains')


@register
class CreateWatch(Block):
    key, name, group, reach, takes, gives = 'create_watch', 'Watch in the certificate stream', 'organize', 'local', 'domain', 'same'
    says = 'Creates or updates a certificate-stream watch for these domains, as the Monitoring page does.'
    output_fields = DOMAIN_FIELDS
    fields = (Field('name', 'Watch name', 'text', required=True), Field('enabled', 'Start watching', 'choice', default='yes', choices=(('yes', 'Yes'), ('no', 'Save it paused'))))

    def begin(self, ctx, items, settings, allowance):
        from leakscope_app.models import WatchRule
        from leakscope_app.operations import _save_watch
        # A watch holds at most 100 names; the rest are reported, not silently dropped.
        names, left_out = [item['id'] for item in items][:100], max(0, len(items) - 100)
        if not names: return Done(items, note='No domains to watch')
        row = WatchRule.objects.filter(name=settings['name']).first()
        try: _save_watch({'name': settings['name'], 'domains': names, 'keywords': [], 'enabled': settings['enabled'] == 'yes', 'auto_fetch': False}, row)
        except ValueError as exc: raise BlockError(str(exc)) from None
        return Done(items, note=('Updated' if row else 'Created') + ' watch “' + settings['name'] + '” with ' + str(len(names)) + ' domains', left_out=left_out)


@register
class ExpandDomains(Block):
    key, name, group, reach, takes, gives = 'expand_domains', 'Expand domains', 'find', 'public', 'any', 'domain'
    says = ('Finds related domains and subdomains from certificate logs, passive DNS and saved data, as Domain expansion does. '
            'Give it domains, or choose an organization to start from its name, aliases and saved domains and networks. It can take several minutes.')
    output_fields = DOMAIN_FIELDS
    # Requests each paid source may make in one run. Both together fit the default ceiling of 5 pages.
    WEB_SEARCHES, NETLAS_REQUESTS = 3, 2
    fields = (Field('organization', 'Start from organization', 'choice', options='organizations', help='Uses its name, aliases and saved domains, addresses and networks, and saves what it finds to it.'),
              Field('archives', 'Also search web archives', 'flag', default=False, help='Common Crawl and the Wayback Machine. Free, but slower.'),
              Field('web_search', 'Also search the web', 'flag', default=False, help='Up to 3 web searches for the organization’s sites. Spends web-search credits.'),
              Field('netlas', 'Also ask Netlas', 'flag', default=False, help='Up to 2 Netlas requests. Spends Netlas credits.'),
              Field('providers', 'Also search for its hosts at', 'choice', default='', choices=(('', 'No provider'), ('shodan', 'Shodan'), ('zoomeye', 'ZoomEye'), ('netlas', 'Netlas'), ('all', 'Shodan, ZoomEye and Netlas')),
                    help='Searches for the organization’s name in certificates and for host names under its first domain, and passes on the names found. One page per search: up to 2 per provider.'),
              Field('keep', 'Pass on', 'choice', default='all', choices=(('all', 'Every candidate found'), ('strong', 'Only strong candidates'))))

    def _paid(self, settings):
        return (self.WEB_SEARCHES if settings.get('web_search') else 0), (self.NETLAS_REQUESTS if settings.get('netlas') else 0)

    @staticmethod
    def _providers(settings):
        chosen = settings.get('providers') or ''
        return ('shodan', 'zoomeye', 'netlas') if chosen == 'all' else ((chosen,) if chosen else ())

    def reaches(self, settings):
        return ('public', 'credits') if any(self._paid(settings)) or self._providers(settings) else ('public',)

    def cost(self, settings, items_in):
        return Cost(pages=sum(self._paid(settings)) + 2 * len(self._providers(settings)), items_out=5000)

    def _searches(self, ctx, settings, names, room):
        """Queue the provider searches for the organization's name and first domain. Returns {run id: details} and notes."""
        from leakscope.intelligence.queries import discovery_queries
        from leakscope.phase3.asset_names import registrable_domain
        from .base import get
        organization, domain = '', ''
        if settings['organization']:
            from .organize import _organization
            root = _organization(settings)
            organization = root.value
            if not names:
                names = list(root.outgoing.filter(relation='user_scope', target__kind='domain').values_list('target__value', flat=True)[:1])
        if names:
            try: domain = registrable_domain(names[0])
            except (ValueError, TypeError): domain = ''
        queued, notes, search = {}, [], get('search')
        for provider in self._providers(settings):
            try: queries = discovery_queries(provider, organization, domain)
            except ValueError as exc: notes.append(str(exc)); continue
            for kind, query in queries:
                if room <= 0: notes.append('some provider searches left out: no pages left in this run'); break
                outcome = search.begin(ctx, [], {'provider': provider, 'query': query, 'start': 'beginning', 'pages': 1, 'page_size': 20}, {'pages': 1})
                if not isinstance(outcome, Waiting): notes.append(provider + ': ' + (outcome.note or 'search not run')); continue
                for run_id in outcome.run_ids: queued[run_id] = {'provider': provider, 'kind': kind, 'query': query, 'root': domain if kind == 'domain' else ''}
                room -= 1
        return queued, list(dict.fromkeys(notes))

    def begin(self, ctx, items, settings, allowance):
        from leakscope.phase3.domain_expansion import DEFAULT_SOURCES, start
        domains = [item['id'] for item in items if item['kind'] == 'domain']
        names, left_out = domains[:1000], max(0, len(domains) - 1000)
        if not names and not settings['organization']: return Done([], note='No domains to expand: give it domains or choose an organization')
        # The sources are always named here: left to itself, an expansion from a name alone would search the web and spend credits.
        sources, room = list(DEFAULT_SOURCES), allowance.get('pages', 0)
        web, netlas = (min(wanted, room) for wanted in self._paid(settings))
        netlas = min(netlas, max(0, room - web))
        skipped = [label for label, wanted, got in (('web search', settings['web_search'], web), ('Netlas', settings['netlas'], netlas)) if wanted and not got]
        if settings['archives']: sources += ['commoncrawl', 'wayback']
        if web: sources.append('web')
        if netlas: sources.append('netlas')
        data = {'domains': names, 'sources': sources, 'budgets': {'web': web, 'netlas': netlas}}
        if settings['organization']: data['organization_id'] = settings['organization']
        try: expansion = start(data)
        except ValueError as exc: raise BlockError(str(exc)) from None
        searches, notes = self._searches(ctx, settings, names, room - web - netlas) if self._providers(settings) else ({}, [])
        return Waiting([str(expansion.run_id), *searches], {'expansion': str(expansion.pk), 'run': str(expansion.run_id), 'seeds': len(names), 'left_out': left_out, 'pages': web + netlas,
                                                            'skipped': skipped, 'organization': bool(settings['organization']), 'searches': searches, 'search_notes': notes})

    def resume(self, ctx, items, settings, state, runs):
        from leakscope_app.models import DomainCandidate
        child = runs.get(state.get('run')) or next(iter(runs.values()))
        if child.state == 'failed': raise BlockError(child.error or 'Domain expansion failed')
        rows = DomainCandidate.objects.filter(expansion_id=state['expansion'])
        total = rows.count()
        if settings['keep'] == 'strong': rows = rows.filter(confidence__gte=75)
        found = _unique(domain_item(row.domain, source='domain expansion', status=row.status, confidence=row.confidence, category=row.category,
                                    relation=row.relation, dns_status=row.dns_status, added=row.created_at.isoformat()) for row in rows.order_by('-confidence')[:5000])
        origin = ' and '.join(part for part in ('the organization' if state.get('organization') else '', str(state['seeds']) + ' domains' if state['seeds'] or not state.get('organization') else '') if part)
        note = (str(len(found)) + ' strong of ' + str(total) + ' candidates' if settings['keep'] == 'strong' else str(len(found)) + ' candidates') + ' from ' + origin
        if not total and not state['seeds'] and not state.get('pages'):
            note += ' · the free sources need at least one domain to work from: give this step a domain, save one to the organization, or turn on web search or Netlas'
        if state.get('skipped'): note += ' · ' + ' and '.join(state['skipped']) + ' left out: no pages left in this run'
        if child.state == 'paused': note += ' · stopped at its own budget; resume it in Domain expansion'
        pages, extra = state.get('pages', 0), []
        if state.get('searches'):
            extra, used, parts = self._names(state['searches'], runs)
            pages += used
            known = {item['id'] for item in found}
            extra = [item for item in extra if item['id'] not in known]
            note += ' · provider searches: ' + ', '.join(parts) + ' (' + str(len(extra)) + ' more names)'
        for line in state.get('search_notes') or []: note += ' · ' + line
        return Done(found + extra, pages=pages, note=note, left_out=state.get('left_out', 0))

    @staticmethod
    def _names(searches, runs):
        """Host names from what the provider searches saved: under the domain for a domain search, every name for a name search."""
        import json
        from leakscope.build.kinds import endpoint_items
        found, pages, parts = [], 0, []
        for run_id, detail in searches.items():
            child = runs.get(run_id)
            if child is None or child.state != 'succeeded':
                parts.append(detail['provider'] + ' ' + detail['kind'] + ' search failed'); continue
            try: result = json.loads(child.result or '{}')
            except ValueError: result = {}
            pages += int(result.get('pages') or 0)
            items, _ = endpoint_items(list(dict.fromkeys(result.get('endpoint_ids') or [])))
            root = detail.get('root') or ''
            names = [name.lower() for item in items for name in item['fields'].get('names') or [] if '.' in name and not name.startswith('*.')]
            if root: names = [name for name in names if name == root or name.endswith('.' + root)]
            names = list(dict.fromkeys(names))
            parts.append(detail['provider'] + ' ' + detail['kind'] + ' ' + str(len(names)) + ' names from ' + str(len(items)) + ' results')
            found += [domain_item(name, source=detail['provider'] + ' search', relation='subdomain' if root else 'related') for name in names]
        return _unique(found), pages, parts


@register
class ReviewDomains(Block):
    key, name, group, reach, takes, gives = 'review_domains', 'Accept or reject domains', 'organize', 'local', 'domain', 'same'
    says = 'Records a review decision for domains found by domain expansion, as its review table does.'
    output_fields = DOMAIN_FIELDS
    fields = (Field('action', 'Decision', 'choice', required=True, choices=(('accept', 'Accept'), ('reject', 'Reject'))),
              Field('organization', 'Accept into', 'choice', options='organizations', help='Needed to accept domains from an expansion that has no organization.'))

    def begin(self, ctx, items, settings, allowance):
        from leakscope.phase3 import domain_expansion
        from leakscope_app.models import DomainCandidate
        names = [item['id'] for item in items][:5000]
        newest = {}
        for pk, domain, expansion in DomainCandidate.objects.filter(domain__in=names).order_by('-updated_at').values_list('pk', 'domain', 'expansion_id'):
            newest.setdefault(domain, (str(pk), expansion))
        groups = {}
        for domain, (candidate, expansion) in newest.items(): groups.setdefault(expansion, []).append((candidate, domain))
        word = 'accepted' if settings['action'] == 'accept' else 'rejected'
        done, failed, reason = set(), 0, ''
        for expansion, rows in groups.items():
            for index in range(0, len(rows), 500):
                part = rows[index:index + 500]
                data = {'action': settings['action'], 'candidate_ids': [candidate for candidate, _ in part]}
                if settings['organization']: data['organization_id'] = settings['organization']
                try: domain_expansion.review(expansion, data)
                except ValueError as exc: failed += len(part); reason = reason or str(exc)
                else: done.update(domain for _, domain in part)
        for item in items:
            if item['id'] in done: item['fields']['status'] = word
            else: item['fields'].setdefault('status', '')
        note = str(len(done)) + ' ' + word
        if len(names) - len(newest): note += ' · ' + str(len(names) - len(newest)) + ' not found by domain expansion'
        if failed: note += ' · ' + str(failed) + ' could not be reviewed: ' + reason
        return Done(items, note=note, left_out=max(0, len(items) - 5000))



@register
class HasDnsRecords(Block):
    key, name, group, reach, takes, gives = 'has_dns_records', 'Has saved DNS records', 'logic', 'local', 'domain', 'domain'
    says = 'Keeps the domains that have saved DNS records, or the ones that do not, as Domain coverage shows. Use it to look up only the gaps.'
    output_fields = DOMAIN_FIELDS
    fields = (Field('keep', 'Keep', 'choice', default='without', choices=(('without', 'Domains without saved DNS records'), ('with', 'Domains with saved DNS records'))),)

    def begin(self, ctx, items, settings, allowance):
        from leakscope.phase3 import relationships as store
        names = [item['id'] for item in items]
        covered = set()
        for start in range(0, len(names), 500):
            nodes = store._visible_nodes().filter(kind='domain', value__in=names[start:start + 500])
            covered |= set(store.visible_evidence().filter(source='dns', subject_id__in=nodes.values('pk')).values_list('subject__value', flat=True))
        kept = [item for item in items if (item['id'] in covered) == (settings['keep'] == 'with')]
        return Done(kept, note=str(len(covered & set(names))) + ' with saved DNS records · ' + str(len(set(names) - covered)) + ' without')
