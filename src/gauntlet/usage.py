"""Token accounting and cost estimation for the LLM agents.

This is a *local estimate*, not billing truth — it multiplies reported token
counts by a hardcoded price table. Treat it as a design signal (is the Screening
Agent's RAG context blowing up the prompt?), not as an invoice. Langfuse in
Phase 6 supersedes this for tracing; the per-run totals stay useful for the
Phase 3 eval harness, which needs cost per evaluated case.

Prices are USD per million tokens, from the published Anthropic rate card.
"""

import logging

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# (input, output) USD per 1M tokens.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    # Sonnet 5 carries introductory pricing ($2/$10) through 2026-08-31. This
    # table uses list price, so Sonnet 5 estimates run high until then.
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Cache writes bill above base input rate, reads far below it.
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute TTL; a 1-hour TTL would be 2.0
CACHE_READ_MULTIPLIER = 0.10


class LLMUsage(BaseModel):
    """One LLM call's token spend. `cost_usd` is None for an unpriced model —
    that reads as 'unknown', where 0.0 would falsely read as 'free'."""

    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> float | None:
    rates = PRICING.get(model)
    if rates is None:
        logger.warning("No price entry for model %r; cost will be reported as unknown", model)
        return None

    input_rate, output_rate = rates
    billable_input = (
        input_tokens
        + cache_creation_input_tokens * CACHE_WRITE_MULTIPLIER
        + cache_read_input_tokens * CACHE_READ_MULTIPLIER
    )
    return (billable_input * input_rate + output_tokens * output_rate) / 1_000_000


def usage_from_response(agent: str, model: str, response) -> LLMUsage:
    """Build an LLMUsage from an Anthropic Message. Cache fields are absent on
    responses that didn't touch the cache, so they default to zero."""
    raw = response.usage
    cache_read = getattr(raw, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(raw, "cache_creation_input_tokens", 0) or 0

    return LLMUsage(
        agent=agent,
        model=model,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        cost_usd=estimate_cost(
            model, raw.input_tokens, raw.output_tokens, cache_read, cache_write
        ),
    )


def total_cost(usages: list[LLMUsage]) -> float | None:
    """Sum of estimated cost. None if any call's model was unpriced — a partial
    total would understate spend without saying so."""
    if any(u.cost_usd is None for u in usages):
        return None
    return sum(u.cost_usd for u in usages)  # type: ignore[misc]
