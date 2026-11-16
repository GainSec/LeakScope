"""Draw evidence images from saved banners and protocol exchanges.

Local work only: reads saved observations and wire transcripts, draws each one
once and stores the image beside its source. No endpoint or provider is contacted.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=5000, help='Maximum saved endpoints to read (newest first).')
        parser.add_argument('--endpoint', action='append', default=[], help='Limit to one saved endpoint ID; may be repeated.')
        parser.add_argument('--dry-run', action='store_true', help='Count what could be drawn without writing anything.')

    def handle(self, *args, **options):
        from leakscope.phase3.evidence_jobs import endpoint_documents, existing_keys, render_endpoint, render_key
        from leakscope.phase3.state import visible_endpoints
        rows = visible_endpoints().order_by('-last_seen', '-pk')
        if options['endpoint']:
            rows = rows.filter(pk__in=options['endpoint'])
        totals = {'endpoints': 0, 'drawn': 0, 'existing': 0, 'failed': 0, 'drawable': 0}
        for endpoint in rows[:max(1, options['limit'])].iterator(chunk_size=200):
            totals['endpoints'] += 1
            if options['dry_run']:
                known = existing_keys([endpoint.pk])
                for document in endpoint_documents(endpoint):
                    totals['existing' if render_key(document['source']) in known else 'drawable'] += 1
                continue
            outcome = render_endpoint(endpoint)
            for key in ('drawn', 'existing', 'failed'):
                totals[key] += outcome[key]
        self.stdout.write(str(totals) + ' network_contact=False')
