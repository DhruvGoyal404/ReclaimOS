"""A minimal webhook receiver for the live test-mode slice.

This is **not** a production HTTP server. It exists for one session: start it,
tunnel it with ngrok, configure the dashboard, trigger a payment, capture the
delivery, stop. The entire lifecycle is one person at a terminal.

What it records, and why:

1. Every inbound header set, because the open question from failure-log entry #2
   is whether ``X-Razorpay-Event-Id`` is present on every delivery. We cannot
   answer that without seeing the headers Razorpay actually sends.
2. Every delivery is run through the real ``ingest()`` pipeline -- signature
   verification, parsing, event store -- so the same code path used by the eval
   harness is exercised against real traffic.
3. A JSONL log of raw deliveries (``data/live/webhooks.jsonl``) supplements the
   transcript. The two together are the evidence.

Dependencies: only the stdlib ``http.server``. No FastAPI, no Flask, no new
dependency -- a clean clone already has everything this needs.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from reclaimos.config import REPO_ROOT, load_razorpay_credentials
from reclaimos.diagnose.redact import redact
from reclaimos.domain import IST
from reclaimos.ingest import EVENT_ID_HEADER, SIGNATURE_HEADER, IngestResult, ingest
from reclaimos.store import Database, EventStore

WEBHOOK_LOG: Final[Path] = REPO_ROOT / "data" / "live" / "webhooks.jsonl"

# Headers worth capturing for the open question about event-id presence.
INTERESTING_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "x-razorpay-event-id",
        "x-razorpay-signature",
        "content-type",
        "x-razorpay-event",
        "user-agent",
    }
)


class WebhookDelivery(BaseModel):
    """One raw delivery, exactly as it arrived."""

    model_config = ConfigDict(frozen=True)

    received_at: datetime
    method: str
    path: str
    headers: dict[str, str]
    body_bytes: int
    status_returned: int
    event_id_header: str | None = None
    signature_header: str | None = None
    ingest_accepted: bool = False
    ingest_duplicate: bool = False
    ingest_reason: str = ""
    body_preview: str = ""


class ReceiverState:
    """Shared state between the handler and the CLI. Thread-safe via a lock."""

    def __init__(self, webhook_secret: str, db: Database) -> None:
        self.webhook_secret = webhook_secret
        self.event_store = EventStore(db)
        self.deliveries: list[WebhookDelivery] = []
        self._lock = threading.Lock()
        WEBHOOK_LOG.parent.mkdir(parents=True, exist_ok=True)

    def record(self, delivery: WebhookDelivery) -> None:
        with self._lock:
            self.deliveries.append(delivery)
            with WEBHOOK_LOG.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(delivery.model_dump_json() + "\n")

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.deliveries)

    def event_id_report(self) -> dict[str, Any]:
        """Summarise the open question: is X-Razorpay-Event-Id on every delivery?"""
        with self._lock:
            total = len(self.deliveries)
            with_id = sum(1 for d in self.deliveries if d.event_id_header)
            without_id = total - with_id
            ids_seen = [d.event_id_header for d in self.deliveries if d.event_id_header]
        return {
            "total_deliveries": total,
            "with_event_id_header": with_id,
            "without_event_id_header": without_id,
            "consistent": total > 0 and without_id == 0,
            "event_ids": ids_seen,
        }


def _make_handler(state: ReceiverState) -> type[BaseHTTPRequestHandler]:
    """Factory: returns a handler class bound to the shared state."""

    class WebhookHandler(BaseHTTPRequestHandler):
        """Handles POST /webhook/razorpay only. Everything else gets 404."""

        def do_POST(self) -> None:
            # Always read the request body, even on paths we reject: on Windows,
            # replying before draining the body makes the client socket abort
            # (WinError 10053) instead of reading our response cleanly.
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            if self.path.rstrip("/") != "/webhook/razorpay":
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()
                return

            # Capture headers for the event-id question.
            captured_headers: dict[str, str] = {}
            for header_name in INTERESTING_HEADERS:
                value = self.headers.get(header_name)
                if value:
                    captured_headers[header_name] = value

            event_id_header = self.headers.get(EVENT_ID_HEADER.lower()) or self.headers.get(
                EVENT_ID_HEADER
            )
            signature_header = self.headers.get(SIGNATURE_HEADER.lower()) or self.headers.get(
                SIGNATURE_HEADER
            )

            # Run through the real ingest pipeline.
            result: IngestResult = ingest(
                body=body,
                signature=signature_header,
                secret=state.webhook_secret,
                store=state.event_store,
                event_id=event_id_header,
            )

            # Build a safe preview of the body for logging (redacted, truncated).
            try:
                raw = json.loads(body.decode("utf-8"))
                preview = redact(json.dumps(raw, indent=None, sort_keys=True)[:500])
            except (UnicodeDecodeError, json.JSONDecodeError):
                preview = f"<{len(body)} bytes, not valid JSON>"

            delivery = WebhookDelivery(
                received_at=datetime.now(tz=IST),
                method="POST",
                path=self.path,
                headers=captured_headers,
                body_bytes=len(body),
                status_returned=200 if result.accepted else 400,
                event_id_header=event_id_header,
                signature_header="[present]" if signature_header else None,
                ingest_accepted=result.accepted,
                ingest_duplicate=result.duplicate,
                ingest_reason=result.reason,
                body_preview=preview,
            )
            state.record(delivery)

            # Razorpay expects 2xx to stop retrying. We return 200 on accepted,
            # 400 on rejected (bad signature), which will cause a retry — correct
            # behaviour, because we genuinely do not want to acknowledge a forgery.
            status = HTTPStatus.OK if result.accepted else HTTPStatus.BAD_REQUEST
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {
                "accepted": result.accepted,
                "duplicate": result.duplicate,
                "reason": result.reason,
            }
            self.wfile.write(json.dumps(response).encode("utf-8"))

        def do_GET(self) -> None:
            """Health check / status endpoint."""
            if self.path.rstrip("/") == "/health":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                report = state.event_id_report()
                response = {
                    "status": "listening",
                    "deliveries": state.count,
                    "event_id_report": report,
                }
                self.wfile.write(json.dumps(response).encode("utf-8"))
                return
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            """Override to prefix with our own tag so it's distinguishable from noise."""
            # BaseHTTPRequestHandler.log_message expects exactly this signature.
            import sys

            sys.stderr.write(f"[webhook-receiver] {self.address_string()} - {format % args}\n")

    return WebhookHandler


