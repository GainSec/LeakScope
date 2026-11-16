import json
import uuid
from leakscope.build.kinds import ENDPOINT_FIELDS, endpoint_items, gone, placeholders, refresh, render
from leakscope.phase3.jobs import queue_lookup, scoped_query, search_progress
from leakscope.phase3.providers import effective_page_size
from leakscope_app.models import SavedLookup
from .base import Block, BlockError, Field, Cost, Done, Waiting, register


@register
class Search(Block):
    """One provider search through the existing lookup job, so provider limits and budgets still apply."""
    key, name, group, reach, takes, gives = 'search', 'Search a provider', 'find', 'credits', 'any', 'endpoint'
    says = 'Runs one Shodan, ZoomEye or Netlas query and saves what comes back.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('provider', 'Provider', 'choice', required=True, choices=(('shodan', 'Shodan'), ('zoomeye', 'ZoomEye'), ('netlas', 'Netlas'))),
              Field('query', 'Query', 'text', required=True, template=True, help='Exact provider query. With a value such as hostname="{{hostname}}" it runs once per result.'),
              Field('start', 'Start at', 'choice', default='continue', choices=(('continue', 'Where the last run stopped (new results only)'), ('beginning', 'The beginning (repeat results is OK)'))),
              Field('pages', 'Pages to fetch', 'number', default=1, minimum=1, maximum=20),
              Field('page_size', 'ZoomEye results per page', 'number', default=20, minimum=1, maximum=100))

    def cost(self, settings, items_in):
        size = effective_page_size(settings['provider'], settings['page_size'])
        times = max(1, items_in) if placeholders(settings['query']) else 1
        return Cost(pages=settings['pages'] * times, items_out=settings['pages'] * size * times)

    def searches(self, items, settings):
        """(query, scope, service type) for each search to run, and how many results could not be used."""
        # A query that uses {{values}} runs once per result (each distinct query once); otherwise it runs once.
        if not placeholders(settings['query']): return [(settings['query'], {}, '')], 0
        rendered = [render(settings['query'], item, quoted=True) for item in items]
        return [(query, {}, '') for query in dict.fromkeys(query for query in rendered if query)], sum(1 for query in rendered if not query)

    def begin(self, ctx, items, settings, allowance):
        provider, size = settings['provider'], effective_page_size(settings['provider'], settings['page_size'])
        searches, unusable = self.searches(items, settings)
        room, queued, notes, skipped, asked = allowance['pages'], {}, [], 0, []
        for query, scope, service in searches:
            # The exact text sent to the provider, kept so the run can show it.
            try: exact = scoped_query(provider, query, scope)
            except ValueError as exc: raise BlockError(str(exc)) from None
            pages, start_page = min(settings['pages'], room), 1
            if pages <= 0: skipped += 1; continue
            if settings['start'] == 'continue':
                progress = search_progress(provider, exact, size)
                if progress and progress.exhausted: notes.append('Earlier runs already fetched every result for this search'); continue
                if progress: start_page = progress.next_page
            # One lookup per workflow, provider and query, reused by every run: its daily run budget then
            # accumulates exactly as it does for a lookup run from its own page, and the list does not fill up.
            key = uuid.uuid5(uuid.NAMESPACE_URL, 'leakscope-workflow-search:' + ctx.workflow_id + ':' + provider + ':' + query + ':' + json.dumps(scope, sort_keys=True))
            lookup, _ = SavedLookup.objects.update_or_create(pk=key, defaults={'name': ('Workflow · ' + ctx.workflow_name)[:200], 'provider': provider,
                'query': query, 'scope': scope, 'service_type': service, 'collection_mode': 'passive', 'auto_fetch': False, 'enabled': False, 'actions': [],
                'page_count': pages, 'start_page': start_page, 'page_size': size, 'max_results': pages * size})
            run = queue_lookup(lookup)
            if run.state == 'skipped': notes.append(run.error or 'The lookup was skipped'); continue
            queued[str(run.pk)] = pages
            asked.append(exact)
            room -= pages
        state = {'requested_pages': settings['pages'], 'queued': queued, 'skipped': skipped, 'unusable': unusable, 'notes': list(dict.fromkeys(notes))[:3], 'asked': asked[:5], 'asked_more': max(0, len(asked) - 5)}
        if not queued: return Done([], note=self._note(state, 0, 0, 0), left_out=skipped)
        return Waiting(list(queued), state)

    @staticmethod
    def _note(state, new, repeat, dropped):
        parts = [str(new) + ' new · ' + str(repeat) + ' captured before'] if state['queued'] else []
        parts += state.get('notes') or []
        short = [pages for pages in state['queued'].values() if pages < state['requested_pages']]
        if short: parts.append('fetched ' + str(short[0]) + ' of ' + str(state['requested_pages']) + ' pages (ceiling)')
        if state.get('skipped'): parts.append(str(state['skipped']) + ' searches not run (page ceiling)')
        if state.get('unusable'): parts.append(str(state['unusable']) + ' results had no value for the query')
        if state.get('asked'): parts.append('Asked: ' + ' | '.join(query[:200] for query in state['asked']) + (' and ' + str(state['asked_more']) + ' more' if state.get('asked_more') else ''))
        return ' · '.join(parts) + gone(dropped)

    def resume(self, ctx, items, settings, state, runs):
        ids, pages, new, repeat = [], 0, 0, 0
        for key, child in runs.items():
            if child.state != 'succeeded': raise BlockError(child.error or 'The search did not finish (' + child.state + ')')
            try: result = json.loads(child.result or '{}')
            except ValueError: result = {}
            found = result.get('endpoint_ids') or []
            ids += found; pages += int(result.get('pages') or 0)
            new += result.get('new_results', len(found)); repeat += result.get('repeat_results', 0)
        found, dropped = endpoint_items(list(dict.fromkeys(ids)))
        short = any(pages < state['requested_pages'] for pages in state['queued'].values())
        return Done(found, pages=pages, note=self._note(state, new, repeat, dropped), left_out=(1 if short else 0) + state.get('skipped', 0))


