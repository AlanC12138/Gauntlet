"""Recording eval results.

The table existed since Phase 4 with a docstring explaining why it mattered, and
nothing ever wrote to it — so these tests exist as much to keep it wired up as to
check the writing works.
"""

from gauntlet.db import evals as repo
from gauntlet.db import get_session
from gauntlet.evals.models import EvalReport, EvalSuiteResult


def _report(backtest_passed: bool = True, compliance_passed: bool = True) -> EvalReport:
    return EvalReport(
        suites=[
            EvalSuiteResult(
                suite="backtest", passed=backtest_passed, summary="12/12 exact"
            ),
            EvalSuiteResult(
                suite="compliance", passed=compliance_passed, summary="26/26 exact"
            ),
        ]
    )


def test_one_row_per_suite_not_per_report(clean_db):
    """The suites measure different things — numeric agreement versus
    classification accuracy — so a combined row would force one pass/fail over
    both and lose which one moved."""
    with get_session() as session:
        written = repo.save_report(session, _report())

    assert written == 2
    with get_session() as session:
        assert {r.suite for r in repo.recent(session, "backtest")} == {"backtest"}
        assert len(repo.recent(session, "compliance")) == 1


def test_history_accumulates_newest_first(clean_db):
    """A trend, not a snapshot: the value is in comparing runs."""
    for summary in ("run one", "run two", "run three"):
        report = _report()
        report.suites[0].summary = summary
        with get_session() as session:
            repo.save_report(session, report)

    with get_session() as session:
        history = repo.recent(session, "backtest")

    assert len(history) == 3
    assert history[0].summary == "run three"


def test_a_failing_suite_is_recorded_as_failing(clean_db):
    """The row that matters most is the one where a number moved."""
    with get_session() as session:
        repo.save_report(session, _report(backtest_passed=False))

    with get_session() as session:
        assert repo.recent(session, "backtest")[0].passed is False
        assert repo.recent(session, "compliance")[0].passed is True


def test_the_full_suite_payload_survives(clean_db):
    """Stored whole so a regression can be diagnosed later, not just detected."""
    report = _report()
    report.suites[0].summary = "12/12 exact, worst deviation 2.8e-11"

    with get_session() as session:
        repo.save_report(session, report)
    with get_session() as session:
        payload = repo.recent(session, "backtest")[0].payload

    assert payload["summary"] == "12/12 exact, worst deviation 2.8e-11"
    assert payload["suite"] == "backtest"


def test_an_unreachable_store_does_not_fail_the_eval(monkeypatch):
    """A regression guard that refuses to report because a database is down has
    made itself the outage."""
    from sqlalchemy.exc import OperationalError

    def refuse():
        raise OperationalError("select 1", {}, Exception("connection refused"))

    monkeypatch.setattr("gauntlet.db.get_session", refuse)

    assert repo.save_report_best_effort(_report()) == 0
