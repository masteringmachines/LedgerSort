"""
client.py — the only module that touches the network.

A tiny stdlib-only client for TypeSafe's System One endpoint
(POST /v1/systemone, Bearer auth). It honors the same environment
variables as the official SDKs (TYPESAFE_API_KEY, TYPESAFE_BASE_URL),
and retries 429/529 with exponential backoff, as the API docs advise.

Everything else in this project talks to a `Transport`:
    transport(payload: dict) -> response dict
so tests (and the offline --mock mode) can swap this out entirely.
"""

from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable

DEFAULT_BASE_URL = "https://api.typesafe.ai"
ENDPOINT = "/v1/systemone"
DEFAULT_MODEL = "jev-latest"
RETRY_STATUSES = {429, 529}

Transport = Callable[[dict], dict]


class JevError(RuntimeError):
    pass


def make_http_transport(
    api_key: str,
    base_url: str | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    opener: Callable = urllib.request.urlopen,
) -> Transport:
    url = (base_url or os.environ.get("TYPESAFE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/") + ENDPOINT

    def transport(payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        last_error = "unknown error"
        for attempt in range(retries + 1):
            request = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "ledgersort/0.1",
                },
            )
            try:
                with opener(request, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", "replace")[:300]
                last_error = f"HTTP {err.code}: {detail}"
                if err.code not in RETRY_STATUSES:
                    raise JevError(last_error) from err
                retry_after = err.headers.get("Retry-After") if err.headers else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 ** attempt
            except urllib.error.URLError as err:
                last_error = f"connection failed: {err.reason}"
                delay = 2.0 ** attempt
            if attempt < retries:
                sleep(delay)
        raise JevError(f"giving up after {retries + 1} attempts ({last_error})")

    return transport


def transport_from_env() -> Transport:
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise JevError("TYPESAFE_API_KEY is not set (use --mock or --dry-run to try without one)")
    return make_http_transport(key)
