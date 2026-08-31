"""Dataset assembly: synthetic subscriptions, sealed world, split, manifest.

The generator emits three kinds of file per run:

``<split>.jsonl``
    ``SubscriptionRecord`` lines. This is all a policy ever sees.

``<split>.world.json``
    The sealed ``WorldRecord`` truth. Loaded only by the eval harness.

``manifest.json``
    Seed, generator version, realised failure mix, and a SHA-256 for every file.
    The test-split hash is quoted in EVAL.md so a reader can verify that the
    numbers were produced against the split we claim.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from reclaimos.domain import (
    IST,
    DeclineClass,
    Mandate,
    Method,
    PaymentAttempt,
    SubscriptionRecord,
    codes_for,
)
from reclaimos.generator import profiles
from reclaimos.generator.outcome_model import WorldRecord, draw_world_record

#: Bump when a change alters the data or the world physics, so old runs are never
#: silently compared against new ones.
GENERATOR_VERSION: Final[str] = "1.0.0"

#: The window failed charges are drawn from. Fixed so datasets are comparable.
WINDOW_START: Final[datetime] = datetime(2026, 6, 1, tzinfo=IST)
WINDOW_MONTHS: Final[int] = 2

_ID_ALPHABET: Final[str] = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"


def _razorpay_style_id(rng: random.Random, prefix: str) -> str:
    """A 14-character id in Razorpay's shape, so nothing has to be reshaped later."""
    return prefix + "".join(rng.choices(_ID_ALPHABET, k=14))


def _sample_charge_at(rng: random.Random, billing_cycle_day: int) -> datetime:
    """Place the failed charge on a billing day inside the window.

    The hour distribution reflects how recurring charges are actually presented:
    mostly in an overnight batch, with a tail across the day for retries of the
    merchant's own scheduling.
    """
    month_offset = rng.randrange(WINDOW_MONTHS)
    month = WINDOW_START.month + month_offset
    year = WINDOW_START.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    hour = rng.choices([2, 3, 4, 9, 13, 18], weights=[0.30, 0.22, 0.16, 0.12, 0.10, 0.10])[0]
    return datetime(year, month, billing_cycle_day, hour, rng.randrange(60), tzinfo=IST)


def _sample_mandate(
    rng: random.Random,
    plan_amount_paise: int,
    method: Method,
    charge_at: datetime,
    true_class: DeclineClass,
) -> Mandate:
    """Draw the consent envelope for this subscription.

    Two details that matter downstream:

    * The cap is a multiple of the plan price, sometimes exactly 1x. A tight
      envelope is realistic and gives the mandate check something real to refuse.
    * When the true failure is an expired mandate, the expiry is placed *before*
      the charge. The dataset therefore stays internally consistent, and any
      policy that blindly retries such a record commits a real mandate violation
      -- which is exactly the safety metric we want to be able to measure.
    """
    multiple = rng.choices([1, 2, 3], weights=[0.25, 0.55, 0.20])[0]
    if true_class is DeclineClass.EXPIRY_MANDATE_EXPIRED:
        expiry = charge_at - timedelta(days=rng.randint(1, 30))
    else:
        expiry = charge_at + timedelta(days=rng.randint(120, 900))
    return Mandate(
        max_amount_paise=plan_amount_paise * multiple,
        expiry=expiry,
        allowed_method=method,
        reason_code="subscription_recovery",
    )


def generate(
    n: int,
    seed: int,
) -> tuple[list[SubscriptionRecord], dict[str, WorldRecord]]:
    """Generate ``n`` failed subscriptions plus their sealed world state."""
    rng = random.Random(seed)
    records: list[SubscriptionRecord] = []
    world: dict[str, WorldRecord] = {}

    for _ in range(n):
        method = profiles.sample_method(rng)
        true_class = profiles.sample_decline_class(rng, method)
        plan_amount = profiles.sample_plan_amount_paise(rng)
        tenure = profiles.sample_tenure_months(rng)
        cycle_day = profiles.sample_billing_cycle_day(rng)
        charge_at = _sample_charge_at(rng, cycle_day)

        subscription_id = _razorpay_style_id(rng, "sub_")
        candidates = codes_for(true_class)
        code = candidates[rng.randrange(len(candidates))]

        prior_failures = rng.choices([0, 1, 2, 3], weights=[0.62, 0.24, 0.10, 0.04])[0]

        record = SubscriptionRecord(
            subscription_id=subscription_id,
            customer_id=_razorpay_style_id(rng, "cust_"),
            plan_id=_razorpay_style_id(rng, "plan_"),
            method=method,
            plan_amount_paise=plan_amount,
            billing_cycle_day=cycle_day,
            charge_at=charge_at,
            customer_tenure_months=tenure,
            prior_success_count=max(0, tenure - rng.randint(0, 2)),
            prior_failure_count=prior_failures,
            mandate=_sample_mandate(rng, plan_amount, method, charge_at, true_class),
            failed_attempt=PaymentAttempt(
                attempt_no=1,
                occurred_at=charge_at,
                amount_paise=plan_amount,
                succeeded=False,
                error_code=code.code,
                error_source=code.source,
                error_step=code.step,
                error_reason=code.reason,
                error_description=code.description,
            ),
        )
        records.append(record)
        world[subscription_id] = draw_world_record(
            rng, subscription_id, true_class, charge_at, tenure
        )

    return records, world


