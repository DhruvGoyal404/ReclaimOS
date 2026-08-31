"""The policy interface every recovery strategy implements.

Baselines implement it now; the LangGraph agent implements it in Phase 4. Because
the harness only ever talks to this interface, the agent will be measured by
exactly the same code path that produced the baseline numbers -- there is no
separate, friendlier evaluation route for our own work.

A policy sees a ``LoopState`` and nothing else. It cannot reach the sealed world,
which is enforced by construction: ``WorldRecord`` is never a field on this state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from reclaimos.domain import AttemptResult, Decision, SubscriptionRecord


@dataclass(frozen=True)
class LoopState:
    """Everything a policy is allowed to know at one decision point."""

    record: SubscriptionRecord

    #: Hours since the original failed charge.
    elapsed_hours: float = 0.0

    #: Charge attempts this policy has already spent on this record.
    charge_attempts: int = 0

    #: Customer contacts (payment link, card update) already sent.
    contact_actions: int = 0

    #: Index of the action slot about to be filled. Equals ``len(history)``.
    slot: int = 0

    history: tuple[tuple[Decision, AttemptResult], ...] = field(default_factory=tuple)

    @property
    def total_actions(self) -> int:
        return self.charge_attempts + self.contact_actions


@runtime_checkable
class Policy(Protocol):
    """A recovery strategy."""

    name: str
    description: str

    def decide(self, state: LoopState) -> Decision:
        """Choose the next action. Must terminate by returning ``STOP`` eventually."""
        ...
