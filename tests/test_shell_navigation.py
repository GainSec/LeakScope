import re

from django.test import TestCase

GROUPS = {
    'find': ['discover', 'cloud', 'domain-expansion', 'queries'],
    'evidence': ['assets', 'technologies', 'screenshots', 'geography', 'certificates', 'findings'],
    'scope': ['organizations', 'relationships', 'domain-coverage', 'correlations'],
    'watch': ['changes', 'monitoring', 'lookups', 'runs'],
    'automate': ['build'],
    'tools': ['preview', 'catalog', 'research', 'analysis', 'exports'],
}


class ShellNavigationTests(TestCase):
    def page(self, path='/'):
        return self.client.get(path).content.decode()

    def test_sidebar_groups_entries_by_job_in_order(self):
        html = self.page('/assets/')
        self.assertEqual(re.findall(r'data-nav-group="([\w-]+)"', html), list(GROUPS))
        for group, entries in GROUPS.items():
            block = html.split(f'data-nav-group="{group}"')[1].split('</details>')[0]
            self.assertEqual(re.findall(r'data-nav="([\w-]+)"', block), entries)
        self.assertNotIn('data-nav="signatures"', html)
        self.assertNotIn('data-nav="explore"', html)
        self.assertIn('id="command-extra"', html)

    def test_every_entry_has_a_visible_text_label(self):
        links = re.findall(r'<a [^>]*data-nav="[\w-]+"[^>]*>(.*?)</a>', self.page(), re.S)
        self.assertGreaterEqual(len(links), 25)
        for link in links:
            self.assertRegex(link, r'class="nav-title">[^<]+<')

    def test_inspect_tabs_only_on_preview_and_explore(self):
        self.assertIn('class="page-tabs"', self.page('/explore/'))
        self.assertIn('class="page-tabs"', self.page('/preview/'))
        self.assertNotIn('class="page-tabs"', self.page('/assets/'))

    def test_status_items_live_in_one_menu(self):
        html = self.page()
        menu = html.split('id="status-menu"')[1].split('</details>')[0]
        for needle in ('id="activity-logs-toggle"', 'id="motion-toggle"', 'Private network', 'System health'):
            self.assertIn(needle, menu)
        bar = html.split('class="topbar"')[1].split('</header>')[0]
        self.assertLess(bar.index('id="workspace-activity"'), bar.index('id="status-menu"'))
        self.assertNotIn('id="command-toggle"', bar)
