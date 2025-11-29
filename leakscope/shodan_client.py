import logging
from typing import Any, Dict, Optional

import requests


class ShodanClientError(Exception):
    """Raised when the Shodan API returns an error or cannot be reached."""


class ShodanClient:
    BASE_URL = "https://api.shodan.io"

    def __init__(self, api_key: str, timeout: int = 30, session: Optional[requests.Session] = None):
        if not api_key:
            raise ShodanClientError("Missing SHODAN_API_KEY (set environment variable SHODAN_API_KEY)")
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        self.log = logging.getLogger(__name__)

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = params.copy() if params else {}
        params.setdefault("key", self.api_key)
        url = f"{self.BASE_URL}{path}"
        try:
            resp = self.session.request(method=method, url=url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ShodanClientError(f"Failed to contact Shodan API: {exc}") from exc

        if resp.status_code == 401:
            raise ShodanClientError("Invalid Shodan API key (401)")
        if resp.status_code == 402:
            raise ShodanClientError("Shodan API request quota exceeded (402)")
        if resp.status_code == 429:
            raise ShodanClientError("Shodan API rate limit reached (429)")

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise ShodanClientError(f"Shodan API returned HTTP {resp.status_code}: {resp.text}") from exc

        try:
            return resp.json()
        except ValueError as exc:
            raise ShodanClientError("Failed to decode Shodan API response as JSON") from exc

    def info(self) -> Dict[str, Any]:
        return self._request("GET", "/api-info")

    def host_search(self, query: str, page: int = 1) -> Dict[str, Any]:
        params = {"query": query, "page": page}
        return self._request("GET", "/shodan/host/search", params=params)

    def host_count(self, query: str) -> Dict[str, Any]:
        params = {"query": query}
        return self._request("GET", "/shodan/host/count", params=params)

    def host_filters(self) -> Dict[str, Any]:
        return self._request("GET", "/shodan/host/search/filters")

    def host_facets(self) -> Dict[str, Any]:
        return self._request("GET", "/shodan/host/search/facets")
