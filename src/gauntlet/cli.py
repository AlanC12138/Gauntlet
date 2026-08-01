"""Phase 1 CLI. `gauntlet run` executes the propose -> backtest loop end to end."""

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from gauntlet.mandates import DEMO_MANDATES, get_mandate
from gauntlet.models import RebalanceFrequency

app = typer.Typer(add_completion=False, help="Gauntlet — portfolio construction, verified.")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)-7s %(name)s: %(message)s",
    )


@app.command("mandates")
def list_mandates() -> None:
    """List the bundled demo mandates."""
    for key in DEMO_MANDATES:
        mandate = get_mandate(key)
        typer.echo(f"{key:<14} {mandate.name} ({mandate.risk_profile})")


@app.command("fetch")
def fetch(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = True,
) -> None:
    """Warm the price cache for the entire universe over its declared window."""
    _configure_logging(verbose)
    from gauntlet.data.prices import ensure_cached
    from gauntlet.universe import load_universe

    universe = load_universe()
    window = universe.backtest_window
    written = ensure_cached(universe.tickers, window.start, window.end)
    typer.echo(f"Cache up to date. {written} new price bars written.")


@app.command("run")
def run(
    mandate_name: Annotated[str, typer.Argument(help="Demo mandate key")] = "balanced",
    rebalance: Annotated[RebalanceFrequency | None, typer.Option("--rebalance")] = None,
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Also write the full report as JSON")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = True,
) -> None:
    """Propose an allocation for a mandate, then backtest it."""
    _configure_logging(verbose)

    try:
        mandate = get_mandate(mandate_name)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from None

    if rebalance is not None:
        mandate = mandate.model_copy(update={"rebalance": rebalance})

    from gauntlet.pipeline import run_pipeline
    from gauntlet.report import render

    report = run_pipeline(mandate)
    typer.echo(render(report))

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"\nFull report written to {json_out}")


@app.command("eval")
def run_evals(
    suite: Annotated[
        str | None, typer.Option("--suite", help="backtest | compliance (default: both)")
    ] = None,
    json_out: Annotated[Path | None, typer.Option("--json")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run the eval harness. No LLM calls, no tokens spent."""
    _configure_logging(verbose)
    from gauntlet.evals.runner import SUITES, render, run_all

    if suite is not None and suite not in SUITES:
        raise typer.BadParameter(f"unknown suite {suite!r}; available: {', '.join(SUITES)}")

    report = run_all([suite] if suite else None)
    typer.echo(render(report))

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"\nWritten to {json_out}")

    if not report.passed:
        raise typer.Exit(code=1)


@app.command("backtest")
def backtest_weights(
    weights_json: Annotated[
        str, typer.Argument(help='Weights as JSON, e.g. \'{"SPY": 0.6, "AGG": 0.4}\'')
    ],
    mandate_name: Annotated[str, typer.Option("--mandate")] = "balanced",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = True,
) -> None:
    """Backtest explicit weights without calling the Allocation Agent.

    Useful for exercising the engine without spending tokens.
    """
    _configure_logging(verbose)
    from gauntlet.backtest import run_backtest
    from gauntlet.config import settings
    from gauntlet.data.prices import price_panel

    weights = json.loads(weights_json)
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}

    mandate = get_mandate(mandate_name)
    prices = price_panel(list(weights), mandate.start, mandate.end)
    result = run_backtest(
        weights,
        prices,
        rebalance=mandate.rebalance,
        initial_capital=mandate.initial_capital,
        risk_free_rate=settings.risk_free_rate,
    )
    m = result.metrics
    typer.echo(f"Window        {result.start} to {result.end} ({m.years:.2f}y)")
    typer.echo(f"Final value   ${result.final_value:,.0f}")
    typer.echo(f"Total return  {m.total_return:>8.2%}")
    typer.echo(f"CAGR          {m.cagr:>8.2%}")
    typer.echo(f"Volatility    {m.annualized_volatility:>8.2%}")
    typer.echo(f"Max drawdown  {m.max_drawdown:>8.2%}")
    typer.echo(f"Sharpe        {m.sharpe_ratio:>8.2f}")


if __name__ == "__main__":
    app()
