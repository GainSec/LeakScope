import json
from django.core.management.base import BaseCommand, CommandError
from leakscope_app.signals import LEGACY_MODELS, legacy_row, bridge_legacy
from leakscope_app.models import EvidenceArtifact, ServiceObservation
from leakscope.phase3.analysis import index_artifact, index_observation


class Command(BaseCommand):
    help='Report eligible legacy finding/artifact indexing; --apply performs idempotent writes'

    def add_arguments(self,parser):
        parser.add_argument('--apply',action='store_true')

    def handle(self,*args,**options):
        report={'mode':'apply' if options['apply'] else 'dry-run','eligible':0,'skipped':0,'indexed':0,'errors':0,'artifacts':0,'observations_indexed':0}
        for model in LEGACY_MODELS:
            rows=model.objects.all()
            if any(field.name=='search' for field in model._meta.fields):rows=rows.select_related('search')
            for obj in rows.iterator(chunk_size=500):
                try:
                    if legacy_row(obj) is None:report['skipped']+=1;continue
                    report['eligible']+=1
                    if options['apply']:
                        bridge_legacy(obj);report['indexed']+=1
                except Exception:
                    # Reports contain counts only, never finding contents.
                    report['errors']+=1
        for artifact in EvidenceArtifact.objects.all().iterator(chunk_size=500):
            report['artifacts']+=1
            if options['apply']:
                try:index_artifact(artifact)
                except Exception:report['errors']+=1
        if options['apply']:
            for observation in ServiceObservation.objects.all().iterator(chunk_size=10):
                try:index_observation(observation);report['observations_indexed']+=1
                except Exception:report['errors']+=1
        self.stdout.write(json.dumps(report,sort_keys=True))
        if options['apply'] and report['errors']:
            raise CommandError('Some records could not be indexed; consult the aggregate error count')
