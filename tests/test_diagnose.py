"""Classifier and propensity table.

Two families of test here. The ordinary ones check the lookup and the arithmetic.
The ones that matter check the properties ADR-0007 makes binding: the money-
relevant decision is computed without any model, ambiguity makes the policy more
cautious, and this rule table is not a copy of the simulator it is scored against.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta

import pytest

from conftest import make_record
from reclaimos.diagnose.classifier import (
    AMBIGUITY_PREFERENCE,
    TUPLE_INDEX,
    UNAMBIGUOUS_CONFIDENCE,
    classify,
)
from reclaimos.diagnose.propensity import (
    BASE_PROPENSITY,
    RECOVERABLE_THRESHOLD,
    score,
)
from reclaimos.domain import (
    AMBIGUOUS_TUPLES,
    DECLINE_CODES,
    IST,
    DeclineClass,
    Method,
    PaymentAttempt,
    codes_for,
)

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "reclaimos"


def _attempt(cls: DeclineClass, index: int = 0) -> PaymentAttempt:
    code = codes_for(cls)[index]
    return PaymentAttempt(
        attempt_no=1,
        occurred_at=datetime(2026, 6, 5, 3, 0, tzinfo=IST),
        amount_paise=49_900,
        error_code=code.code,
        error_source=code.source,
        error_step=code.step,
        error_reason=code.reason,
        error_description=code.description,
    )


# --- the classifier is deterministic and model-free --------------------------


def test_classification_is_a_pure_lookup_with_no_model_anywhere() -> None:
    """ADR-0007 criterion 1, checked structurally rather than promised.

    The classifier must not be able to reach a model even if someone wanted it to.
    """
    tree = ast.parse((SRC / "diagnose" / "classifier.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)

    forbidden = ("anthropic", "openai", "httpx", "requests", "urllib", "socket")
    for name in imported:
        assert not any(name.startswith(bad) for bad in forbidden), f"classifier imports {name}"
    assert not any(name.startswith("reclaimos.diagnose.explainer") for name in imported)


def test_the_same_tuple_always_classifies_the_same_way() -> None:
    for cls in DeclineClass:
        attempt = _attempt(cls)
        assert classify(attempt) == classify(attempt)


def test_an_unambiguous_tuple_resolves_to_its_own_class() -> None:
    for cls in DeclineClass:
        for index, code in enumerate(codes_for(cls)):
            if (code.code, code.reason) in AMBIGUOUS_TUPLES:
                continue
            result = classify(_attempt(cls, index))
            assert result.decline_class is cls
            assert not result.ambiguous
            assert result.confidence == UNAMBIGUOUS_CONFIDENCE
            assert result.rule_id.startswith("classify.exact.")


def test_an_ambiguous_tuple_is_flagged_and_lists_every_candidate() -> None:
    """The error floor has to be visible, not silently resolved."""
    attempt = _attempt(DeclineClass.HARD_DO_NOT_HONOR, index=1)  # the shared tuple
    result = classify(attempt)

    assert result.ambiguous
    assert len(result.candidates) > 1
    assert result.confidence < 0.5
    assert result.hard_possible
    assert DeclineClass.HARD_DO_NOT_HONOR in result.candidates


def test_an_unrecognised_tuple_is_unknown_and_treated_as_possibly_hard() -> None:
    """An unfamiliar code is not a licence to guess optimistically."""
    attempt = PaymentAttempt(
        attempt_no=1,
        occurred_at=datetime(2026, 6, 5, 3, 0, tzinfo=IST),
        amount_paise=49_900,
        error_code="TOTALLY_NEW_ERROR",
        error_source="issuer",
        error_step="payment_authorization",
        error_reason="something_we_have_never_seen",
    )
    result = classify(attempt)
    assert result.decline_class is DeclineClass.UNKNOWN
    assert result.ambiguous and result.hard_possible
    assert result.confidence < 0.3


def test_evidence_carries_the_exact_fields_the_decision_used() -> None:
    result = classify(_attempt(DeclineClass.EXPIRY_CARD_EXPIRED))
    assert result.evidence["error_reason"] == "card_expired"
    assert set(result.evidence) == {
        "error_code",
        "error_source",
        "error_step",
        "error_reason",
    }


def test_the_ambiguity_preference_covers_every_class() -> None:
    assert set(AMBIGUITY_PREFERENCE) == set(DeclineClass)


def test_the_tuple_index_covers_the_whole_taxonomy() -> None:
    assert sum(len(v) for v in TUPLE_INDEX.values()) == len(
        {(c.code, c.source, c.step, c.reason, c.true_class) for c in DECLINE_CODES}
    )


# --- the propensity table -----------------------------------------------------


def test_every_class_has_a_base_propensity() -> None:
    assert set(BASE_PROPENSITY) == set(DeclineClass)


def test_hard_declines_score_below_the_recoverable_threshold() -> None:
    """The stopping rule depends on this ordering being unmistakable."""
    for cls in DeclineClass:
        if cls.is_hard:
            result = score(make_record(true_class=cls), classify(_attempt(cls)))
            assert not result.recoverable, f"{cls} scored {result.score}"


def test_soft_declines_score_well_above_the_threshold() -> None:
    for cls in DeclineClass:
        if cls.is_soft:
            attempt = _attempt(cls)
            result = score(make_record(true_class=cls), classify(attempt))
            if classify(attempt).ambiguous:
                continue  # ambiguity is penalised on purpose; covered separately
            assert result.recoverable
            assert result.score > RECOVERABLE_THRESHOLD * 2


def test_an_expired_card_is_recoverable_even_though_a_retry_is_not() -> None:
    """Propensity means 'recoverable by some permitted action', not 'this retry
    will work'. Conflating them is how dunning systems hammer dead cards."""
    cls = DeclineClass.EXPIRY_CARD_EXPIRED
    result = score(make_record(true_class=cls), classify(_attempt(cls)))
    assert result.recoverable
    assert BASE_PROPENSITY[cls] < BASE_PROPENSITY[DeclineClass.SOFT_INSUFFICIENT_FUNDS]


