from django.core.exceptions import ObjectDoesNotExist
from leakscope.build.kinds import endpoint_items, refresh, gone
from leakscope.phase3.jobs import queue_capture
from .base import Block, Cost, Done, Waiting, register
from leakscope.build.kinds import ENDPOINT_FIELDS

NOT_QUEUED = ' not queued (cooldown, budget or unsupported)'


class Capture(Block):
    """Contacts each endpoint once through the existing capture job; its cooldown and blocklist still apply.
    Each registered subclass below is one capture action."""
    group, reach, takes, gives = 'collect', 'target', 'endpoint', 'endpoint'
    output_fields = ENDPOINT_FIELDS
    action = ''

    def cost(self, settings, items_in):
        return Cost(contacts=items_in)

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)
        chosen, left_out = items[:allowance['contacts']], max(0, len(items) - allowance['contacts'])
        queued, missed = {}, 0
        for item in chosen:
            try: run = queue_capture(item['id'], [self.action], origin=ctx.origin, cooldown_seconds=3600, max_daily=1000)
            except (ValueError, ObjectDoesNotExist): run = None  # unsupported, or deleted since the refresh
            if run is None: missed += 1
            else: queued[str(run.pk)] = item['id']
        if not queued: return Done([], note='0 captured · ' + str(missed) + NOT_QUEUED + gone(dropped), left_out=left_out)
        return Waiting(list(queued), {'endpoints': queued, 'not_queued': missed, 'left_out': left_out})

    def resume(self, ctx, items, settings, state, runs):
        good = [state['endpoints'][key] for key, run in runs.items() if run.state == 'succeeded' and key in state['endpoints']]
        ordered = [endpoint for endpoint in state['endpoints'].values() if endpoint in set(good)]
        found, dropped = endpoint_items(ordered)
        note = str(len(good)) + ' captured · ' + str(state.get('not_queued', 0)) + NOT_QUEUED + ' · ' + str(len(runs) - len(good)) + ' failed' + gone(dropped)
        return Done(found, contacts=len(state['endpoints']), note=note, left_out=state.get('left_out', 0))


@register
class HttpFingerprint(Capture):
    key, name, action = 'http_fingerprint', 'HTTP fingerprint', 'httpx'
    says = 'One web request to each result: title, server, technologies.'


@register
class FetchResponse(Capture):
    key, name, action = 'fetch_response', 'Fetch response', 'http'
    says = 'Saves the response headers and body of each result as evidence.'


@register
class Screenshot(Capture):
    key, name, action = 'screenshot', 'Screenshot', 'screenshot'
    says = 'Opens each result in the capture browser and saves a screenshot.'


@register
class ProtocolIdentity(Capture):
    key, name, action = 'protocol_identity', 'Protocol identity', 'identity'
    says = "Performs the service's own greeting and records what it says it is."


@register
class PortCheck(Capture):
    key, name, action = 'port_check', 'Port answers', 'check'
    says = 'Checks that each result still accepts a connection.'
