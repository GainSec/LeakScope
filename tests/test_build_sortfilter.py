"""Sort and Filter on domains: domains carry the date they were added, and Sort reads plainly for names, numbers and dates."""
from datetime import timedelta
from django.utils import timezone
from leakscope.build.blocks.base import catalog, get
from leakscope.build.kinds import DOMAIN_FIELDS, domain_item
from leakscope_app import relationships
from leakscope_app.models import CertificateName, CertificateRecord, CertificateWatchMatch, DomainCandidate, DomainExpansion, Run
from tests.test_build_blocks import CTX
from tests.test_build_engine import EngineCase

NOW = timezone.now()


def ago(days):
    return NOW - timedelta(days=days)


class DomainAddedTests(EngineCase):
    def expansion(self):
        return DomainExpansion.objects.create(run=Run.objects.create(kind='domain_expansion', state='succeeded'), scope_key=str(DomainExpansion.objects.count()))

    def test_saved_domains_carry_the_earliest_date_they_were_added(self):
        first, second = self.expansion(), self.expansion()
        DomainCandidate.objects.create(expansion=first, domain='old.example.com', root='example.com', created_at=ago(30))
        DomainCandidate.objects.create(expansion=second, domain='old.example.com', root='example.com', created_at=ago(2))
        DomainCandidate.objects.create(expansion=second, domain='new.example.com', root='example.com', created_at=ago(1))
        block = get('saved_domains')
        added = {item['id']: item['fields']['added'] for item in block.begin(CTX, [], block.clean({}), {}).items}
        self.assertEqual(added, {'old.example.com': ago(30).isoformat(), 'new.example.com': ago(1).isoformat()})
        self.assertIn('added', DOMAIN_FIELDS)

    def test_organization_domains_and_certificate_names_carry_it_too(self):
        root = relationships.store.node('organization', 'Acme Corp')
        adder = get('add_domains_to_organization')
        adder.begin(CTX, [domain_item('b.acme.example'), domain_item('a.acme.example')], adder.clean({'organization': str(root.pk)}), {})
        root.outgoing.filter(target__value='b.acme.example').update(first_seen=ago(9))
        root.outgoing.filter(target__value='a.acme.example').update(first_seen=ago(3))
        start = get('organization_domains')
        items = start.begin(CTX, [], start.clean({'organization': str(root.pk)}), {}).items
        self.assertEqual({item['id']: item['fields']['added'] for item in items}, {'b.acme.example': ago(9).isoformat(), 'a.acme.example': ago(3).isoformat()})
        sort = get('sort')
        newest = sort.begin(CTX, items, sort.clean({'by': 'added', 'order': 'desc'}), {})
        self.assertEqual([item['id'] for item in newest.items], ['a.acme.example', 'b.acme.example'])
        self.assertEqual(newest.note, 'Sorted by added, newest first')
        record = CertificateRecord.objects.create(key='a', first_seen=ago(5))
        CertificateName.objects.create(certificate=record, name='sso.example.com')
        CertificateWatchMatch.objects.create(certificate=record, watch_key='w', watch_name='Acme')
        certs = get('certificate_names')
        self.assertEqual(certs.begin(CTX, [], certs.clean({}), {}).items[0]['fields']['added'], ago(5).isoformat())


class SortTests(EngineCase):
    def test_names_sort_a_to_z_and_the_note_says_which_way(self):
        sort = get('sort')
        items = [domain_item(name) for name in ('Mail.example.com', 'api.example.com', 'zeta.example.com')]
        up = sort.begin(CTX, items, sort.clean({'by': 'domain', 'order': 'asc'}), {})
        self.assertEqual([item['id'] for item in up.items], ['api.example.com', 'mail.example.com', 'zeta.example.com'])
        self.assertEqual(up.note, 'Sorted by domain, A to Z')
        down = sort.begin(CTX, items, sort.clean({'by': 'domain', 'order': 'desc'}), {})
        self.assertEqual((down.items[0]['id'], down.note), ('zeta.example.com', 'Sorted by domain, Z to A'))

    def test_items_without_the_field_go_last_either_way(self):
        sort = get('sort')
        items = [domain_item('none.example.com'), domain_item('low.example.com', confidence=10), domain_item('high.example.com', confidence=90)]
        for order, expected in (('asc', ['low.example.com', 'high.example.com', 'none.example.com']), ('desc', ['high.example.com', 'low.example.com', 'none.example.com'])):
            with self.subTest(order=order):
                self.assertEqual([item['id'] for item in sort.begin(CTX, items, sort.clean({'by': 'confidence', 'order': order}), {}).items], expected)

    def test_the_order_choices_read_for_names_numbers_and_dates(self):
        row = next(block for block in catalog() if block['key'] == 'sort')
        order = next(field for field in row['fields'] if field['key'] == 'order')
        self.assertEqual(order['choices'], [['asc', 'A to Z · smallest · oldest first'], ['desc', 'Z to A · largest · newest first']])
        self.assertIn('date added', row['says'])

    def test_filter_keeps_domains_by_part_of_the_name(self):
        block = get('filter')
        items = [domain_item(name) for name in ('dev.example.com', 'www.example.com', 'api-dev.example.com')]
        kept = block.begin(CTX, items, block.clean({'conditions': {'rules': [{'field': 'domain', 'op': 'contains', 'value': 'dev'}]}}), {})
        self.assertEqual([item['id'] for item in kept.items], ['dev.example.com', 'api-dev.example.com'])
