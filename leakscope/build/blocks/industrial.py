"""The Industrial page's per-result actions: read-only point inspection, TCP port inventory and the web interface check."""
import json
from leakscope_app.models import ServiceEndpoint
from .base import Field, Cost, register
from .enrich import PerResultJob


@register
class IndustrialInspect(PerResultJob):
    key, name, group, reach, spends, done_word = 'industrial_inspect', 'Read industrial points', 'collect', 'target', 'contacts', 'inspected'
    says = 'Reads device identity and a few points from Modbus TCP and BACnet/IP results, read-only, as the Industrial page does.'
    fields = (Field('timeout_seconds', 'Timeout (seconds)', 'number', default=10, minimum=1, maximum=20),)

    def launch(self, ctx, item, settings):
        from leakscope.phase3 import ics_jobs
        return ics_jobs.queue_inspection(ServiceEndpoint.objects.get(pk=item['id']), {'timeout_seconds': settings['timeout_seconds']}).pk


@register
class PortInventory(PerResultJob):
    key, name, group, reach, spends, done_word = 'port_inventory', 'Port inventory', 'collect', 'target', 'contacts', 'hosts scanned'
    says = 'Actively checks which TCP ports are open on each result’s IP address, as the Industrial page does, and saves what answers.'
    fields = (Field('ports', 'Ports', 'text', default='1-1024', help='Ranges and lists, such as 1-1024 or 80,443,8080.'),
              Field('rate_per_second', 'Connections per second', 'number', default=20, minimum=1, maximum=100),
              Field('max_seconds', 'Time budget per host (seconds)', 'number', default=120, minimum=15, maximum=900),
              Field('pass_on', 'Pass on', 'choice', default='found', choices=(('found', 'The services it found'), ('same', 'The results that were scanned'))))

    def cost(self, settings, items_in):
        return Cost(contacts=items_in, items_out=items_in * (50 if settings['pass_on'] == 'found' else 1))

    def begin(self, ctx, items, settings, allowance):
        outcome = super().begin(ctx, items, settings, allowance)
        if hasattr(outcome, 'state'): outcome.state['pass_on'] = settings['pass_on']
        return outcome

    def launch(self, ctx, item, settings):
        from leakscope.phase3 import ics_jobs
        options = {key: settings[key] for key in ('ports', 'rate_per_second', 'max_seconds')}
        return ics_jobs.queue_scan(ServiceEndpoint.objects.get(pk=item['id']), options).pk

    def results(self, state, good, runs):
        if state.get('pass_on') == 'same': return state['ready'] + good
        found = []
        for run in runs.values():
            if run.state != 'succeeded': continue
            try: found += json.loads(run.result or '{}').get('endpoint_ids') or []
            except ValueError: continue
        return found


@register
class WebInterfaceCheck(PerResultJob):
    key, name, group, reach, spends, done_word = 'web_probe', 'Check for a web interface', 'collect', 'target', 'contacts', 'checked'
    says = 'Tries HTTP or HTTPS on results whose technology is unknown or HTTP, and saves the response.'
    fields = (Field('scheme', 'Try', 'choice', default='https', choices=(('https', 'HTTPS'), ('http', 'HTTP'))),)

    def launch(self, ctx, item, settings):
        from leakscope.phase3 import ics_jobs
        return ics_jobs.queue_web_probe(ServiceEndpoint.objects.get(pk=item['id']), settings['scheme'] == 'https').pk