def run_receiver(
    port: int = 8000,
    webhook_secret: str | None = None,
    db_url: str | None = None,
) -> None:
    """Start the webhook receiver. Blocks until Ctrl+C."""
    from reclaimos.config import settings

    if webhook_secret is None:
        creds = load_razorpay_credentials()
        webhook_secret = creds.webhook_secret
        if not webhook_secret:
            raise ValueError(
                "RAZORPAY_WEBHOOK_SECRET is not set in .env. "
                "Set it to the same value you configure in the Razorpay dashboard."
            )

    db = Database.from_url(db_url or settings.database_url)
    state = ReceiverState(webhook_secret=webhook_secret, db=db)
    handler_class = _make_handler(state)

    server = HTTPServer(("0.0.0.0", port), handler_class)
    import sys

    print(f"[webhook-receiver] listening on http://0.0.0.0:{port}", file=sys.stderr)
    print("[webhook-receiver] webhook path: POST /webhook/razorpay", file=sys.stderr)
    print("[webhook-receiver] health check: GET /health", file=sys.stderr)
    print(f"[webhook-receiver] log: {WEBHOOK_LOG}", file=sys.stderr)
    print("[webhook-receiver] Ctrl+C to stop and print summary", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        report = state.event_id_report()
        print("\n" + "=" * 60, file=sys.stderr)
        print("[webhook-receiver] SESSION SUMMARY", file=sys.stderr)
        print(f"  deliveries received: {report['total_deliveries']}", file=sys.stderr)
        print(
            f"  with X-Razorpay-Event-Id: {report['with_event_id_header']}",
            file=sys.stderr,
        )
        print(
            f"  without X-Razorpay-Event-Id: {report['without_event_id_header']}",
            file=sys.stderr,
        )
        if report["total_deliveries"] > 0:
            if report["consistent"]:
                print(
                    "  → CONSISTENT: event-id header present on every delivery",
                    file=sys.stderr,
                )
                print(
                    "    Action: harden event-id derivation to use it; "
                    "retire evt_nodedupe_ fallback",
                    file=sys.stderr,
                )
            else:
                print(
                    "  → INCONSISTENT: event-id header missing on some deliveries",
                    file=sys.stderr,
                )
                print(
                    "    Action: log as sixth honest finding; "
                    "evt_nodedupe_ fallback is load-bearing",
                    file=sys.stderr,
                )
        else:
            print("  → no deliveries received", file=sys.stderr)
        print(f"  event ids seen: {report['event_ids']}", file=sys.stderr)
        print(f"  log written to: {WEBHOOK_LOG}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
