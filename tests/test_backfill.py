import io, json
from django.test import TestCase
from django.core.management import call_command
from leakscope_app.models import Search, Gitlab


class AttributionBackfillTests(TestCase):
    def test_dry_run_then_apply_is_idempotent(self):
        search = Search.objects.create(type="gitlab", provider="zoomeye")
        row = Gitlab.objects.create(
            search=search, provider="shodan", ip="192.0.2.88", port="443"
        )
        out = io.StringIO()
        call_command("backfill_providers", stdout=out)
        self.assertEqual(json.loads(out.getvalue())["changed"], 1)
        row.refresh_from_db()
        self.assertEqual(row.provider, "shodan")
        out = io.StringIO()
        call_command("backfill_providers", apply=True, stdout=out)
        row.refresh_from_db()
        self.assertEqual(row.provider, "zoomeye")
        out = io.StringIO()
        call_command("backfill_providers", apply=True, stdout=out)
        self.assertEqual(json.loads(out.getvalue())["changed"], 0)
