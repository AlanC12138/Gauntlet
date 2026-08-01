"""The two guarantees that make the governance layer more than decoration:
screening findings must be grounded in retrieved text, and a deterministic
compliance failure cannot be approved away by the LLM supervisor."""

import pytest

from gauntlet.agents.screening import _validate_grounding
from gauntlet.agents.synthesis import synthesize
from gauntlet.mandates import get_mandate
from gauntlet.models import (
    Allocation,
    ComplianceReport,
    Position,
    ScreeningFinding,
    ScreeningProposal,
    ScreeningReport,
    Violation,
)
from gauntlet.retrieval import Document, RetrievedDocument

CORPUS_TEXT = (
    "Philip Morris International is a tobacco company. Its business is the "
    "manufacture and sale of cigarettes and smoke-free nicotine products."
)


@pytest.fixture
def retrieved():
    return [
        RetrievedDocument(
            document=Document(
                doc_id="tobacco-and-nicotine",
                category="Tobacco",
                tickers=["PM"],
                exclusion=True,
                body=CORPUS_TEXT,
            ),
            score=100.0,
            matched_on="entity",
        )
    ]


@pytest.fixture
def allocation():
    return Allocation(
        strategy_summary="t",
        positions=[
            Position(ticker="PM", weight=0.5, rationale="x"),
            Position(ticker="SPY", weight=0.5, rationale="x"),
        ],
    )


def _finding(**kwargs) -> ScreeningFinding:
    base = {
        "ticker": "PM",
        "category": "Tobacco",
        "concern": "Tobacco manufacturer",
        "excerpt": "Philip Morris International is a tobacco company",
        "source": "tobacco-and-nicotine",
        "recommend_exclude": True,
    }
    return ScreeningFinding(**{**base, **kwargs})


def test_grounded_finding_survives(retrieved, allocation):
    kept, dropped = _validate_grounding(
        ScreeningProposal(findings=[_finding()]), retrieved, allocation
    )
    assert len(kept) == 1 and dropped == 0


def test_whitespace_differences_do_not_break_grounding(retrieved, allocation):
    quote = "Philip   Morris\nInternational  is a tobacco   company"
    kept, dropped = _validate_grounding(
        ScreeningProposal(findings=[_finding(excerpt=quote)]), retrieved, allocation
    )
    assert len(kept) == 1 and dropped == 0


def test_fabricated_excerpt_is_dropped(retrieved, allocation):
    """The core anti-hallucination guarantee: a plausible-sounding quote that
    appears in no retrieved note does not reach the Synthesis Agent."""
    invented = "PM was fined $4 billion for concealing internal research"
    kept, dropped = _validate_grounding(
        ScreeningProposal(findings=[_finding(excerpt=invented)]), retrieved, allocation
    )
    assert kept == [] and dropped == 1


def test_finding_about_an_unheld_ticker_is_dropped(retrieved, allocation):
    kept, dropped = _validate_grounding(
        ScreeningProposal(findings=[_finding(ticker="XOM")]), retrieved, allocation
    )
    assert kept == [] and dropped == 1


def test_correct_quote_cited_to_the_wrong_document_is_kept(retrieved, allocation):
    """A citation error is not a hallucination — the text really is in the corpus."""
    kept, dropped = _validate_grounding(
        ScreeningProposal(findings=[_finding(source="fossil-fuel-extraction")]),
        retrieved,
        allocation,
    )
    assert len(kept) == 1 and dropped == 0


# --- Synthesis: the deterministic override ----------------------------------


class _FakeUsage:
    input_tokens = 100
    output_tokens = 200
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeResponse:
    stop_reason = "end_turn"
    stop_details = None
    usage = _FakeUsage()

    def __init__(self, parsed):
        self.parsed_output = parsed


class _FakeMessages:
    def __init__(self, parsed):
        self._parsed = parsed

    def parse(self, **_kwargs):
        return _FakeResponse(self._parsed)


class _FakeClient:
    def __init__(self, parsed):
        self.messages = _FakeMessages(parsed)


def _synthesis_proposal(decision: str, **kwargs):
    from gauntlet.agents.synthesis import SynthesisProposal

    base = {"decision": decision, "reasoning": "looks fine to me", "memo": "approved memo"}
    return SynthesisProposal(**{**base, **kwargs})


def _reports(compliance_passed: bool):
    violations = (
        []
        if compliance_passed
        else [Violation(rule="max_position_weight", message="AAPL is 50.00%, over the 10.00% limit.")]
    )
    compliance = ComplianceReport(
        passed=compliance_passed,
        violations=violations,
        checks_run=["max_position_weight"],
        sector_exposures={"Information Technology": 0.5},
    )
    screening = ScreeningReport(passed=True, findings=[], tickers_screened=["AAPL"])
    return compliance, screening


def test_supervisor_cannot_approve_over_a_compliance_violation(allocation):
    """The whole point of a deterministic engine: an LLM cannot talk past it."""
    compliance, screening = _reports(compliance_passed=False)
    client = _FakeClient(_synthesis_proposal("approve"))

    verdict, _ = synthesize(
        get_mandate("balanced"),
        allocation,
        backtest=_stub_backtest(),
        compliance=compliance,
        screening=screening,
        client=client,
    )
    assert verdict.decision == "revise"
    assert verdict.memo is None
    assert "max_position_weight" in verdict.revision_feedback
    # The supervisor's own reasoning survives the override rather than vanishing.
    assert "looks fine to me" in verdict.revision_feedback


def test_supervisor_approval_stands_when_compliance_passes(allocation):
    compliance, screening = _reports(compliance_passed=True)
    client = _FakeClient(_synthesis_proposal("approve"))

    verdict, usage = synthesize(
        get_mandate("balanced"),
        allocation,
        backtest=_stub_backtest(),
        compliance=compliance,
        screening=screening,
        client=client,
    )
    assert verdict.decision == "approve"
    assert verdict.memo == "approved memo"
    assert verdict.revision_feedback is None
    assert usage.agent == "Synthesis Agent"


def test_revise_without_feedback_is_an_error(allocation):
    """Feedback is what the loop-back sends back; a revise without it is a dead end."""
    compliance, screening = _reports(compliance_passed=True)
    client = _FakeClient(_synthesis_proposal("revise", revision_feedback=None, memo=None))

    with pytest.raises(RuntimeError, match="no revision feedback"):
        synthesize(
            get_mandate("balanced"),
            allocation,
            backtest=_stub_backtest(),
            compliance=compliance,
            screening=screening,
            client=client,
        )


def _stub_backtest():
    import pandas as pd

    from gauntlet.backtest import run_backtest

    prices = pd.DataFrame(
        {"PM": [100.0, 110.0], "SPY": [100.0, 105.0]},
        index=pd.to_datetime(["2020-01-31", "2020-02-29"]),
    )
    return run_backtest({"PM": 0.5, "SPY": 0.5}, prices, rebalance="none")
