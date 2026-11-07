"""Small, polite client for the public TripSpark RouteMap endpoints only."""

from __future__ import annotations

import base64
import binascii
import re
import ssl
import time
from collections.abc import Callable, Iterable
from typing import Any

import requests
import truststore
from requests.adapters import HTTPAdapter

from aggiepulse.common.paths import USER_AGENT

BASE_URL = "https://aggiespirit.ts.tamu.edu"
_TOKEN_RE = re.compile(
    r"RouteMap\.init\(\s*routeMapOptions\s*,\s*[^,]+,\s*"
    r"[\"']([A-Za-z0-9+/=]+)[\"']\s*,\s*false\s*\)",
    re.DOTALL,
)


class TripSparkError(RuntimeError):
    """A failed fetch; status 0 means no HTTP response was received."""

    def __init__(self, message: str, http_status: int = 0) -> None:
        super().__init__(message)
        self.http_status = http_status


def extract_token(html: str) -> str:
    """Decode the inline RouteMap token, rather than the cookie token."""
    match = _TOKEN_RE.search(html)
    if match is None:
        raise ValueError("RouteMap.init verification token not found")
    try:
        token = base64.b64decode(match.group(1), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("Invalid RouteMap verification token") from exc
    if not token or "\n" in token or "\r" in token:
        raise ValueError("Invalid RouteMap verification token")
    return token


class _SystemTrustAdapter(HTTPAdapter):
    """Use native OS trust (including missing intermediates) with TLS verification."""

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **kwargs: Any) -> Any:
        kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return super().proxy_manager_for(proxy, **kwargs)


def _new_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", _SystemTrustAdapter())
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"{BASE_URL}/RouteMap",
            "Origin": BASE_URL,
        }
    )
    return session


class TripSparkClient:
    """Refresh cookies/token after errors and retry with capped exponential backoff.

    Each method makes at most ``max_attempts`` requests, then raises TripSparkError
    so the collector can durably record the failure. Failure backoff survives
    method calls, and resets after a successful API response. ``wait`` returns
    true to cancel (e.g. threading.Event.wait during shutdown).
    """

    def __init__(
        self,
        *,
        timeout: float = 30,
        max_attempts: int = 3,
        backoff_initial: float = 2,
        backoff_cap: float = 300,
        session_factory: Callable[[], requests.Session] = _new_session,
        wait: Callable[[float], object] = time.sleep,
    ) -> None:
        if max_attempts < 1 or timeout <= 0 or backoff_initial < 0 or backoff_cap < 0:
            raise ValueError("Invalid timeout, attempt count, or backoff")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff_initial = backoff_initial
        self.backoff_cap = backoff_cap
        self._session_factory = session_factory
        self._wait = wait
        self._session: requests.Session | None = None
        self._ready = False
        self._failures = 0
        self.last_http_status = 0

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
        self._session = None
        self._ready = False

    def _bootstrap(self) -> None:
        self._session = self._session_factory()
        response = self._session.get(f"{BASE_URL}/RouteMap", timeout=self.timeout)
        self.last_http_status = response.status_code
        response.raise_for_status()
        self._session.headers["RequestVerificationToken"] = extract_token(response.text)
        self._ready = True

    def _fetch(self, endpoint: str, form: list[tuple[str, str]] | None) -> Any:
        for attempt in range(self.max_attempts):
            if self._failures:
                delay = min(
                    self.backoff_cap,
                    self.backoff_initial * 2 ** min(self._failures - 1, 30),
                )
                if self._wait(delay):
                    raise TripSparkError("Collector stopping", self.last_http_status)
            self.last_http_status = 0
            try:
                if not self._ready:
                    self._bootstrap()
                assert self._session is not None
                # An explicit empty body gives GetBaseData Content-Length: 0.
                self.last_http_status = 0
                response = self._session.post(
                    f"{BASE_URL}/RouteMap/{endpoint}/",
                    data=form if form is not None else b"",
                    timeout=self.timeout,
                )
                self.last_http_status = response.status_code
                response.raise_for_status()
                if "json" not in response.headers.get("Content-Type", "").lower():
                    raise ValueError("TripSpark returned a non-JSON response")
                payload = response.json()
                if endpoint == "GetBaseData":
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("routes"), list
                    ):
                        raise ValueError("TripSpark base payload has no routes list")
                    if not payload["routes"] or any(
                        not isinstance(route, dict) or not route.get("key")
                        for route in payload["routes"]
                    ):
                        raise ValueError(
                            "TripSpark base payload has invalid route keys"
                        )
                elif not isinstance(payload, list):
                    raise ValueError("TripSpark vehicles payload is not a list")
                self._failures = 0
                return payload
            except (requests.RequestException, ValueError) as exc:
                status = self.last_http_status
                self._failures += 1
                self.close()
                if attempt + 1 == self.max_attempts:
                    raise TripSparkError(str(exc), status) from exc
        raise AssertionError("unreachable")

    def get_base_data(self) -> dict[str, Any]:
        return self._fetch("GetBaseData", None)

    def get_vehicles(self, route_keys: Iterable[str]) -> list[dict[str, Any]]:
        keys = list(dict.fromkeys(route_keys))
        if not keys:
            raise ValueError("At least one route key is required")
        return self._fetch("GetVehicles", [("routeKeys[]", key) for key in keys])
