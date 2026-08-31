"""Diagnosis: deterministic classification and propensity scoring.

The money-relevant decision is made here, without a model. The LLM explainer that
narrates it lives alongside and receives these results as inputs (ADR-0007).
"""

from reclaimos.diagnose.classifier import Classification, classify
from reclaimos.diagnose.explainer import (
    AnthropicClient,
    Explanation,
    LLMClient,
    explain,
    template_explanation,
)
from reclaimos.diagnose.propensity import (
    BASE_PROPENSITY,
    RECOVERABLE_THRESHOLD,
    Factor,
    Propensity,
    score,
)
from reclaimos.diagnose.redact import contains_pii, redact

__all__ = [
    "BASE_PROPENSITY",
    "RECOVERABLE_THRESHOLD",
    "AnthropicClient",
    "Classification",
    "Explanation",
    "Factor",
    "LLMClient",
    "Propensity",
    "classify",
    "contains_pii",
    "explain",
    "redact",
    "score",
    "template_explanation",
]
