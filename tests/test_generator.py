"""Generator conformance: the failure mix, internal consistency, determinism."""

from __future__ import annotations

import random

from reclaimos.domain import DECLINE_CODES, DeclineClass, Method, SubscriptionRecord
from reclaimos.generator import build_dataset, generate, read_records, read_world, split_records
from reclaimos.generator.generate import read_manifest
from reclaimos.generator.outcome_model import MAX_SLOTS
from reclaimos.generator.profiles import (
    AGGREGATE_BANDS,
    CLASS_WEIGHTS_BY_METHOD,
    METHOD_WEIGHTS,
    marginal_family_weights,
)

WorldMap = dict[str, object]


# --- the failure mix -------------------------------------------------------


def test_method_weights_sum_to_one() -> None:
    assert abs(sum(METHOD_WEIGHTS.values()) - 1.0) < 1e-9


def test_class_weights_sum_to_one_per_method() -> None:
    for method, weights in CLASS_WEIGHTS_BY_METHOD.items():
        assert abs(sum(weights.values()) - 1.0) < 1e-9, method


def test_marginal_mix_lands_inside_the_target_bands() -> None:
    """The per-rail tables must roll up into the bands the brief calls for."""
    marginal = marginal_family_weights()
    for family, (low, high) in AGGREGATE_BANDS.items():
        assert low <= marginal[family] <= high, f"{family}={marginal[family]:.4f}"


def test_realised_mix_tracks_the_theoretical_mix(
    dataset: tuple[list[SubscriptionRecord], WorldMap],
) -> None:
    records, world = dataset
    theoretical = marginal_family_weights()
    counts = {"soft": 0, "hard": 0, "expiry": 0, "unknown": 0}
    for record in records:
        cls = world[record.subscription_id].true_class  # type: ignore[union-attr]
        if cls.is_soft:
            counts["soft"] += 1
        elif cls.is_hard:
            counts["hard"] += 1
        elif cls.is_expiry:
            counts["expiry"] += 1
        else:
            counts["unknown"] += 1
    for family, count in counts.items():
        realised = count / len(records)
        assert abs(realised - theoretical[family]) < 0.10, f"{family}: {realised:.3f}"


# --- internal consistency --------------------------------------------------


def test_card_expiry_never_happens_off_the_card_rail() -> None:
    """A UPI mandate has no card to expire. A flat mix would emit this anyway."""
    records, world = generate(800, seed=3)
    for record in records:
        if record.method is not Method.CARD:
            assert world[record.subscription_id].true_class is not DeclineClass.EXPIRY_CARD_EXPIRED


def test_every_error_tuple_exists_in_the_taxonomy(
    dataset: tuple[list[SubscriptionRecord], WorldMap],
) -> None:
    records, _ = dataset
    known = {(c.code, c.source, c.step, c.reason) for c in DECLINE_CODES}
    for record in records:
        attempt = record.failed_attempt
        assert (
            attempt.error_code,
            attempt.error_source,
            attempt.error_step,
            attempt.error_reason,
        ) in known


def test_expired_mandate_records_really_have_an_expired_mandate(
    dataset: tuple[list[SubscriptionRecord], WorldMap],
) -> None:
    """The dataset must not contradict itself, or the safety metric is fiction."""
    records, world = dataset
    for record in records:
        cls = world[record.subscription_id].true_class  # type: ignore[union-attr]
        expired = record.mandate.expiry < record.charge_at
        assert expired == (cls is DeclineClass.EXPIRY_MANDATE_EXPIRED), record.subscription_id


def test_mandate_always_covers_the_plan_amount(
    dataset: tuple[list[SubscriptionRecord], WorldMap],
) -> None:
    records, _ = dataset
    for record in records:
        assert record.mandate.max_amount_paise >= record.plan_amount_paise
        assert record.mandate.allowed_method is record.method


def test_every_record_has_one_draw_per_slot(
    dataset: tuple[list[SubscriptionRecord], WorldMap],
) -> None:
    _, world = dataset
    for truth in world.values():
        assert len(truth.draws) == MAX_SLOTS  # type: ignore[union-attr]
        assert all(0.0 <= d < 1.0 for d in truth.draws)  # type: ignore[union-attr]


# --- determinism and splitting ---------------------------------------------


def test_same_seed_produces_byte_identical_records() -> None:
    a_records, a_world = generate(80, seed=11)
    b_records, b_world = generate(80, seed=11)
    assert [r.model_dump_json() for r in a_records] == [r.model_dump_json() for r in b_records]
    assert {k: v.model_dump_json() for k, v in a_world.items()} == {
        k: v.model_dump_json() for k, v in b_world.items()
    }


def test_different_seeds_produce_different_records() -> None:
    a, _ = generate(80, seed=11)
    b, _ = generate(80, seed=12)
    assert {r.subscription_id for r in a} != {r.subscription_id for r in b}


def test_split_is_exact_and_disjoint(
    dataset: tuple[list[SubscriptionRecord], WorldMap],
) -> None:
    records, _ = dataset
    train, test = split_records(records, 0.30)
    assert len(test) == round(len(records) * 0.30)
    assert len(train) + len(test) == len(records)
    assert not {r.subscription_id for r in train} & {r.subscription_id for r in test}


def test_split_depends_only_on_the_id_not_on_input_order(
    dataset: tuple[list[SubscriptionRecord], WorldMap],
) -> None:
    """Shuffling the input must not move a single record across the boundary."""
    records, _ = dataset
    shuffled = list(records)
    random.Random(99).shuffle(shuffled)
    _, test_a = split_records(records, 0.30)
    _, test_b = split_records(shuffled, 0.30)
    assert {r.subscription_id for r in test_a} == {r.subscription_id for r in test_b}


# --- persistence ------------------------------------------------------------


def test_dataset_round_trips_through_disk(tmp_path: object) -> None:
    from pathlib import Path

    out = Path(str(tmp_path))
    manifest = build_dataset(out, n=60, seed=5, test_fraction=0.30)

    assert manifest.n_total == 60
    assert manifest.n_train + manifest.n_test == 60
    assert set(manifest.sha256) == {"train.jsonl", "test.jsonl"}
    assert read_manifest(out).sha256 == manifest.sha256

    for split in ("train", "test"):
        records = read_records(out / f"{split}.jsonl")
        world = read_world(out / f"{split}.world.json")
        assert len(records) == len(world)
        assert {r.subscription_id for r in records} == set(world)
        assert all(r.charge_at.tzinfo is not None for r in records)


def test_world_file_and_record_file_stay_disjoint(tmp_path: object) -> None:
    """A policy reads only the .jsonl. It must contain no ground truth at all."""
    from pathlib import Path

    out = Path(str(tmp_path))
    build_dataset(out, n=40, seed=5)
    raw = (out / "test.jsonl").read_text(encoding="utf-8")
    for leak in ("true_class", "funds_return_hours", "outage_end_hours", "base_intent", "draws"):
        assert leak not in raw, f"ground truth {leak!r} leaked into the agent-visible split"
