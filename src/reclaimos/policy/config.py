"""The agent's free parameters — every knob, in one place, versioned and frozen.

Phase 4 is the first time ReclaimOS has anything tunable, which is the first time
the held-out split can be corrupted by tuning. So the knobs live in one frozen
object with a content hash, and the workflow is mechanical:

1. Defaults below are chosen **a priori** from published dunning practice, before
   any measurement.
2. Tuning runs on ``train`` only. ``reclaimos tune`` refuses ``--split test``.
3. The chosen config is written to ``config/agent-frozen.json`` and committed.
4. Every evaluation records the config hash next to its numbers, so a reader can
   tell whether a reported figure came from the frozen config or a local edit.
5. The held-out split is read once, after the freeze, and the read is logged.

If a number in this file changes, the hash changes, and any EVAL.md quoting the
old hash is visibly stale. That is the point.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from reclaimos.config import REPO_ROOT

#: Where the frozen, committed configuration lives.
FROZEN_CONFIG_PATH: Final[Path] = REPO_ROOT / "config" / "agent-frozen.json"


class AgentConfig(BaseModel):
    """Every free parameter the recovery agent has. Fifteen knobs, no hidden ones.

    Anything that changes behaviour belongs here rather than as a literal in the
    policy, so "what was the agent configured to do" has a single answer with a
    single hash.
    """

    model_config = ConfigDict(frozen=True)

    # --- when to retry at all --------------------------------------------
    retry_min_propensity: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Do not spend a charge attempt below this propensity.",
    )
    max_charge_attempts: int = Field(
        default=3,
        ge=0,
        le=8,
        description=(
            "Charge attempts before halting. Razorpay's docs disagree with "
            "themselves (3 vs 4); we start at 3 and let tuning argue."
        ),
    )
    max_contact_actions: int = Field(
        default=2, ge=0, le=4, description="Customer contacts before halting."
    )

    # --- retry timing ------------------------------------------------------
    soft_retry_delays_hours: tuple[float, ...] = Field(
        default=(48.0, 96.0, 168.0),
        description="Hours after the original failure for successive soft retries.",
    )
    technical_retry_delay_hours: float = Field(
        default=36.0,
        gt=0,
        description="Issuer or gateway faults resolve on their own; wait past the outage.",
    )
    limit_exceeded_delay_hours: float = Field(
        default=48.0, gt=0, description="Per-transaction and velocity limits reset on a cycle."
    )

    # --- salary-cycle alignment -------------------------------------------
    payday_alignment: bool = Field(
        default=True,
        description=(
            "For insufficient funds, prefer retrying just after the month turns. "
            "Standard dunning practice; the record carries the calendar we need."
        ),
    )
    payday_buffer_hours: float = Field(
        default=8.0, ge=0, description="Wait this long past the month boundary."
    )
    payday_max_wait_hours: float = Field(
        default=240.0,
        gt=0,
        description="Never delay for payday beyond this; a stale charge recovers poorly.",
    )

    # --- outreach ----------------------------------------------------------
    contact_delay_hours: float = Field(
        default=12.0, ge=0, description="Delay before the first customer contact."
    )
    contact_on_hard_decline: bool = Field(
        default=True,
        description=(
            "A hard decline must never be retried, but the customer can still choose "
            "another instrument. One ask, not a ladder."
        ),
    )
    contact_on_expiry: bool = Field(
        default=True, description="Route expired instruments to an update flow."
    )

    # --- caution -----------------------------------------------------------
    ambiguity_shortens_ladder: bool = Field(
        default=True,
        description=(
            "When the decline tuple might be a hard decline, spend one fewer charge "
            "attempt. Uncertainty should cost attempts, not customers."
        ),
    )

    # --- gates -------------------------------------------------------------
    hitl_amount_threshold_paise: int = Field(
        default=500_000,
        gt=0,
        description="Charges above this go to a human. INR 5,000 by default.",
    )
    recovery_window_hours: float = Field(
        default=720.0, gt=0, description="Write-off horizon; 30 days."
    )

    # --- provenance --------------------------------------------------------
    def fingerprint(self) -> str:
        """Content hash of the configuration, quoted alongside every result."""
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return sha256(body.encode("utf-8")).hexdigest()

    def save(self, path: Path = FROZEN_CONFIG_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "config": self.model_dump(mode="json"),
            "fingerprint": self.fingerprint(),
        }
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
        )
        return path

    @classmethod
    def load(cls, path: Path = FROZEN_CONFIG_PATH) -> AgentConfig:
        """Load the frozen config, verifying it has not been edited in place."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = cls.model_validate(raw["config"])
        if config.fingerprint() != raw["fingerprint"]:
            raise ValueError(
                f"{path} has been edited by hand: its contents no longer match the "
                f"recorded fingerprint. Re-freeze with `reclaimos tune` instead."
            )
        return config

    @classmethod
    def frozen_or_default(cls, path: Path = FROZEN_CONFIG_PATH) -> AgentConfig:
        """The frozen config if one has been committed, else the a-priori defaults."""
        return cls.load(path) if path.exists() else cls()