def test_ambiguity_lowers_the_score_it_never_raises_it() -> None:
    """ADR-0007: uncertainty must make the policy more cautious."""
    record = make_record()
    clear = classify(_attempt(DeclineClass.SOFT_INSUFFICIENT_FUNDS, index=0))
    murky = classify(_attempt(DeclineClass.SOFT_INSUFFICIENT_FUNDS, index=1))
    assert not clear.ambiguous and murky.ambiguous

    assert score(record, murky).score < score(record, clear).score
    assert any(f.name == "ambiguity" for f in score(record, murky).factors)


def test_each_spent_attempt_lowers_the_score() -> None:
    record = make_record()
    classification = classify(_attempt(DeclineClass.SOFT_INSUFFICIENT_FUNDS))
    first = score(record, classification, attempts_made=0)
    third = score(record, classification, attempts_made=2)
    assert third.score < first.score


def test_an_expired_mandate_collapses_the_score(db: object) -> None:
    """Directly observable on the record, and no retry is permitted against it."""
    cls = DeclineClass.EXPIRY_MANDATE_EXPIRED
    live = make_record(true_class=cls, mandate_expiry_offset_days=365)
    lapsed = make_record(true_class=cls, mandate_expiry_offset_days=-5)
    classification = classify(_attempt(cls))

    assert score(lapsed, classification).score < score(live, classification).score / 4
    assert any(f.name == "expired_mandate" for f in score(lapsed, classification).factors)


def test_tenure_and_history_move_the_score_in_the_expected_direction() -> None:
    classification = classify(_attempt(DeclineClass.SOFT_INSUFFICIENT_FUNDS))
    loyal = make_record(tenure_months=36)
    new = make_record(tenure_months=0)
    assert score(loyal, classification).score > score(new, classification).score


def test_the_score_decomposes_into_readable_arithmetic() -> None:
    """Every ledger row has to show its working (ADR-0003)."""
    record = make_record(tenure_months=12, method=Method.UPI_AUTOPAY)
    result = score(record, classify(_attempt(DeclineClass.SOFT_INSUFFICIENT_FUNDS)), 1)

    assert result.rule_id.startswith("propensity.")
    assert {f.name for f in result.factors} >= {"attempt_decay", "method", "tenure"}
    assert all(f.why for f in result.factors)

    recomputed = result.base
    for factor in result.factors:
        recomputed *= factor.multiplier
    assert abs(recomputed - result.score) < 1e-3
    assert "=" in result.explain()


def test_the_score_stays_inside_zero_and_one() -> None:
    for cls in DeclineClass:
        for attempts in (0, 1, 4, 10):
            result = score(
                make_record(true_class=cls, tenure_months=48),
                classify(_attempt(cls)),
                attempts_made=attempts,
            )
            assert 0.0 <= result.score <= 1.0


# --- the anti-circularity guard ----------------------------------------------


def test_the_rule_table_is_not_a_copy_of_the_simulator() -> None:
    """The one test that keeps the evaluation meaningful.

    Our propensity table is a *hypothesis* about recovery; the world model is the
    thing scoring it. If a future "improvement" copies the simulator's constants
    across, every metric becomes circular and the whole EVAL.md is theatre
    (ADR-0006). Tests may import both sides; source code may not -- that boundary
    is enforced by tests/test_import_boundary.py.
    """
    from reclaimos.generator.outcome_model import RETRY_BASE

    identical = [cls for cls in DeclineClass if BASE_PROPENSITY[cls] == RETRY_BASE[cls]]
    assert len(identical) < 3, (
        f"the propensity table has drifted into a copy of the world model for "
        f"{identical}; that would make precision and recall circular"
    )


