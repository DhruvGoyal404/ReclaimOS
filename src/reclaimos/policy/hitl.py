"""The human-in-the-loop review queue.

The contract, stated plainly because a "gate" that lets the action through while
someone thinks about it is not a gate:

* **An escalated action does not execute.** ``submit`` returns a pending item and
  the executor is never called. There is no timeout that auto-approves.
* **The batch continues.** Escalating one subscription blocks that subscription
  and nothing else.
* **Escalation is a first-class outcome**, recorded in the ledger with the amount
  withheld, not an error or an absence.

In evaluation, escalations are **counted, never resolved** — see
``reclaimos.eval.metrics``. No simulated reviewer approves anything, so escalated
money is simply not recovered. That is deliberately the unflattering choice: it
means a policy cannot improve its safety numbers by escalating everything,
because every escalation costs it recovery in the headline figure.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from reclaimos.domain import IST, ActionType


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewItem(BaseModel):
    """One action awaiting a human decision."""

    model_config = ConfigDict(frozen=True)

    review_id: str
    subscription_id: str
    action: ActionType
    amount_paise: int = Field(ge=0)
    reason: str
    rule_id: str
    created_at: datetime
    status: ReviewStatus = ReviewStatus.PENDING
    resolved_at: datetime | None = None
    resolved_by: str | None = None

    @property
    def pending(self) -> bool:
        return self.status is ReviewStatus.PENDING

    def as_ledger_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ReviewQueue:
    """In-process queue. Submitting is *instead of* executing, never alongside it."""

    def __init__(self) -> None:
        self._items: dict[str, ReviewItem] = {}

    def submit(
        self,
        subscription_id: str,
        action: ActionType,
        amount_paise: int,
        reason: str,
        rule_id: str,
        at: datetime | None = None,
    ) -> ReviewItem:
        item = ReviewItem(
            review_id=f"rev_{uuid4().hex[:16]}",
            subscription_id=subscription_id,
            action=action,
            amount_paise=amount_paise,
            reason=reason,
            rule_id=rule_id,
            created_at=at or datetime.now(tz=IST),
        )
        self._items[item.review_id] = item
        return item

    def _resolve(self, review_id: str, status: ReviewStatus, by: str) -> ReviewItem:
        item = self._items[review_id]
        if not item.pending:
            raise ValueError(f"{review_id} is already {item.status.value}")
        resolved = item.model_copy(
            update={"status": status, "resolved_at": datetime.now(tz=IST), "resolved_by": by}
        )
        self._items[review_id] = resolved
        return resolved

    def approve(self, review_id: str, by: str = "operator") -> ReviewItem:
        return self._resolve(review_id, ReviewStatus.APPROVED, by)

    def reject(self, review_id: str, by: str = "operator") -> ReviewItem:
        return self._resolve(review_id, ReviewStatus.REJECTED, by)

    def pending(self) -> list[ReviewItem]:
        return [i for i in self._items.values() if i.pending]

    def all(self) -> list[ReviewItem]:
        return list(self._items.values())

    def gated_paise(self) -> int:
        """Money withheld pending review. Reported in EVAL.md as its own column."""
        return sum(i.amount_paise for i in self._items.values() if i.pending)
