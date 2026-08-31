"""A thin, recorded HTTP client for Razorpay test mode.

Raw ``requests`` rather than the Razorpay SDK, for one specific reason: the SDK
wraps a non-2xx response in an exception whose message is often empty, which is
exactly what happened on the first probe of this account —
``ServerError:`` with nothing after the colon. The status code and body are the
evidence the live slice exists to collect, so they must not be swallowed.

The SDK is still a dependency and is used by the gateway wrapper for writes; this
client is the instrument, not the integration.

Every call goes through the ``Recorder``. Nothing here reads or writes anything
outside Razorpay test mode, and the credential loader refuses a live key
(``config.RazorpayCredentials``).
"""

from __future__ import annotations

from typing import Any, Final

import requests

from reclaimos.config import load_razorpay_credentials
from reclaimos.live.recorder import Recorder

BASE_URL: Final[str] = "https://api.razorpay.com/v1"
TIMEOUT: Final[float] = 30.0


class RazorpayLiveClient:
    """Recorded access to Razorpay test mode."""

    def __init__(self, recorder: Recorder | None = None) -> None:
        credentials = load_razorpay_credentials()  # refuses anything but rzp_test_
        self._auth = (credentials.key_id, credentials.key_secret)
        self.key_id = credentials.key_id
        self.recorder = recorder or Recorder()

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        note: str = "",
    ) -> tuple[int, Any]:
        url = f"{BASE_URL}{path}"
        try:
            response = requests.request(
                method,
                url,
                auth=self._auth,
                params=params,
                json=json_body,
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            self.recorder.record(
                method,
                path,
                status=None,
                ok=False,
                request=json_body or params or {},
                error=f"{type(exc).__name__}: {exc}",
                note=note,
            )
            raise

        try:
            body = response.json()
        except ValueError:
            body = {"_raw": response.text[:1000]}

        self.recorder.record(
            method,
            path,
            status=response.status_code,
            ok=response.ok,
            request=json_body or params or {},
            response=body if isinstance(body, dict) else {"items": body},
            error="" if response.ok else str(body)[:500],
            note=note,
        )
        return response.status_code, body

    # --- reads -------------------------------------------------------------

    def get(
        self, path: str, params: dict[str, Any] | None = None, note: str = ""
    ) -> tuple[int, Any]:
        return self._call("GET", path, params=params, note=note)

    # --- writes (test mode only) -------------------------------------------

    def post(self, path: str, body: dict[str, Any], note: str = "") -> tuple[int, Any]:
        return self._call("POST", path, json_body=body, note=note)

    # --- capability probing -------------------------------------------------

    def probe_endpoints(self, paths: tuple[str, ...]) -> dict[str, int]:
        """Which parts of the API this account can actually reach.

        Worth doing explicitly and recording: a 401 on one endpoint while others
        return 200 is a provisioning fact about the account, not a broken key,
        and the distinction is invisible if you only ever call one endpoint.
        """
        results: dict[str, int] = {}
        for path in paths:
            status, _ = self.get(path, {"count": 1}, note="capability probe")
            results[path] = status
        return results
