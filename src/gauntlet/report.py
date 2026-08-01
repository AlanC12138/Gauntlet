"""Terminal rendering of a RunReport. Recharts replaces this in Phase 5."""

from gauntlet.models import RunReport


def _pct(x: float) -> str:
    return f"{x * 100:>7.2f}%"


def render(report: RunReport) -> str:
    m = report.backtest.metrics
    lines: list[str] = []

    lines.append("=" * 72)
    lines.append(f"MANDATE: {report.mandate.name}  ({report.mandate.risk_profile})")
    lines.append("=" * 72)
    lines.append(report.mandate.objective)
    lines.append("")

    verdict = "APPROVED" if report.approved else "NOT APPROVED"
    rounds = len(report.iterations)
    lines.append(
        f"VERDICT: {verdict}  "
        f"({rounds} iteration{'s' if rounds != 1 else ''}"
        f"{', loop-back exercised' if rounds > 1 else ''})"
    )
    lines.append("")

    if rounds > 1:
        lines.append("-- Revision history " + "-" * 52)
        for it in report.iterations:
            outcome = it.verdict.decision.upper()
            lines.append(f"  Iteration {it.index}: {outcome}")
            if it.verdict.decision == "revise":
                reason = it.verdict.revision_feedback or it.verdict.reasoning
                for line in reason.strip().splitlines()[:6]:
                    lines.append(f"      {line.strip()}")
        lines.append("")

    lines.append("-- Strategy " + "-" * 60)
    lines.append(report.allocation.strategy_summary)
    lines.append("")

    lines.append("-- Allocation " + "-" * 58)
    for p in sorted(report.allocation.positions, key=lambda p: -p.weight):
        lines.append(f"  {p.ticker:<6} {_pct(p.weight)}   {p.rationale}")
    lines.append("")

    lines.append("-- Sector exposure " + "-" * 53)
    for sector, weight in report.sector_exposures.items():
        bar = "#" * round(weight * 60)
        lines.append(f"  {sector:<24} {_pct(weight)}  {bar}")
    lines.append("")

    c = report.compliance
    lines.append("-- Compliance (deterministic) " + "-" * 42)
    if c.passed:
        lines.append(f"  PASS — no violations across {len(c.checks_run)} checks")
    else:
        lines.append(f"  FAIL — {len(c.violations)} violation(s)")
        for v in c.violations:
            lines.append(f"    [{v.rule}] {v.message}")
    lines.append("")

    s = report.screening
    lines.append("-- Screening (RAG-grounded) " + "-" * 44)
    if not s.findings:
        lines.append(f"  PASS — no findings across {len(s.tickers_screened)} holdings")
    else:
        lines.append(f"  {'PASS' if s.passed else 'FAIL'} — {len(s.findings)} finding(s)")
        for f in s.findings:
            verb = "EXCLUDE" if f.recommend_exclude else "flag"
            lines.append(f"    {f.ticker:<6} [{verb}] {f.category}: {f.concern}")
            lines.append(f"           source: {f.source}")
    if s.ungrounded_findings_dropped:
        lines.append(
            f"  ({s.ungrounded_findings_dropped} finding(s) dropped — excerpt not found "
            "in any retrieved note)"
        )
    lines.append("")

    b = report.backtest
    lines.append("-- Backtest " + "-" * 60)
    lines.append(f"  Window                {b.start} to {b.end}  ({m.years:.2f} years)")
    lines.append(f"  Rebalance             {b.rebalance}")
    lines.append(f"  Initial capital       ${b.initial_capital:,.0f}")
    lines.append(f"  Final value           ${b.final_value:,.0f}")
    lines.append("")
    lines.append(f"  Total return         {_pct(m.total_return)}")
    lines.append(f"  CAGR                 {_pct(m.cagr)}")
    lines.append(f"  Volatility (ann.)    {_pct(m.annualized_volatility)}")
    lines.append(f"  Max drawdown         {_pct(m.max_drawdown)}")
    lines.append(f"  Sharpe                {m.sharpe_ratio:>7.2f}")
    lines.append(f"  Sortino               {m.sortino_ratio:>7.2f}")
    lines.append(f"  Calmar                {m.calmar_ratio:>7.2f}")
    lines.append("")

    lines.append("-- Calendar year returns " + "-" * 47)
    for year, ret in b.calendar_year_returns.items():
        bar = ("+" if ret >= 0 else "-") * min(round(abs(ret) * 100), 60)
        lines.append(f"  {year}  {_pct(ret)}  {bar}")
    lines.append("")

    if report.usage:
        lines.append("-- LLM spend (estimated) " + "-" * 47)
        for u in report.usage:
            cost = f"${u.cost_usd:.4f}" if u.cost_usd is not None else "unknown"
            lines.append(
                f"  {u.agent:<20} {u.model:<18} "
                f"{u.input_tokens:>7,} in / {u.output_tokens:>7,} out   {cost:>10}"
            )
        total = report.total_cost_usd
        total_str = f"${total:.4f}" if total is not None else "unknown"
        lines.append(
            f"  {'TOTAL':<20} {'':<18} {report.total_tokens:>7,} tokens"
            f"{'':>13}{total_str:>10}"
        )
        lines.append("")

    if report.verdict.memo:
        lines.append("-- Investment committee memo " + "-" * 43)
        for line in report.verdict.memo.strip().splitlines():
            lines.append(f"  {line}")
        lines.append("")

    lines.append(
        "Note: backtest returns are computed over a survivorship-biased universe "
        "and exclude transaction costs. See data/README.md."
    )
    return "\n".join(lines)
