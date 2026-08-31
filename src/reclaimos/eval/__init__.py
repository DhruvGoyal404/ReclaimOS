"""Evaluation: policies, harness, metrics, and EVAL.md rendering.

Built before the agent exists, on purpose (ADR-0006).
"""

from reclaimos.eval.baselines import (
    ContactOncePolicy,
    DoNothingPolicy,
    RetryOncePolicy,
    RetryThriceFixedPolicy,
    all_baselines,
)
from reclaimos.eval.costs import CHARGE_ATTEMPT_COST, CONTACT_COST, cost_of
from reclaimos.eval.harness import compute_oracles, run_oracle, run_policy, run_record
from reclaimos.eval.metrics import Confusion, ExceptionRow, Interval, PolicyMetrics
from reclaimos.eval.policy import LoopState, Policy
from reclaimos.eval.runner import EvalRun, evaluate, read_run, write_run

__all__ = [
    "CHARGE_ATTEMPT_COST",
    "CONTACT_COST",
    "Confusion",
    "ContactOncePolicy",
    "DoNothingPolicy",
    "EvalRun",
    "ExceptionRow",
    "Interval",
    "LoopState",
    "Policy",
    "PolicyMetrics",
    "RetryOncePolicy",
    "RetryThriceFixedPolicy",
    "all_baselines",
    "compute_oracles",
    "cost_of",
    "evaluate",
    "read_run",
    "run_oracle",
    "run_policy",
    "run_record",
    "write_run",
]
