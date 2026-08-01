"""The boundary between what the Allocation Agent emits and what the rest of the
system trusts. No API calls — these exercise validation only."""

import pytest

from gauntlet.agents.allocation import _validate
from gauntlet.models import AllocationProposal, ProposedPosition
from gauntlet.universe import load_universe


def _proposal(**weights: float) -> AllocationProposal:
    return AllocationProposal(
        strategy_summary="test",
        positions=[
            ProposedPosition(ticker=t, weight=w, rationale="because") for t, w in weights.items()
        ],
    )


@pytest.fixture(scope="module")
def universe():
    return load_universe()


def test_valid_proposal_passes_through(universe):
    allocation = _validate(_proposal(AAPL=0.6, JPM=0.4), universe)
    assert allocation.weights == pytest.approx({"AAPL": 0.6, "JPM": 0.4})


def test_small_rounding_drift_is_normalized(universe):
    # An LLM emitting 0.333/0.333/0.334-style weights shouldn't fail the run.
    allocation = _validate(_proposal(AAPL=0.333, JPM=0.333, XOM=0.333), universe)
    assert sum(allocation.weights.values()) == pytest.approx(1.0)
    assert allocation.weights["AAPL"] == pytest.approx(1 / 3)


def test_large_weight_drift_is_an_error_not_a_normalization(universe):
    with pytest.raises(ValueError, match="sum to"):
        _validate(_proposal(AAPL=0.6, JPM=0.6), universe)


def test_tickers_outside_the_universe_are_rejected(universe):
    with pytest.raises(ValueError, match="outside the universe"):
        _validate(_proposal(AAPL=0.5, FAKE=0.5), universe)


def test_non_positive_weights_are_rejected(universe):
    with pytest.raises(ValueError, match="non-positive"):
        _validate(_proposal(AAPL=1.2, JPM=-0.2), universe)


def test_empty_proposal_is_rejected(universe):
    with pytest.raises(ValueError, match="no positions"):
        _validate(AllocationProposal(strategy_summary="", positions=[]), universe)


def test_duplicate_positions_are_rejected(universe):
    proposal = AllocationProposal(
        strategy_summary="test",
        positions=[
            ProposedPosition(ticker="AAPL", weight=0.5, rationale="a"),
            ProposedPosition(ticker="AAPL", weight=0.5, rationale="b"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate"):
        _validate(proposal, universe)
