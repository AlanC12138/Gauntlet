import pytest

from gauntlet.universe import load_universe, sector_exposures

GICS_SECTORS = {
    "Information Technology",
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Financials",
    "Health Care",
    "Industrials",
    "Energy",
    "Utilities",
    "Materials",
    "Real Estate",
}


def test_universe_loads_and_is_internally_consistent():
    universe = load_universe()
    assert len(universe.constituents) == len(set(universe.tickers)), "duplicate tickers"
    assert universe.backtest_window.start < universe.backtest_window.end


def test_every_sector_is_represented():
    covered = {c.sector for c in load_universe().constituents if c.sector}
    assert covered == GICS_SECTORS


def test_sector_cap_treatment_matches_sector_presence():
    """A `constituent` must name a sector; the exempt treatments must not — the
    compliance engine keys off exactly this invariant."""
    for c in load_universe().constituents:
        if c.sector_cap_treatment == "constituent":
            assert c.sector is not None, f"{c.ticker} counts toward a cap but has no sector"
        else:
            assert c.sector is None, f"{c.ticker} is exempt but names sector {c.sector}"


def test_sector_exposures_aggregate_by_sector():
    exposures = sector_exposures({"AAPL": 0.3, "MSFT": 0.2, "JPM": 0.5})
    assert exposures == pytest.approx({"Information Technology": 0.5, "Financials": 0.5})


def test_exempt_instruments_bucket_by_treatment_not_sector():
    exposures = sector_exposures({"SPY": 0.5, "AGG": 0.3, "XLK": 0.2})
    assert exposures == pytest.approx(
        {"diversified": 0.5, "non_equity": 0.3, "Information Technology": 0.2}
    )


def test_unknown_ticker_is_rejected():
    with pytest.raises(KeyError, match="not in universe"):
        sector_exposures({"NOTREAL": 1.0})
