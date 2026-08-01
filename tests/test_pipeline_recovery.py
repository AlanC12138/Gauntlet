"""Malformed-proposal recovery.

A proposal that fails validation never reaches the assessments — there is nothing
coherent to assess — but it must not kill the run either. It consumes an
iteration, its tokens are still billed, and the failure is fed back as revision
feedback. Verified live against Haiku 4.5, which returns weight sums like 1.43;
these tests pin the behavior without spending tokens.
"""

import pytest

from gauntlet import pipeline as pipeline_module
from gauntlet.agents.allocation import AllocationValidationError
from gauntlet.mandates import get_mandate
from gauntlet.usage import LLMUsage


def _usage(cost: float = 0.007) -> LLMUsage:
    return LLMUsage(
        agent="Allocation Agent",
        model="claude-haiku-4-5",
        input_tokens=1500,
        output_tokens=1000,
        cost_usd=cost,
    )


def test_every_proposal_malformed_raises_with_the_last_error(monkeypatch):
    attempts: list[str | None] = []

    def always_malformed(mandate, universe=None, feedback=None, client=None):
        attempts.append(feedback)
        raise AllocationValidationError(
            f"Allocation Agent's weights sum to 1.{len(attempts)}300, expected ~1.0", _usage()
        )

    monkeypatch.setattr(pipeline_module, "propose_allocation", always_malformed)

    with pytest.raises(RuntimeError, match="never reached assessment"):
        pipeline_module.run_pipeline(get_mandate("strict"), client=object(), max_iterations=3)

    assert len(attempts) == 3, "should use the full iteration budget"
    assert attempts[0] is None, "first attempt gets no feedback"
    assert attempts[1] is not None and "sum to" in attempts[1], (
        "the validation error must reach the next attempt as feedback"
    )


def test_feedback_carries_the_specific_failure(monkeypatch):
    seen: list[str | None] = []

    def malformed(mandate, universe=None, feedback=None, client=None):
        seen.append(feedback)
        raise AllocationValidationError(
            "Allocation Agent proposed tickers outside the universe: ['FAKE']", _usage()
        )

    monkeypatch.setattr(pipeline_module, "propose_allocation", malformed)

    with pytest.raises(RuntimeError):
        pipeline_module.run_pipeline(get_mandate("strict"), client=object(), max_iterations=2)

    assert "FAKE" in seen[1], "feedback should name the offending ticker, not just say 'invalid'"


def test_validation_error_carries_usage_for_billing():
    """Tokens spent on a discarded proposal were still billed. Dropping them
    would make the run's reported cost quietly wrong."""
    error = AllocationValidationError("bad weights", _usage(cost=0.0069))
    assert error.usage.cost_usd == pytest.approx(0.0069)
    assert error.usage.input_tokens == 1500
    assert isinstance(error, ValueError), "must stay catchable as ValueError"
