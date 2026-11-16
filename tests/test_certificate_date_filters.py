from datetime import datetime, timezone
from django.test import TestCase, override_settings
from leakscope_app.models import CertificateRecord


class CertificateDateFilterTests(TestCase):
    def certificate(self, stamp):
        return CertificateRecord.objects.create(key=stamp.isoformat(),domains=['anduril.com'],last_seen=stamp)

    def query(self, **params):
        response=self.client.get('/api/assets/certificates',params)
        self.assertEqual(response.status_code,200,response.content)
        return {item['id'] for item in response.json()['results']}

    def test_date_only_bounds_include_both_edges_and_the_whole_day(self):
        before=self.certificate(datetime(2026,11,7,23,59,59,999999,tzinfo=timezone.utc))
        first=self.certificate(datetime(2026,11,8,0,0,0,tzinfo=timezone.utc))
        noon=self.certificate(datetime(2026,11,8,12,30,tzinfo=timezone.utc))
        last=self.certificate(datetime(2026,11,8,23,59,59,999999,tzinfo=timezone.utc))
        after=self.certificate(datetime(2026,11,9,0,0,0,tzinfo=timezone.utc))
        self.assertEqual(self.query(since='2026-11-08',until='2026-11-08'),{str(item.pk) for item in [first,noon,last]})

    @override_settings(TIME_ZONE='America/New_York')
    def test_date_only_bounds_use_application_timezone_across_dst_boundary(self):
        # DST ends November1; the local day is25hours, from04:00Z to04:59:59Z.
        before=self.certificate(datetime(2026,11,1,3,59,59,tzinfo=timezone.utc))
        first=self.certificate(datetime(2026,11,1,4,0,0,tzinfo=timezone.utc))
        last=self.certificate(datetime(2026,11,2,4,59,59,999999,tzinfo=timezone.utc))
        after=self.certificate(datetime(2026,11,2,5,0,0,tzinfo=timezone.utc))
        self.assertEqual(self.query(since='2026-11-01',until='2026-11-01'),{str(first.pk),str(last.pk)})

    @override_settings(TIME_ZONE='America/New_York')
    def test_aware_datetime_until_preserves_exact_instant_and_offset(self):
        before=self.certificate(datetime(2026,11,8,11,59,59,tzinfo=timezone.utc))
        exact=self.certificate(datetime(2026,11,8,12,0,0,tzinfo=timezone.utc))
        after=self.certificate(datetime(2026,11,8,12,0,0,1,tzinfo=timezone.utc))
        self.assertEqual(self.query(since='2026-11-08T06:59:59-05:00',until='2026-11-08T07:00:00-05:00'),{str(before.pk),str(exact.pk)})

    def test_invalid_date_or_datetime_is_a_clean_bad_request(self):
        for value in ['2026-02-30','2026-13-08','2026-11-08T25:00:00Z','not-a-date']:
            with self.subTest(value=value):
                self.assertEqual(self.client.get('/api/assets/certificates',{'until':value}).status_code,400)
