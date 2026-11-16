from unittest.mock import Mock
import requests
from django.test import SimpleTestCase
from leakscope.shodan_client import ShodanClient, ShodanClientError
from leakscope.zoomeye_client import ZoomEyeClient, ZoomEyeClientError


class ProviderRedactionTests(SimpleTestCase):
    def test_shodan_transport_error_does_not_expose_key(self):
        session = Mock()
        session.request.side_effect = requests.ConnectionError(
            "Failed https://api.shodan.io/?key=fixture-sensitive-key"
        )
        with self.assertRaises(ShodanClientError) as result:
            ShodanClient("fixture-sensitive-key", session=session).info()
        self.assertNotIn("fixture-sensitive-key", str(result.exception))

    def test_zoomeye_error_body_does_not_expose_key(self):
        response = Mock(status_code=500, text="echo fixture-sensitive-key")
        response.raise_for_status.side_effect = requests.HTTPError("request failed")
        session = Mock()
        session.request.return_value = response
        with self.assertRaises(ZoomEyeClientError) as result:
            ZoomEyeClient("fixture-sensitive-key", session=session).userinfo()
        self.assertNotIn("fixture-sensitive-key", str(result.exception))
