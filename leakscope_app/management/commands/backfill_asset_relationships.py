"""Materialize evidence from local saved records; never query providers."""
from django.core.management.base import BaseCommand
from leakscope_app.models import ServiceObservation, CloudResource
from leakscope.phase3.certificate_store import backfill_certificates
from leakscope.phase3.relationships import ingest_observation_graph, ingest_cloud_graph


class Command(BaseCommand):
    help = 'Idempotently index retained local certificates, observations and cloud evidence'

    def handle(self, *args, **options):
        certificates = backfill_certificates()
        observations = 0
        for row in ServiceObservation.objects.exclude(provider='certstream').filter(hidden=False).select_related('endpoint').iterator(chunk_size=200):
            ingest_observation_graph(row)
            observations += 1
        cloud = 0
        for row in CloudResource.objects.select_related('endpoint').iterator(chunk_size=200):
            ingest_cloud_graph(row)
            cloud += 1
        self.stdout.write(f'Indexed local records: certificates={certificates}, observations={observations}, cloud={cloud}; no network requests')
