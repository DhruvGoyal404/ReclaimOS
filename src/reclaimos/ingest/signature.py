"""Webhook signature verification.

Razorpay signs each webhook with HMAC-SHA256 over the **raw request body**, using
the webhook secret, and sends the hex digest in ``X-Razorpay-Signature``.

Two details that are easy to get wrong and expensive to get wrong:

**Verify the raw bytes, never a re-serialised object.** Parsing JSON and dumping
it again changes whitespace and key order, so the digest no longer matches -- and
the tempting "fix" is to relax verification. The body is passed around as
``bytes`` for exactly this reason.

**Compare in constant time.** A naive ``==`` on the digest leaks how many leading
characters matched, which is enough to forge a signature byte by byte given
enough attempts. ``hmac.compare_digest`` does not.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Razorpay-Signature"
EVENT_ID_HEADER = "X-Razorpay-Event-Id"


def compute_signature(body: bytes, secret: str) -> str:
    """HMAC-SHA256 hex digest of the raw body under ``secret``."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """Constant-time check of a webhook signature.

    Returns ``False`` rather than raising for a missing or malformed signature:
    an unsigned request is an ordinary rejection to be recorded, not an
    exceptional condition. An empty secret always fails, so a misconfigured
    deployment refuses everything instead of accepting everything -- the safer
    direction to fail by a wide margin.
    """
    if not signature or not secret:
        return False
    return hmac.compare_digest(compute_signature(body, secret), signature.strip())
