"""Runs the eval suites and renders their results."""

from gauntlet.evals import backtest_math, compliance
from gauntlet.evals.models import EvalReport

SUITES = {
    "backtest": backtest_math.run,
    "compliance": compliance.run,
}


def run_all(suites: list[str] | None = None) -> EvalReport:
    names = suites or list(SUITES)
    return EvalReport(suites=[SUITES[name]() for name in names])


def render(report: EvalReport) -> str:
    lines: list[str] = []
    for suite in report.suites:
        status = "PASS" if suite.passed else "FAIL"
        lines.append("=" * 74)
        lines.append(f"{suite.suite}  [{status}]")
        lines.append("=" * 74)
        lines.append(f"  {suite.summary}")
        lines.append("")

        if suite.agreements:
            lines.append(f"  {'metric':<32}{'cases':>7}{'max abs error':>16}{'tolerance':>13}")
            for a in suite.agreements:
                flag = "" if a.passed else "  <-- OVER"
                lines.append(
                    f"  {a.metric:<32}{a.cases:>7}{a.max_absolute_error:>16.2e}"
                    f"{a.tolerance:>13.0e}{flag}"
                )
            lines.append("")

        if suite.rule_scores:
            lines.append(
                f"  {'rule':<26}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>4}"
                f"{'precision':>11}{'recall':>9}{'F1':>7}"
            )
            for s in sorted(suite.rule_scores, key=lambda s: s.rule):
                lines.append(
                    f"  {s.rule:<26}{s.true_positives:>4}{s.false_positives:>4}"
                    f"{s.false_negatives:>4}{s.true_negatives:>4}"
                    f"{s.precision:>11.3f}{s.recall:>9.3f}{s.f1:>7.3f}"
                )
            lines.append("")

        failures = suite.failures
        if failures:
            lines.append(f"  {len(failures)} failing case(s):")
            for case in failures:
                lines.append(f"    {case.name}: {case.detail}")
            lines.append("")

    lines.append("=" * 74)
    lines.append("OVERALL: " + ("PASS" if report.passed else "FAIL"))
    lines.append(
        "Compliance is deterministic, so 100% is the pass mark rather than a "
        "score; anything lower is a defect."
    )
    return "\n".join(lines)