@register
class SearchService(Search):
    """A catalogue search: the same provider query Discover builds for a service."""
    key, name = 'search_service', 'Search for a service'
    says = 'Runs the Service catalog query for a technology on one provider, as Discover does.'
    fields = (Field('service', 'Service', 'choice', required=True, options='services'),
              Field('provider', 'Provider', 'choice', required=True, choices=(('shodan', 'Shodan'), ('zoomeye', 'ZoomEye'), ('netlas', 'Netlas'))),
              Field('strategy', 'Match by', 'choice', default='fingerprint', choices=(('fingerprint', 'Response fingerprint, any port'), ('ports', 'Reference ports'), ('nonstandard', 'Fingerprint, excluding reference ports'))),
              Field('keyword', 'Also containing', 'text', template=True), Field('country', 'Country code', 'text'), Field('network', 'Network (IP or CIDR)', 'text'),
              Field('start', 'Start at', 'choice', default='continue', choices=(('continue', 'Where the last run stopped (new results only)'), ('beginning', 'The beginning (repeat results is OK)'))),
              Field('pages', 'Pages to fetch', 'number', default=1, minimum=1, maximum=20),
              Field('page_size', 'ZoomEye results per page', 'number', default=20, minimum=1, maximum=100))

    def cost(self, settings, items_in):
        size = effective_page_size(settings['provider'], settings['page_size'])
        times = max(1, items_in) if placeholders(settings['keyword']) else 1
        return Cost(pages=settings['pages'] * times, items_out=settings['pages'] * size * times)

    def searches(self, items, settings):
        from leakscope_app.workbench import _discovery_query
        keywords = [settings['keyword']] if not placeholders(settings['keyword']) else [render(settings['keyword'], item) for item in items]
        found, unusable = [], 0
        for keyword in dict.fromkeys(keywords):
            if keyword is None: unusable += 1; continue
            params = {'keyword': keyword, 'country': settings['country'], 'network': settings['network'], 'exclude': '', 'query_mode': settings['strategy'], 'collection_mode': 'passive'}
            try: built = _discovery_query(settings['service'], settings['provider'], params)
            except (ValueError, KeyError) as exc: raise BlockError(str(exc)) from None
            if not built.get('supported'): raise BlockError(built.get('reason') or 'This provider has no query for that service and strategy')
            found.append((built['base_query'], built.get('scope') or {}, built.get('base_type', settings['service'])))
        return found, unusable