def split_records(
    records: list[SubscriptionRecord],
    test_fraction: float = 0.30,
) -> tuple[list[SubscriptionRecord], list[SubscriptionRecord]]:
    """Split into train and test, deterministically and exactly.

    Ordering by a hash of the subscription id -- rather than shuffling -- means the
    assignment depends only on the id. Regenerating with more records leaves every
    existing record on the side it was already on, so the held-out split cannot
    quietly absorb data we have already looked at.
    """
    ordered = sorted(records, key=lambda r: hashlib.sha256(r.subscription_id.encode()).hexdigest())
    n_test = round(len(ordered) * test_fraction)
    return ordered[n_test:], ordered[:n_test]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_records(path: Path, records: list[SubscriptionRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(record.model_dump_json() + "\n")


def read_records(path: Path) -> list[SubscriptionRecord]:
    with path.open(encoding="utf-8") as fh:
        return [SubscriptionRecord.model_validate_json(line) for line in fh if line.strip()]


def write_world(path: Path, world: dict[str, WorldRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {sid: rec.model_dump() for sid, rec in world.items()}
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8", newline="\n"
    )


def read_world(path: Path) -> dict[str, WorldRecord]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {sid: WorldRecord.model_validate(rec) for sid, rec in raw.items()}


class Manifest(BaseModel):
    """Provenance for one generated dataset."""

    generator_version: str
    seed: int
    created_at: datetime
    n_total: int
    n_train: int
    n_test: int
    class_mix: dict[str, float]
    family_mix: dict[str, float]
    sha256: dict[str, str]

    def summary(self) -> str:
        return (
            f"generator {self.generator_version} · seed {self.seed} · "
            f"{self.n_total} records ({self.n_train} train / {self.n_test} test)"
        )


def build_dataset(out_dir: Path, n: int, seed: int, test_fraction: float = 0.30) -> Manifest:
    """Generate, split, write, and return the manifest."""
    records, world = generate(n, seed)
    train, test = split_records(records, test_fraction)

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, subset in (("train", train), ("test", test)):
        write_records(out_dir / f"{name}.jsonl", subset)
        write_world(
            out_dir / f"{name}.world.json",
            {r.subscription_id: world[r.subscription_id] for r in subset},
        )

    counts: dict[str, int] = {}
    families = {"soft": 0, "hard": 0, "expiry": 0, "unknown": 0}
    for record in records:
        cls = world[record.subscription_id].true_class
        counts[cls.value] = counts.get(cls.value, 0) + 1
        if cls.is_soft:
            families["soft"] += 1
        elif cls.is_hard:
            families["hard"] += 1
        elif cls.is_expiry:
            families["expiry"] += 1
        else:
            families["unknown"] += 1

    manifest = Manifest(
        generator_version=GENERATOR_VERSION,
        seed=seed,
        created_at=datetime.now(tz=IST),
        n_total=len(records),
        n_train=len(train),
        n_test=len(test),
        class_mix={k: round(v / len(records), 4) for k, v in sorted(counts.items())},
        family_mix={k: round(v / len(records), 4) for k, v in families.items()},
        sha256={f"{name}.jsonl": _sha256(out_dir / f"{name}.jsonl") for name in ("train", "test")},
    )
    (out_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8", newline="\n"
    )
    return manifest


def read_manifest(out_dir: Path) -> Manifest:
    return Manifest.model_validate_json((out_dir / "manifest.json").read_text(encoding="utf-8"))
