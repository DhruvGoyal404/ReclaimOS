"""Records every live Razorpay call, so the slice is evidence rather than a claim.

Two reasons this exists rather than just calling the SDK directly.

**The live slice is a proof, and a proof needs an artefact.** "We integrated with
Razorpay" is worth nothing; a committed transcript of real requests and real
responses, with real ids, is worth something. The recording is the deliverable.

**Test-mode credentials still deserve care.** Nothing secret is written: the key
id is truncated, the secret never touches this module, and response bodies pass
through the same PII redaction the LLM output does. Test mode is not a licence to
be careless with the habit.

The transcript is append-only JSONL under ``data/live/``, one line per call.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from reclaimos.config import REPO_ROOT
from reclaimos.diagnose.redact import redact
from reclaimos.domain import IST

LIVE_DIR: Final[Path] = REPO_ROOT / "data" / "live"
TRANSCRIPT: Final[Path] = LIVE_DIR / "transcript.jsonl"

#: Response fields that must never be written to disk, even in test mode.
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "key_secret",
        "secret",
        "password",
        "token",
        "card_id",
        "vpa",
        "account_number",
        "ifsc",
        "beneficiary_name",
    }
)


class CallRecord(BaseModel):
    """One request and its response, as they actually happened."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    method: str
    path: str
    status: int | None
    ok: bool
    request: dict[str, Any] = {}
    response: dict[str, Any] = {}
    error: str = ""
    note: str = ""


def _scrub(node: Any) -> Any:
    """Redact sensitive keys and any PII in string values, recursively."""
    if isinstance(node, dict):
        return {
            key: ("[redacted]" if key.lower() in SENSITIVE_KEYS else _scrub(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_scrub(item) for item in node]
    if isinstance(node, str):
        return redact(node)
    return node


class Recorder:
    """Append-only transcript of live API calls."""

    def __init__(self, path: Path = TRANSCRIPT) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.calls: list[CallRecord] = []

    def record(
        self,
        method: str,
        path: str,
        *,
        status: int | None = None,
        ok: bool = True,
        request: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        error: str = "",
        note: str = "",
    ) -> CallRecord:
        call = CallRecord(
            at=datetime.now(tz=IST),
            method=method,
            path=path,
            status=status,
            ok=ok,
            request=_scrub(request or {}),
            response=_scrub(response or {}),
            error=redact(error)[:500],
            note=note,
        )
        self.calls.append(call)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(call.model_dump_json() + "\n")
        return call

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for call in self.calls:
            key = f"{call.method} {call.path.split('?')[0]}"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))


def read_transcript(path: Path = TRANSCRIPT) -> list[CallRecord]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [CallRecord.model_validate_json(line) for line in fh if line.strip()]


def write_json(name: str, payload: Any) -> Path:
    """Persist a scrubbed artefact (observed envelopes, reconciliation reports)."""
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = LIVE_DIR / name
    path.write_text(
        json.dumps(_scrub(payload), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
        newline="\n",
    )
    return path
