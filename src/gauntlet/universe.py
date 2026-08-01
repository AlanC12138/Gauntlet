"""Loads and queries data/universe.json.

Sector labels live here and nowhere else — Phase 2's sector-cap rules read them
through this module. See data/README.md for the taxonomy's known limitations.
"""

import functools
import json
from datetime import date
from typing import Literal

from pydantic import BaseModel

from gauntlet.config import settings

AssetClass = Literal["equity", "sector_etf", "equity_etf", "bond_etf", "commodity_etf"]
SectorCapTreatment = Literal["constituent", "diversified", "non_equity"]


class Constituent(BaseModel):
    ticker: str
    name: str
    asset_class: AssetClass
    sector: str | None
    sector_cap_treatment: SectorCapTreatment


class BacktestWindow(BaseModel):
    start: date
    end: date
    rationale: str


class Universe(BaseModel):
    schema_version: str
    universe_id: str
    description: str
    backtest_window: BacktestWindow
    constituents: list[Constituent]

    @property
    def tickers(self) -> list[str]:
        return [c.ticker for c in self.constituents]

    def by_ticker(self, ticker: str) -> Constituent:
        try:
            return self._index[ticker]
        except KeyError:
            raise KeyError(f"{ticker!r} is not in universe {self.universe_id!r}") from None

    @functools.cached_property
    def _index(self) -> dict[str, Constituent]:
        return {c.ticker: c for c in self.constituents}

    def catalog(self) -> str:
        """Compact ticker/sector listing for the Allocation Agent's prompt."""
        lines = []
        for c in self.constituents:
            sector = c.sector or "(diversified — exempt from sector caps)"
            lines.append(f"{c.ticker:<6} {sector:<24} {c.name}")
        return "\n".join(lines)


@functools.lru_cache(maxsize=1)
def load_universe() -> Universe:
    with open(settings.universe_path, encoding="utf-8") as f:
        return Universe.model_validate(json.load(f))


# Economic exposure buckets for asset-class bands. Distinct from
# `sector_cap_treatment`, which answers a different question: SPY is `diversified`
# (exempt from sector caps) but is unambiguously *equity* exposure. Conflating the
# two would let a mandate's bond floor be satisfied by an equity index fund.
ECONOMIC_BUCKET: dict[str, str] = {
    "equity": "equity",
    "sector_etf": "equity",
    "equity_etf": "equity",
    "bond_etf": "fixed_income",
    "commodity_etf": "commodity",
}


def asset_class_exposures(weights: dict[str, float]) -> dict[str, float]:
    """Aggregate weights into equity / fixed_income / commodity buckets.

    No look-through: a balanced fund holding both stocks and bonds would be
    miscounted. Our universe has none, but the limitation is real — see
    data/README.md.
    """
    exposures: dict[str, float] = {}
    universe = load_universe()
    for ticker, weight in weights.items():
        bucket = ECONOMIC_BUCKET[universe.by_ticker(ticker).asset_class]
        exposures[bucket] = exposures.get(bucket, 0.0) + weight
    return dict(sorted(exposures.items(), key=lambda kv: -kv[1]))


def sector_exposures(weights: dict[str, float]) -> dict[str, float]:
    """Aggregate weights by GICS sector.

    Instruments with `sector_cap_treatment` of `diversified` or `non_equity` are
    bucketed under their treatment rather than a sector — the compliance engine
    exempts them from sector caps, and burying them in a real sector here would
    hide that. No ETF look-through; see data/README.md.
    """
    exposures: dict[str, float] = {}
    universe = load_universe()
    for ticker, weight in weights.items():
        constituent = universe.by_ticker(ticker)
        if constituent.sector_cap_treatment == "constituent":
            assert constituent.sector is not None  # guaranteed by universe.json
            key = constituent.sector
        else:
            key = constituent.sector_cap_treatment
        exposures[key] = exposures.get(key, 0.0) + weight
    return dict(sorted(exposures.items(), key=lambda kv: -kv[1]))
