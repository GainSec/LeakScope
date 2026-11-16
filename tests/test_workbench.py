import json
from unittest.mock import patch
from django.test import TestCase, Client
from leakscope_app.models import Search, Gitlab, BlacklistEntry


class WorkbenchTests(TestCase):
    def setUp(self):
        self.search = Search.objects.create(
            type="gitlab", keyword="fixture", network="", country="", provider="zoomeye"
        )
        self.finding = Gitlab.objects.create(
            search=self.search,
            provider="zoomeye",
            ip="192.0.2.15",
            port="443",
            url="https://192.0.2.15",
        )

    def test_direct_shell_has_no_login(self):
        for path in ["/", "/domain-coverage/", "/domain-coverage/?organization_id=example"]:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertTemplateUsed(r, "workbench/app.html")

    def test_summary_uses_result_providers(self):
        r = self.client.get("/api/summary")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["counts"]["total"], 1)
        self.assertEqual(
            next(x for x in r.json()["by_provider"] if x["provider"] == "zoomeye")[
                "count"
            ],
            1,
        )

    def test_findings_filter_and_detail(self):
        r = self.client.get("/api/findings?provider=zoomeye&q=192.0.2.15")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 1)
        r = self.client.get("/api/findings/gitlab/%s" % self.finding.pk)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["finding"]["id"], self.finding.pk)

    def test_review_uses_stable_id(self):
        r = self.client.post(
            "/api/findings/gitlab/%s/review" % self.finding.pk,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.finding.refresh_from_db()
        self.assertTrue(self.finding.confirmed)

    def test_get_cannot_mutate(self):
        self.assertEqual(self.client.get("/api/blacklist").status_code, 405)
        self.assertEqual(BlacklistEntry.objects.count(), 0)

    def test_mutation_requires_csrf_without_login(self):
        c = Client(enforce_csrf_checks=True)
        r = c.post(
            "/api/blacklist",
            data=json.dumps({"entries": ["192.0.2.10"]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_blacklist_persists(self):
        r = self.client.post(
            "/api/blacklist",
            data=json.dumps({"entries": ["192.0.2.10"]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(BlacklistEntry.objects.filter(value="192.0.2.10").exists())

    def test_invalid_discovery_is_rejected_before_enqueue(self):
        r = self.client.post(
            "/api/discover",
            data=json.dumps({"types": ["no-such-service"]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_setting_values_are_not_exposed(self):
        cfg = {
            "config": {
                "SHODAN_API_KEY": "private-test-fixture",
                "ZOOMEYE_API_KEY": "other-private-fixture",
                "blacklist": [],
            }
        }
        with patch("leakscope.tasks.get_config", return_value=cfg):
            r = self.client.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "private-test-fixture")
        self.assertTrue(r.json()["providers"]["shodan"]["configured"])

    def test_query_preview_preserves_quoted_keyword(self):
        r = self.client.post(
            "/api/query-preview",
            data=json.dumps(
                {"types": ["gitlab"], "provider": "zoomeye", "keyword": "hello world"}
            ),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn('"hello world"', r.json()["queries"][0]["query"])

    def test_legacy_page_aliases_open_modern_shell(self):
        for path in [
            "/home/",
            "/keyword/",
            "/database/",
            "/custom-queries/",
            "/preview/",
            "/explore/",
            "/export-import/",
            "/javascript/",
            "/github/",
            "/amazon_buckets/",
        ]:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertTemplateUsed(r, "workbench/app.html")

    def test_hidden_standalone_rows_can_be_restored(self):
        from leakscope_app.models import Github

        item = Github.objects.create(
            commit="fixture",
            path="src/app.py",
            secret="do-not-display-fixture",
            keyword="fixture",
            provider="unknown",
        )
        url = "/api/findings/github/%s/review" % item.pk
        self.assertEqual(
            self.client.post(
                url,
                data=json.dumps({"status": "hidden"}),
                content_type="application/json",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/findings?type=github").json()["count"], 0
        )
        self.assertEqual(
            self.client.get("/api/findings?type=github&status=hidden").json()["count"],
            1,
        )
        self.client.post(
            url, data=json.dumps({"status": "new"}), content_type="application/json"
        )
        self.assertEqual(
            self.client.get("/api/findings?type=github").json()["count"], 1
        )

    def test_local_export_masks_secrets_and_excludes_hidden(self):
        from leakscope_app.models import Github

        item = Github.objects.create(
            commit="fixture",
            path="src/app.py",
            secret="do-not-export-fixture",
            keyword="fixture",
            provider="unknown",
        )
        r = self.client.get("/export/?type=github&format=json")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "do-not-export-fixture")
        self.client.post(
            "/api/findings/github/%s/review" % item.pk,
            data=json.dumps({"status": "hidden"}),
            content_type="application/json",
        )
        self.assertEqual(self.client.get("/export/?type=github&format=json").json(), [])

    def test_detail_sensitive_value_needs_explicit_reveal(self):
        from leakscope_app.models import Github

        item = Github.objects.create(
            commit="fixture",
            path="src/app.py",
            secret="sensitive-observation-fixture",
            keyword="fixture",
            provider="unknown",
        )
        url = "/api/findings/github/%s" % item.pk
        self.assertNotContains(self.client.get(url), "sensitive-observation-fixture")
        self.assertContains(
            self.client.get(url + "?reveal=1"), "sensitive-observation-fixture"
        )

    def test_legacy_alias_hidden_state_is_consistent_and_restorable(self):
        import hashlib
        from leakscope_app.models import ConsulKV, FingerprintLog

        obj = ConsulKV.objects.create(search=self.search, ip="192.0.2.23", port="8500")
        fp = hashlib.sha256(b"consulkv|192.0.2.23|8500").hexdigest()
        FingerprintLog.objects.create(
            type="ConsulKV", ip=obj.ip, port=obj.port, fingerprint=fp, status="hidden"
        )
        rows = self.client.get("/api/findings?type=consul&status=hidden").json()[
            "results"
        ]
        self.assertEqual(rows[0]["status"], "hidden")
        self.assertEqual(
            self.client.get("/api/findings/consul/%s" % obj.pk).json()["status"],
            "hidden",
        )
        self.assertEqual(self.client.get("/api/summary").json()["counts"]["hidden"], 1)
        self.client.post(
            "/api/findings/consul/%s/review" % obj.pk,
            data=json.dumps({"status": "new"}),
            content_type="application/json",
        )
        self.assertEqual(
            self.client.get("/api/findings/consul/%s" % obj.pk).json()["status"], "new"
        )
        self.assertEqual(
            self.client.get("/api/findings?type=consul").json()["count"], 1
        )

    def test_discovery_aliases_are_deduplicated_after_canonicalization(self):
        r = self.client.post(
            "/api/query-preview",
            data=json.dumps({"types": ["harbor", "registry"], "provider": "shodan"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["queries"]), 1)