@register
class RunSavedLookup(Block):
    key, name, group, reach, takes, gives = 'run_saved_lookup', 'Run a saved lookup', 'find', 'credits', 'any', 'endpoint'
    says = 'Runs one of your Scheduled lookups now, with its own query, scope, limits and collection mode.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('lookup', 'Lookup', 'choice', required=True, options='lookups'),)

    def _lookup(self, settings):
        try: return SavedLookup.objects.get(pk=uuid.UUID(settings['lookup']))
        except (ValueError, SavedLookup.DoesNotExist): raise BlockError('The chosen lookup no longer exists') from None

    def cost(self, settings, items_in):
        try: lookup = self._lookup(settings)
        except BlockError: return Cost(pages=1)
        return Cost(pages=lookup.page_count, items_out=lookup.max_results)

    def begin(self, ctx, items, settings, allowance):
        lookup = self._lookup(settings)
        if lookup.provider == 'research': raise BlockError('Web research lookups cannot run from a workflow yet')
        # The lookup runs exactly as saved, so it runs only when its page count fits what this run has left.
        if lookup.page_count > allowance['pages']: return Done([], note='Not run: it fetches ' + str(lookup.page_count) + ' pages and ' + str(allowance['pages']) + ' are left in this run', left_out=1)
        run = queue_lookup(lookup)
        if run.state == 'skipped': return Done([], note=run.error or 'The lookup was skipped')
        return Waiting([str(run.pk)], {})

    def resume(self, ctx, items, settings, state, runs):
        child = next(iter(runs.values()))
        if child.state != 'succeeded': raise BlockError(child.error or 'The lookup did not finish (' + child.state + ')')
        try: result = json.loads(child.result or '{}')
        except ValueError: result = {}
        found, dropped = endpoint_items(result.get('endpoint_ids') or [])
        return Done(found, pages=int(result.get('pages') or 0), note=str(result.get('new_results', len(found))) + ' new · ' + str(result.get('repeat_results', 0)) + ' captured before' + gone(dropped))


@register
class RunCustomQuery(Search):
    """A saved Custom query, filled in and run through the same lookup job as Search."""
    key, name = 'run_custom_query', 'Run a custom query'
    says = 'Runs one of your Custom queries, filling in its keyword, country, network and exclusions.'
    fields = (Field('query', 'Custom query', 'choice', required=True, options='queries'),
              Field('provider', 'Provider', 'choice', default='', choices=(('', 'The one saved with the query'), ('shodan', 'Shodan'), ('zoomeye', 'ZoomEye'), ('netlas', 'Netlas'))),
              Field('keyword', 'Keyword', 'text', template=True, help='With a value such as {{hostname}} the query runs once per result.'),
              Field('country', 'Country code', 'text'), Field('network', 'Network (IP or CIDR)', 'text'), Field('exclude', 'Exclude', 'text'),
              Field('start', 'Start at', 'choice', default='continue', choices=(('continue', 'Where the last run stopped (new results only)'), ('beginning', 'The beginning (repeat results is OK)'))),
              Field('pages', 'Pages to fetch', 'number', default=1, minimum=1, maximum=20),
              Field('page_size', 'ZoomEye results per page', 'number', default=20, minimum=1, maximum=100))

    def _saved(self, settings):
        from leakscope_app.models import CustomQuery
        try: return CustomQuery.objects.get(pk=int(settings['query']))
        except (ValueError, TypeError, CustomQuery.DoesNotExist): raise BlockError('The chosen custom query no longer exists') from None

    def _resolved(self, settings):
        if settings['provider']: return settings
        try: return dict(settings, provider=self._saved(settings).provider)
        except BlockError: return dict(settings, provider='shodan')

    def cost(self, settings, items_in):
        settings = self._resolved(settings)
        size = effective_page_size(settings['provider'], settings['page_size'])
        times = max(1, items_in) if placeholders(settings['keyword']) else 1
        return Cost(pages=settings['pages'] * times, items_out=settings['pages'] * size * times)

    def searches(self, items, settings):
        from leakscope.services.query import render_query_template
        saved = self._saved(settings)
        keywords = [settings['keyword']] if not placeholders(settings['keyword']) else [render(settings['keyword'], item, quoted=True) for item in items]
        scope = {key: settings[key] for key in ('country', 'network', 'exclude')}
        found = [(render_query_template(saved.query_template, dict(scope, keyword=keyword)), scope, saved.default_type)
                 for keyword in dict.fromkeys(keywords) if keyword is not None]
        return found, sum(1 for keyword in keywords if keyword is None)

    def begin(self, ctx, items, settings, allowance):
        saved = self._saved(settings)
        return super().begin(ctx, items, dict(settings, provider=settings['provider'] or saved.provider), allowance)


