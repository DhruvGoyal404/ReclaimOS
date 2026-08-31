"""Reconcile what Razorpay actually sends against what we modelled.

Two gaps were logged in `docs/failure-log.md` before this slice existed, both
labelled "modelled, not observed":

1. The decline-code taxonomy in ``domain/decline_codes.py`` — error tuples written
   from the shape of the API rather than from captured payloads.
2. The webhook envelope in ``ingest/webhook.py``, including whether
   ``X-Razorpay-Event-Id`` is always present.

This module compares observation to model and produces a report. A mismatch is
the point of the exercise, not a problem with it: "we modelled X, the API sends
Y, we changed Z" is worth more than a taxonomy that happened to be right.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from reclaimos.domain import DECLINE_CODES


class TupleObservation(BaseModel):
    """One error envelope seen in the wild."""

    model_config = ConfigDict(frozen=True)

    error_code: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    error_reason: str | None = None
    error_description: str | None = None
    payment_id: str | None = None
    method: str | None = None

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (
            self.error_code or "",
            self.error_source or "",
            self.error_step or "",
            self.error_reason or "",
        )


class Reconciliation(BaseModel):
    """What we modelled, what arrived, and where they disagree."""

    model_config = ConfigDict(frozen=True)

    observed: list[TupleObservation]
    modelled_tuples: list[list[str]]
    matched: list[list[str]]
    unmodelled: list[list[str]]
    fields_seen: list[str]
    fields_missing: list[str]
    notes: list[str]

    @property
    def clean(self) -> bool:
        return not self.unmodelled and not self.fields_missing

    def summary(self) -> str:
        if not self.observed:
            return "no failed payments observed yet — nothing to reconcile"
        if self.clean:
            return (
                f"{len(self.observed)} envelope(s) observed; every tuple matches the "
                "modelled taxonomy"
            )
        return (
            f"{len(self.observed)} envelope(s) observed; {len(self.unmodelled)} tuple(s) "
            f"not in our taxonomy, {len(self.fields_missing)} expected field(s) absent"
        )


#: Fields the classifier reads. If the live envelope omits one, the classifier is
#: reading something Razorpay does not send, and that is a real defect.
EXPECTED_FIELDS: tuple[str, ...] = (
    "error_code",
    "error_source",
    "error_step",
    "error_reason",
    "error_description",
)


def observations_from_payments(payments: list[dict[str, Any]]) -> list[TupleObservation]:
    """Pull error envelopes out of a Razorpay payments listing."""
    seen: list[TupleObservation] = []
    for payment in payments:
        if payment.get("status") != "failed" and not payment.get("error_code"):
            continue
        seen.append(
            TupleObservation(
                error_code=payment.get("error_code"),
                error_source=payment.get("error_source"),
                error_step=payment.get("error_step"),
                error_reason=payment.get("error_reason"),
                error_description=payment.get("error_description"),
                payment_id=payment.get("id"),
                method=payment.get("method"),
            )
        )
    return seen


def reconcile(observations: list[TupleObservation]) -> Reconciliation:
    """Compare observed envelopes with the modelled taxonomy."""
    modelled = {(c.code, c.source, c.step, c.reason) for c in DECLINE_CODES}

    matched: list[tuple[str, str, str, str]] = []
    unmodelled: list[tuple[str, str, str, str]] = []
    for observation in observations:
        (matched if observation.key in modelled else unmodelled).append(observation.key)

    fields_seen: set[str] = set()
    for observation in observations:
        for field in EXPECTED_FIELDS:
            if getattr(observation, field, None):
                fields_seen.add(field)

    missing = [f for f in EXPECTED_FIELDS if observations and f not in fields_seen]

    notes: list[str] = []
    if unmodelled:
        notes.append(
            "Tuples arrived that our taxonomy does not contain. Add them to "
            "domain/decline_codes.py with the class they represent, and record the "
            "diff in docs/failure-log.md."
        )
    if missing:
        notes.append(
            f"The classifier reads {missing}, which the live envelope did not carry. "
            "Either the field is optional and the classifier must tolerate its "
            "absence, or the mapping is wrong."
        )
    if not observations:
        notes.append(
            "No failed payment has been captured yet. A payment link paid with a "
            "failing test card produces one; see docs/live-slice.md."
        )

    return Reconciliation(
        observed=observations,
        modelled_tuples=sorted([list(t) for t in modelled]),
        matched=[list(m) for m in sorted(set(matched))],
        unmodelled=[list(u) for u in sorted(set(unmodelled))],
        fields_seen=sorted(fields_seen),
        fields_missing=missing,
        notes=notes,
    )
