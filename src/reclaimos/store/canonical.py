"""Canonical serialisation and the hash-chain primitives.

A hash chain is only tamper-evident if the bytes being hashed are reproducible.
Two machines rendering the same decision must produce the same string, or
verification fails for boring reasons and stops being believed.

Canonical form is: sorted keys, no insignificant whitespace, UTF-8, datetimes as
ISO-8601 with an explicit offset, enums as their values. Deliberately *not*
``ensure_ascii``: a Hinglish dunning message is going into these payloads, and
escaping it to ``\\uXXXX`` would make the ledger unreadable to the humans it
exists for. UTF-8 encoding is fixed and explicit instead.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any, Final

#: The chain starts here. A fixed, obviously-synthetic value so the first real
#: entry has something to link to and nobody mistakes it for a hash of content.
GENESIS_HASH: Final[str] = "0" * 64


class NonCanonicalPayload(ValueError):
    """Raised when a payload cannot be canonicalised safely."""


def _encode(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    raise NonCanonicalPayload(f"cannot canonicalise {type(value).__name__}: {value!r}")


def _reject_float_money(node: Any, path: str = "") -> None:
    """Refuse a float in any ``*_paise`` field, at the serialisation boundary.

    ``tests/test_money.py`` already forbids float money *annotations*; this
    catches the dynamic case, where a dict assembled at runtime carries a float
    into the permanent record. The ledger is append-only, so a rounding error
    written here can never be corrected -- only annotated by a later entry.
    """
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key).endswith("_paise") and isinstance(value, float):
                raise NonCanonicalPayload(
                    f"{child} is a float ({value!r}); money is integer paise, always"
                )
            _reject_float_money(value, child)
    elif isinstance(node, list | tuple):
        for index, value in enumerate(node):
            _reject_float_money(value, f"{path}[{index}]")


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Render a payload to its one canonical string form."""
    _reject_float_money(payload)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_encode,
    )


def sha256_hex(data: str | bytes) -> str:
    """Hex SHA-256 of a string (UTF-8) or bytes."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def chain_hash(prev_hash: str, payload_json: str) -> str:
    """The link: ``sha256(prev_hash || canonical_json(payload))``.

    Concatenating the previous hash *inside* the digest is what makes the chain
    tamper-evident: editing entry N changes its hash, which breaks the link every
    later entry depends on, so a forger has to rewrite the entire tail.
    """
    return sha256_hex(prev_hash + payload_json)