@register
class FindSimilar(Search):
    """Find similar, automated: one search per distinct piece of saved evidence."""
    key, name, takes = 'find_similar', 'Find similar', 'endpoint'
    says = 'Searches a provider for other hosts with the same favicon, certificate, server, product or device type as each result.'
    fields = (Field('match', 'With the same', 'choice', required=True, choices=(('favicon', 'Favicon'), ('certificate', 'Certificate'), ('server', 'Server header'),
                                                                                 ('product', 'Product'), ('device', 'Device type'))),
              Field('provider', 'Provider', 'choice', required=True, choices=(('shodan', 'Shodan'), ('zoomeye', 'ZoomEye'), ('netlas', 'Netlas'))),
              Field('country', 'Country code', 'text'), Field('network', 'Network (IP or CIDR)', 'text'),
              Field('start', 'Start at', 'choice', default='continue', choices=(('continue', 'Where the last run stopped (new results only)'), ('beginning', 'The beginning (repeat results is OK)'))),
              Field('pages', 'Pages to fetch', 'number', default=1, minimum=1, maximum=20),
              Field('page_size', 'ZoomEye results per page', 'number', default=20, minimum=1, maximum=100))

    def cost(self, settings, items_in):
        size, times = effective_page_size(settings['provider'], settings['page_size']), max(1, items_in)
        return Cost(pages=settings['pages'] * times, items_out=settings['pages'] * size * times)

    def searches(self, items, settings):
        from leakscope.intelligence.queries import _MAP, compile_queries
        from leakscope.phase3.state import visible_endpoints
        from leakscope_app.intelligence import _config, _observed_fields
        config, provider = _config(), settings['provider']
        scope = {key: settings[key] for key in ('country', 'network') if settings[key]}
        rows = {str(row.pk): row for row in visible_endpoints().filter(pk__in=[item['id'] for item in items if item.get('kind') == 'endpoint'])}
        queries, unusable = [], 0
        for item in items:
            row, query = rows.get(item['id']), ''
            # Only evidence this provider can search on: an MMH3 favicon for Shodan and ZoomEye, a SHA-256 one for Netlas.
            signals = [{key: field[key] for key in ('field', 'value', 'source')} for field in (_observed_fields(row, 'endpoint', config) if row else [])
                       if field['field'].split('.')[0] == settings['match'] and field['field'] in _MAP[provider]]
            if signals:
                try: query = (compile_queries(signals, mode='all', scope=scope).get(provider) or {}).get('query') or ''
                except (ValueError, KeyError): query = ''
            if query: queries.append(query)
            else: unusable += 1
        return [(query, {}, '') for query in dict.fromkeys(queries)], unusable


def _target(item):
    """The one address an item stands for: (field, value) with field 'ip' or 'host', or None."""
    fields = item.get('fields') or {}
    if fields.get('ip'): return 'ip', str(fields['ip']).lower()
    name = fields.get('hostname') or fields.get('domain') or fields.get('address') or ''
    return ('host', str(name).lower().rstrip('.')) if name else None


