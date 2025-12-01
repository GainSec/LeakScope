import base64
import logging
from typing import Any, Dict, Optional

import requests


class ZoomEyeClientError(Exception):
    """Raised when the ZoomEye API returns an error or cannot be reached."""


class ZoomEyeClient:
    # Prefer .ai per latest API; .org is legacy and may reject newer keys.
    BASE_URL = "https://api.zoomeye.ai/v2"

    def __init__(self, api_key: str, timeout: int = 30, session: Optional[requests.Session] = None):
        if not api_key:
            raise ZoomEyeClientError("Missing ZOOMEYE_API_KEY (set environment variable ZOOMEYE_API_KEY)")
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        self.log = logging.getLogger(__name__)

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                 json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{path}"
        headers = {
            "API-KEY": self.api_key,
            "User-Agent": "LeakScope/zoomeye",
        }

        try:
            resp = self.session.request(
                method=method,
                url=url,
                params=params if json_body is None else None,
                json=json_body if json_body is not None else None,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ZoomEyeClientError(f"Failed to contact ZoomEye API: {exc}") from exc

        if resp.status_code in (401, 403):
            raise ZoomEyeClientError("Invalid ZoomEye API key or permission denied")

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise ZoomEyeClientError(f"ZoomEye API returned HTTP {resp.status_code}: {resp.text}") from exc

        try:
            return resp.json()
        except ValueError as exc:
            raise ZoomEyeClientError("Failed to decode ZoomEye API response as JSON") from exc

    def userinfo(self) -> Dict[str, Any]:
        return self._request("POST", "/userinfo", json_body={})

    def search(self, query: str, page: int = 1, pagesize: int = 20, sub_type: str = "all",
               fields: str = "", facets: str = "") -> Dict[str, Any]:
        # ZoomEye requires base64-encoded query via qbase64.
        qbase64 = base64.b64encode(query.encode("utf-8")).decode("utf-8")
        payload = {
            "qbase64": qbase64,
            "page": page,
            "pagesize": pagesize,
            "sub_type": sub_type,
            "fields": fields,
            "facets": facets,
        }
        return self._request("POST", "/search", json_body=payload)


def normalize_match(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a ZoomEye match into a Shodan-like shape for downstream handlers."""
    norm: Dict[str, Any] = {}
    raw_ip = raw.get("ip")
    ip = None
    if isinstance(raw_ip, list) and raw_ip:
        ip = raw_ip[0]
    elif isinstance(raw_ip, str):
        ip = raw_ip
    norm["ip_str"] = ip

    portinfo = raw.get("portinfo") or {}
    port = portinfo.get("port") or raw.get("port")
    norm["port"] = port

    # Attempt to map web/http details
    web = raw.get("web") or {}
    http_section: Dict[str, Any] = {}
    title = web.get("title") or portinfo.get("title")
    if title:
        http_section["title"] = title
    body = web.get("body") or web.get("raw_data") or raw.get("data")
    if body:
        http_section["html"] = body
    if http_section:
        norm["http"] = http_section

    norm["data"] = raw.get("data")
    norm["protocol"] = raw.get("protocol") or portinfo.get("service")
    return norm
