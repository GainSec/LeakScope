"""Report or repair provider attribution using only linked run evidence."""

import json
from django.core.management.base import BaseCommand
from django.db import transaction
from leakscope.services.registry import service_types


class Command(BaseCommand):
    help = "Dry-run provider corrections by default; pass --apply to persist evidence-backed values."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true", help="Persist the reported corrections"
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        report = {
            "mode": "apply" if apply else "dry-run",
            "changed": 0,
            "ambiguous": 0,
            "models": [],
        }
        with transaction.atomic():
            for service in service_types():
                model = service["model"]
                linked = "search" in {f.name for f in model._meta.fields}
                query = (
                    model.objects.select_related("search")
                    if linked
                    else model.objects.all()
                )
                changes = 0
                ambiguous = 0
                for row in query.iterator(chunk_size=500):
                    if linked:
                        search = getattr(row, "search", None)
                        provider = getattr(search, "provider", "unknown")
                        desired = (
                            provider if provider in ("shodan", "zoomeye") else "unknown"
                        )
                    else:
                        desired = (
                            "unknown"
                            if row.provider in ("", "shodan")
                            else row.provider
                        )
                        ambiguous += int(row.provider in ("", "shodan", "unknown"))
                    if row.provider != desired:
                        changes += 1
                        if apply:
                            model.objects.filter(pk=row.pk).update(provider=desired)
                report["changed"] += changes
                report["ambiguous"] += ambiguous
                report["models"].append(
                    {"type": service["key"], "changed": changes, "ambiguous": ambiguous}
                )
        self.stdout.write(json.dumps(report))