@register
class SearchAddress(Search):
    """Drill down on one address: an exact provider query per IP address or name, and only matching results passed on."""
    key, name = 'search_address', 'Look up each address'
    says = 'Asks a provider about exactly each IP address or name that reaches this step, and passes on only results at that address.'
    fields = (Field('provider', 'Provider', 'choice', required=True, choices=(('shodan', 'Shodan'), ('zoomeye', 'ZoomEye'), ('netlas', 'Netlas'))),
              Field('match', 'For names, pass on', 'choice', default='exact', choices=(('exact', 'Only that exact name'), ('subdomains', 'The name and its subdomains'))),
              Field('start', 'Start at', 'choice', default='continue', choices=(('continue', 'Where the last run stopped (new results only)'), ('beginning', 'The beginning (repeat results is OK)'))),
              Field('pages', 'Pages to fetch per address', 'number', default=1, minimum=1, maximum=20),
              Field('page_size', 'ZoomEye results per page', 'number', default=20, minimum=1, maximum=100))

    def cost(self, settings, items_in):
        size, times = effective_page_size(settings['provider'], settings['page_size']), max(1, items_in)
        return Cost(pages=settings['pages'] * times, items_out=settings['pages'] * size * times)

    def searches(self, items, settings):
        from leakscope.intelligence.queries import compile_queries
        queries, unusable = [], 0
        for item in items:
            target, query = _target(item), ''
            if target:
                # The provider's own filter for an address or a host name, with the value quoted and escaped.
                try:
                    if target[0] == 'host' and settings['match'] == 'subdomains' and settings['provider'] == 'netlas':
                        # Netlas matches a host exactly, so subdomains have to be asked for by name pattern.
                        from leakscope.intelligence.queries import discovery_queries
                        query = discovery_queries('netlas', domain=target[1])[0][1]
                    else:
                        query = (compile_queries([{'field': target[0], 'value': target[1], 'source': 'workflow'}], mode='all', scope={}).get(settings['provider']) or {}).get('query') or ''
                except (ValueError, KeyError): query = ''
            if query: queries.append(query)
            else: unusable += 1
        return [(query, {}, '') for query in dict.fromkeys(queries)], unusable

    def resume(self, ctx, items, settings, state, runs):
        done = super().resume(ctx, items, settings, state, runs)
        targets = [target for target in map(_target, items) if target]
        ips, names = {value for field, value in targets if field == 'ip'}, {value for field, value in targets if field == 'host'}
        def at(found):
            fields = found['fields']
            held = {str(fields.get('address') or '').lower(), str(fields.get('ip') or '').lower()} | {str(name).lower() for name in fields.get('names') or []}
            if held & ips or held & names: return True
            return settings['match'] == 'subdomains' and any(value.endswith('.' + name) for value in held for name in names)
        kept = [found for found in done.items if at(found)]
        if len(kept) < len(done.items): done.note += ' · ' + str(len(done.items) - len(kept)) + ' at a different address left out'
        done.items = kept
        return done



@register
class SharedEvidence(Block):
    key, name, group, reach, takes, gives = 'shared_evidence', 'Saved results that share evidence', 'find', 'local', 'endpoint', 'endpoint'
    says = 'Passes on other saved results with the same certificate, favicon, tracking ID or other saved evidence, as Correlations shows. Nothing is sent.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('kind', 'Only this kind of evidence', 'text', help='The start of a kind, such as certificate, favicon or tracking. Empty means any.'),
              Field('include', 'Also pass on the results that came in', 'choice', default='no', choices=(('no', 'No'), ('yes', 'Yes'))),
              Field('limit', 'At most', 'number', default=500, minimum=1, maximum=5000))

    def cost(self, settings, items_in):
        return Cost(items_out=settings['limit'])

    def begin(self, ctx, items, settings, allowance):
        from leakscope_app.workspace import _correlation_candidates
        items, dropped = refresh(items)
        mine = [item['id'] for item in items]
        candidates = _correlation_candidates(None)
        if settings['kind']: candidates = candidates.filter(kind__startswith=settings['kind'])
        held = []      # (incoming result, kind, value)
        for start in range(0, len(mine), 500):
            held += [(str(pk), kind, value) for pk, kind, value in candidates.filter(endpoint_id__in=mine[start:start + 500]).values_list('endpoint_id', 'kind', 'value')[:5000]]
        values, own, found, hit = {}, set(mine), [], set()
        for _, kind, value in held: values.setdefault(kind, set()).add(value)
        for kind, wanted in values.items():
            wanted = sorted(wanted)
            for start in range(0, len(wanted), 500):
                for pk, value in candidates.filter(kind=kind, value__in=wanted[start:start + 500]).order_by('endpoint_id').values_list('endpoint_id', 'value')[:20000]:
                    if str(pk) not in own:
                        found.append(str(pk)); hit.add((kind, value))
        shared = {pk for pk, kind, value in held if (kind, value) in hit}
        others = list(dict.fromkeys(found))
        # The results that came in keep the values earlier steps put on them.
        kept = items if settings['include'] == 'yes' else []
        fresh, missing = endpoint_items(others[:max(0, settings['limit'] - len(kept))])
        out = (kept + fresh)[:settings['limit']]
        note = str(len(others)) + ' saved result' + ('' if len(others) == 1 else 's') + ' share evidence with ' + str(len(shared)) + ' of ' + str(len(items))
        return Done(out, note=note + gone(dropped), left_out=max(0, len(others) + (len(mine) if settings['include'] == 'yes' else 0) - settings['limit']))
