"""Signature-verified webhook ingestion. Verify, then normalise, then append."""

from reclaimos.ingest.signature import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    compute_signature,
    verify_signature,
)
from reclaimos.ingest.webhook import (
    REJECTED_EVENT_TYPE,
    DerivedEventId,
    IngestResult,
    derive_event_id,
    ingest,
    parse,
)

__all__ = [
    "EVENT_ID_HEADER",
    "REJECTED_EVENT_TYPE",
    "SIGNATURE_HEADER",
    "DerivedEventId",
    "IngestResult",
    "compute_signature",
    "derive_event_id",
    "ingest",
    "parse",
    "verify_signature",
]
