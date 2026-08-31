"""Tests for the webhook receiver.

These exercise the receiver's handler against the real ``ingest()`` pipeline with
synthetic payloads — the same kind of test the ingest suite runs, but going
through the HTTP path rather than calling ``ingest()`` directly.

The live test (actual Razorpay deliveries) is the receiver itself, run once with
ngrok. These tests cover the machinery.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from http import HTTPStatus
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import requests

from reclaimos.domain import IST
from reclaimos.ingest import compute_signature
from reclaimos.live.webhook_receiver import ReceiverState, WebhookDelivery, _make_handler
from reclaimos.store import Database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_body(
    payment_id: str = "pay_TEST01",
    event: str = "payment.failed",
    extra: dict[str, Any] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "entity": "event",
        "event": event,
        "created_at": 1_780_000_000,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": 49900,
                    "currency": "INR",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_reason": "insufficient_funds",
                    **(extra or {}),
                }
            },
            "subscription": {"entity": {"id": "sub_TEST01"}},
        },
    }
    return json.dumps(payload).encode("utf-8")


SECRET = "test_webhook_secret_for_tests"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@contextmanager
def _receiver_state() -> Iterator[ReceiverState]:
    import reclaimos.live.webhook_receiver as receiver_module

    tmpdir = Path(tempfile.mkdtemp(prefix="reclaimos-webhook-test-"))
    original_log = receiver_module.WEBHOOK_LOG
    receiver_module.WEBHOOK_LOG = tmpdir / "webhooks.jsonl"  # type: ignore[misc]
    db = Database(tmpdir / "reclaimos.db")
    state = ReceiverState(webhook_secret=SECRET, db=db)
    try:
        yield state
    finally:
        db.close()
        receiver_module.WEBHOOK_LOG = original_log  # type: ignore[misc]


def _start_test_server(state: ReceiverState, port: int) -> HTTPServer:
    handler_class = _make_handler(state)
    server = HTTPServer(("127.0.0.1", port), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)
    return server


def _stop_test_server(server: HTTPServer) -> None:
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# ReceiverState unit tests
# ---------------------------------------------------------------------------


def test_receiver_state_records_and_counts() -> None:
    with _receiver_state() as state:
        assert state.count == 0
        report = state.event_id_report()
        assert report["total_deliveries"] == 0
        assert report["with_event_id_header"] == 0
        assert report["without_event_id_header"] == 0


def test_event_id_report_consistent() -> None:
    with _receiver_state() as state:
        d1 = WebhookDelivery(
            received_at=datetime.now(tz=IST),
            method="POST",
            path="/webhook/razorpay",
            headers={},
            body_bytes=100,
            status_returned=200,
            event_id_header="evt_12345",
            ingest_accepted=True,
        )
        d2 = WebhookDelivery(
            received_at=datetime.now(tz=IST),
            method="POST",
            path="/webhook/razorpay",
            headers={},
            body_bytes=100,
            status_returned=200,
            event_id_header="evt_67890",
            ingest_accepted=True,
        )
        state.deliveries = [d1, d2]

        report = state.event_id_report()
        assert report["consistent"] is True
        assert report["with_event_id_header"] == 2
        assert report["without_event_id_header"] == 0


def test_event_id_report_inconsistent() -> None:
    with _receiver_state() as state:
        d1 = WebhookDelivery(
            received_at=datetime.now(tz=IST),
            method="POST",
            path="/webhook/razorpay",
            headers={},
            body_bytes=100,
            status_returned=200,
            event_id_header="evt_12345",
            ingest_accepted=True,
        )
        d2 = WebhookDelivery(
            received_at=datetime.now(tz=IST),
            method="POST",
            path="/webhook/razorpay",
            headers={},
            body_bytes=100,
            status_returned=200,
            event_id_header=None,
            ingest_accepted=True,
        )
        state.deliveries = [d1, d2]

        report = state.event_id_report()
        assert report["consistent"] is False
        assert report["with_event_id_header"] == 1
        assert report["without_event_id_header"] == 1


# ---------------------------------------------------------------------------
# Handler integration test (actual HTTP, in-process)
# ---------------------------------------------------------------------------


def test_handler_accepts_signed_delivery() -> None:
    """A correctly signed delivery goes through the ingest pipeline and returns 200."""
    with _receiver_state() as state:
        port = 18771
        server = _start_test_server(state, port)
        try:
            body = _make_event_body()
            sig = compute_signature(body, SECRET)

            resp = requests.post(
                f"http://127.0.0.1:{port}/webhook/razorpay",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": sig,
                    "X-Razorpay-Event-Id": "evt_test_001",
                },
                timeout=5,
            )
            assert resp.status_code == HTTPStatus.OK
            data = resp.json()
            assert data["accepted"] is True
            assert data["duplicate"] is False

            resp2 = requests.post(
                f"http://127.0.0.1:{port}/webhook/razorpay",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": sig,
                    "X-Razorpay-Event-Id": "evt_test_001",
                },
                timeout=5,
            )
            assert resp2.status_code == HTTPStatus.OK
            data2 = resp2.json()
            assert data2["accepted"] is True
            assert data2["duplicate"] is True
            assert state.count == 2
        finally:
            _stop_test_server(server)


def test_handler_rejects_bad_signature() -> None:
    """A forged or missing signature returns 400."""
    with _receiver_state() as state:
        port = 18772
        server = _start_test_server(state, port)
        try:
            body = _make_event_body()

            resp = requests.post(
                f"http://127.0.0.1:{port}/webhook/razorpay",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "badbadbadbad",
                },
                timeout=5,
            )
            assert resp.status_code == HTTPStatus.BAD_REQUEST
            data = resp.json()
            assert data["accepted"] is False

            resp2 = requests.post(
                f"http://127.0.0.1:{port}/webhook/razorpay",
                data=body,
                headers={"Content-Type": "application/json"},
                timeout=5,
            )
            assert resp2.status_code == HTTPStatus.BAD_REQUEST
            assert state.count == 2
        finally:
            _stop_test_server(server)


def test_handler_returns_404_on_wrong_path() -> None:
    with _receiver_state() as state:
        port = 18773
        server = _start_test_server(state, port)
        try:
            resp = requests.post(
                f"http://127.0.0.1:{port}/wrong/path",
                data=b"{}",
                timeout=5,
            )
            assert resp.status_code == HTTPStatus.NOT_FOUND
        finally:
            _stop_test_server(server)


def test_health_endpoint() -> None:
    with _receiver_state() as state:
        port = 18774
        server = _start_test_server(state, port)
        try:
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=5)
            assert resp.status_code == HTTPStatus.OK
            data = resp.json()
            assert data["status"] == "listening"
            assert "event_id_report" in data
        finally:
            _stop_test_server(server)


def test_event_id_captured_in_delivery() -> None:
    """The event-id header is captured when present, and absent when not."""
    with _receiver_state() as state:
        port = 18775
        server = _start_test_server(state, port)
        try:
            body = _make_event_body(payment_id="pay_EVID01")
            sig = compute_signature(body, SECRET)

            requests.post(
                f"http://127.0.0.1:{port}/webhook/razorpay",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": sig,
                    "X-Razorpay-Event-Id": "evt_present",
                },
                timeout=5,
            )

            body2 = _make_event_body(payment_id="pay_EVID02")
            sig2 = compute_signature(body2, SECRET)
            requests.post(
                f"http://127.0.0.1:{port}/webhook/razorpay",
                data=body2,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": sig2,
                },
                timeout=5,
            )

            report = state.event_id_report()
            assert report["with_event_id_header"] == 1
            assert report["without_event_id_header"] == 1
            assert report["consistent"] is False
        finally:
            _stop_test_server(server)