def test_the_two_tables_disagree_about_expired_cards_on_purpose() -> None:
    """A concrete, intended disagreement, so the previous test cannot be satisfied
    by cosmetic noise: retrying an expired card is hopeless in the world, but the
    subscription is recoverable through a card-update flow, and our table says so.
    """
    from reclaimos.generator.outcome_model import RETRY_BASE

    cls = DeclineClass.EXPIRY_CARD_EXPIRED
    assert RETRY_BASE[cls] < 0.10
    assert BASE_PROPENSITY[cls] > 0.25


@pytest.mark.parametrize("cls", list(DeclineClass))
def test_classification_and_scoring_need_no_credentials(cls: DeclineClass) -> None:
    """CI has no API key, and a judge's first run will not either."""
    record = make_record(true_class=cls)
    classification = classify(_attempt(cls))
    result = score(record, classification)
    assert result.rule_id
    assert classification.rule_id


def test_mandate_expiry_is_read_from_the_record_not_guessed() -> None:
    record = make_record(mandate_expiry_offset_days=-1)
    assert record.mandate.expiry < record.charge_at
    assert record.mandate.expiry == record.charge_at - timedelta(days=1)


# --- the live-observed class --------------------------------------------------

LIVE_NOT_PERMITTED = PaymentAttempt(
    attempt_no=1,
    occurred_at=datetime(2026, 9, 1, 3, 0, tzinfo=IST),
    amount_paise=49_900,
    error_code="BAD_REQUEST_ERROR",
    error_source="business",
    error_step="payment_initiation",
    error_reason="international_transaction_not_allowed",
    error_description="International cards are not enabled for this merchant account.",
)


def test_the_live_observed_tuple_classifies_as_non_retryable() -> None:
    """Observed on real Razorpay test mode, 2026-09-01, on two payments.

    `source=business` at `step=payment_initiation` is a pre-authorisation
    rejection: the merchant's own configuration refused the instrument before any
    issuer was consulted. Retrying is guaranteed waste -- nothing about the
    customer or the bank participates in the refusal, so the same card fails
    identically forever.
    """
    result = classify(LIVE_NOT_PERMITTED)
    assert result.decline_class is DeclineClass.HARD_NOT_PERMITTED
    assert result.decline_class.is_hard
    assert not result.ambiguous
    assert result.hard_possible


def test_the_live_observed_tuple_scores_below_the_retry_floor() -> None:
    record = make_record(true_class=DeclineClass.HARD_NOT_PERMITTED)
    assert not score(record, classify(LIVE_NOT_PERMITTED)).recoverable


def test_the_live_observed_class_is_never_generated() -> None:
    """The guard that keeps the sealed held-out result valid.

    HARD_NOT_PERMITTED was added to the taxonomy *after* the held-out split was
    scored. It carries weight 0.00 on every rail so the simulated population is
    byte-identical to the one that produced 68.0% [56.0, 78.7]. Give it a
    non-zero weight and that number silently stops describing the data it claims
    to describe -- which is why this is a test and not a comment.
    """
    from reclaimos.generator import generate
    from reclaimos.generator.profiles import CLASS_WEIGHTS_BY_METHOD, marginal_class_weights

    assert marginal_class_weights()[DeclineClass.HARD_NOT_PERMITTED] == 0.0
    for method, weights in CLASS_WEIGHTS_BY_METHOD.items():
        assert weights[DeclineClass.HARD_NOT_PERMITTED] == 0.0, method

    _, world = generate(600, seed=99)
    assert all(t.true_class is not DeclineClass.HARD_NOT_PERMITTED for t in world.values())


def test_the_sealed_split_hash_has_not_moved() -> None:
    """The held-out number in EVAL.md describes a specific dataset. If the
    generator ever produces a different one, the number is stale and must be
    re-earned, not re-quoted."""
    import hashlib
    import tempfile
    from pathlib import Path

    from reclaimos.generator import build_dataset

    with tempfile.TemporaryDirectory() as tmp:
        build_dataset(Path(tmp), n=250, seed=42)
        digest = hashlib.sha256((Path(tmp) / "test.jsonl").read_bytes()).hexdigest()

    assert digest == "001d7c1c3d85286203fbce32ac9103059cc514a8b41a9ca102b5be60b6360cb9", (
        "the sealed test split no longer reproduces; the held-out result in EVAL.md "
        "describes data this generator no longer produces"
    )
