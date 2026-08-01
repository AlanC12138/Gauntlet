import pytest

from gauntlet.usage import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    LLMUsage,
    estimate_cost,
    total_cost,
)


def test_cost_uses_published_rates():
    # Opus 5: $5/MTok in, $25/MTok out.
    # 1,000,000 in + 1,000,000 out = $5 + $25 = $30.
    assert estimate_cost("claude-opus-5", 1_000_000, 1_000_000) == pytest.approx(30.0)


def test_cost_of_a_realistic_call():
    # Roughly the shape of one Allocation Agent call.
    cost = estimate_cost("claude-opus-5", input_tokens=1_500, output_tokens=4_000)
    assert cost == pytest.approx((1_500 * 5.0 + 4_000 * 25.0) / 1_000_000)


def test_cache_reads_are_cheaper_than_fresh_input():
    fresh = estimate_cost("claude-opus-5", input_tokens=10_000, output_tokens=0)
    cached = estimate_cost(
        "claude-opus-5", input_tokens=0, output_tokens=0, cache_read_input_tokens=10_000
    )
    assert cached == pytest.approx(fresh * CACHE_READ_MULTIPLIER)


def test_cache_writes_cost_more_than_fresh_input():
    fresh = estimate_cost("claude-opus-5", input_tokens=10_000, output_tokens=0)
    written = estimate_cost(
        "claude-opus-5", input_tokens=0, output_tokens=0, cache_creation_input_tokens=10_000
    )
    assert written == pytest.approx(fresh * CACHE_WRITE_MULTIPLIER)


def test_sonnet_is_cheaper_than_opus_for_the_same_call():
    assert estimate_cost("claude-sonnet-5", 10_000, 10_000) < estimate_cost(
        "claude-opus-5", 10_000, 10_000
    )


def test_unpriced_model_reports_unknown_not_zero():
    """A fabricated $0.00 would read as 'free' rather than 'unmeasured'."""
    assert estimate_cost("some-future-model", 1_000, 1_000) is None


def test_total_is_unknown_if_any_call_is_unpriced():
    priced = LLMUsage(agent="a", model="claude-opus-5", input_tokens=1, output_tokens=1, cost_usd=0.5)
    unpriced = LLMUsage(agent="b", model="mystery", input_tokens=1, output_tokens=1, cost_usd=None)
    assert total_cost([priced, priced]) == pytest.approx(1.0)
    assert total_cost([priced, unpriced]) is None


def test_total_tokens_counts_every_category():
    usage = LLMUsage(
        agent="a",
        model="claude-opus-5",
        input_tokens=100,
        output_tokens=200,
        cache_read_input_tokens=300,
        cache_creation_input_tokens=400,
    )
    assert usage.total_tokens == 1000
