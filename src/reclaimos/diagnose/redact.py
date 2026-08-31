"""PII redaction for anything a model writes before it is stored or sent.

Small on purpose. This is not a data-loss-prevention product; it is a narrow
filter over text we generate ourselves, sitting in front of an append-only store
where a mistake cannot be deleted afterwards.

It runs on model output rather than only on model input, because the interesting
failure is a model that helpfully echoes a card number back into a dunning
message. Redacting on the way out catches that regardless of how it got there.
"""

from __future__ import annotations

import re
from typing import Final

REDACTED: Final[str] = "[redacted]"

#: Order matters: the longest, most specific patterns run first, so a card-like
#: digit run is not partially eaten by the phone-number pattern.
PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # 13-19 digits, optionally separated -- a card PAN. Never ours to hold.
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # UPI handle: something@bank, but only where the suffix is not a TLD-looking
    # domain (those are caught as emails above).
    ("upi", re.compile(r"\b[\w.\-]{3,}@(?:ok\w+|paytm|ybl|axl|upi|ibl|apl)\b", re.I)),
    # Indian mobile numbers, with or without the country code, and with the
    # internal separator people actually use. An earlier version required ten
    # contiguous digits and sailed straight past "+91 98765 43210" -- caught by
    # the test that feeds real-looking secrets through, not by inspection.
    ("phone", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    # IFSC codes identify a bank branch.
    ("ifsc", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
)


def redact(text: str) -> str:
    """Replace anything that looks like PII with a marker."""
    for _, pattern in PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def contains_pii(text: str) -> bool:
    """True if any pattern still matches. Used as an assertion in tests."""
    return any(pattern.search(text) for _, pattern in PATTERNS)
