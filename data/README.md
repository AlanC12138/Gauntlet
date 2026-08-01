# Demo Universe

`universe.json` is the fixed investable universe for Gauntlet. It is the single source of truth for **which instruments exist** and **what sector each belongs to** — the latter being a hard dependency for the Constraint-Compliance Agent's sector-cap rules (Phase 2).

Verified against yfinance: all 52 constituents return 3,270 trading days over the declared window with no gaps.

## Composition

52 instruments: 39 US large-cap equities + 13 ETFs.

| Sector (GICS) | Equities | Sector ETF |
|---|---|---|
| Information Technology | AAPL, MSFT, NVDA, AVGO | XLK |
| Communication Services | GOOGL, META, DIS | — |
| Consumer Discretionary | AMZN, HD, MCD, TSLA | — |
| Consumer Staples | PG, KO, WMT, PM | — |
| Financials | JPM, BAC, V, GS | — |
| Health Care | JNJ, UNH, LLY, PFE | — |
| Industrials | CAT, HON, UNP, LMT | — |
| Energy | XOM, CVX, COP | XLE |
| Utilities | NEE, DUK, SO | — |
| Materials | APD, SHW, NEM | — |
| Real Estate | AMT, PLD, SPG | — |

Plus 3 diversified equity ETFs (SPY, QQQ, IWM), 7 fixed-income ETFs spanning
duration and credit (SHY, IEF, TLT for Treasuries across the curve; AGG for the
aggregate index; TIP for inflation-linked; LQD and HYG for investment-grade and
high-yield credit), and 1 commodity (GLD).

**Why seven bond funds.** The universe originally carried two, which is enough
for an equity-tilted mandate and structurally too few for a conservative one: a
30% fixed-income floor under an 8% single-position cap needs at least four
instruments to be reachable at all, and two caps it at 16%. That made the
conservative demo mandate arithmetically impossible — a defect found only after a
live run spent three iterations failing to satisfy it. A universe intended to
support balanced and conservative mandates needs a real fixed-income menu, not a
token one. `tests/test_asset_class_bands.py` now asserts this feasibility
directly.

## Schema

Each constituent has five fields:

| Field | Values |
|---|---|
| `ticker` | yfinance-compatible symbol |
| `name` | Full legal name |
| `asset_class` | `equity`, `sector_etf`, `equity_etf`, `bond_etf`, `commodity_etf` |
| `sector` | GICS sector string, or `null` for non-sector-attributable instruments |
| `sector_cap_treatment` | `constituent`, `diversified`, `non_equity` |

`sector_cap_treatment` exists so the compliance engine has an **explicit, auditable rule** for ETFs rather than a special case buried in code:

- **`constituent`** — weight counts in full toward `sector`'s cap. All 39 equities plus XLK/XLE.
- **`diversified`** — broad multi-sector equity, no single-sector attribution. Exempt from sector caps, counts toward total equity exposure. SPY, QQQ, IWM.
- **`non_equity`** — exempt from sector caps *and* from total equity exposure. The 7 bond ETFs plus GLD.

Note that `sector_cap_treatment` and the asset-class bucket answer different
questions. SPY is `diversified` (exempt from sector caps) but is unambiguously
*equity* exposure; conflating the two would let a mandate's bond floor be
satisfied with an index fund. The bucket mapping lives in `gauntlet/universe.py`.

## Decisions

**Window: 2013-01-01 → 2025-12-31 (13 years, 3,270 trading days).**
The start is set by META's 2012-05-18 IPO, the latest inception in the set, rounded up to a clean year boundary. The end is *fixed* rather than "today" so backtest results are reproducible run-to-run — same reasoning as the append-only price cache in [TECH_STACK.md](../TECH_STACK.md). The window contains four genuine drawdowns (Aug 2015, Q4 2018, COVID 2020, the 2022 bear), which is what makes it a real test of the drawdown and risk math rather than a monotonic uptrend.

**Sector ETFs included deliberately.** XLK and XLE map cleanly to one sector each, so they force the compliance engine to handle "an ETF that *does* count toward a cap" alongside "an ETF that doesn't." Without them the ETF rule collapses to a blanket exemption and never gets exercised.

**Sized so constraints actually bind.** With 3–4 names per sector, an equal-weight allocation lands each sector at roughly 8–10% — under any sane cap. A cap only trips when the allocator *concentrates*, which is exactly the failure mode worth catching: an LLM asked for growth exposure will reach for AAPL/MSFT/NVDA/AVGO and blow a 25% tech cap. Likewise a 10% single-position limit only binds below ~10 holdings.

**No ESG or exclusion labels in this file — on purpose.** The universe deliberately includes names that a qualitative screen should flag (PM tobacco; XOM/CVX/COP/XLE fossil fuel; LMT defense; NEM mining), but it carries no labels saying so. Those labels belong in the Phase 3 eval fixtures, not here, because the Screening Agent reads this file. Putting the answers in its input would make both the agent and its precision/recall numbers meaningless.

## Known limitations

These are real and should stay documented rather than quietly papered over.

**Survivorship bias.** The constituents were chosen from companies that are large and liquid *today*. Nothing that was delisted, acquired, or collapsed between 2013 and 2025 is here, so any backtest over this universe is biased upward. Acceptable for a demo; not acceptable as a performance claim, and the final memo should not present backtest returns as an unbiased estimate.

**Sector labels are not point-in-time.** Current-day GICS labels are applied across the whole window, but the taxonomy itself changed inside it — Real Estate was split out in 2016, Communication Services in 2018, and Visa/Mastercard moved from Information Technology to Financials in March 2023. V is labeled Financials for all 13 years even though it was classified as tech for the first ten. Historical sector-cap checks therefore reflect today's taxonomy, not the one in force at the time.

**ETF/equity overlap is not looked through.** Holding QQQ *and* AAPL means real economic exposure to AAPL well above its stated weight, and QQQ is ~50% technology despite being treated as `diversified`. The compliance engine takes stated weights at face value. True look-through would need constituent-level holdings data per ETF per date; the `sector_cap_treatment` field is the deliberate simplification, not an oversight.

**Use dividend-adjusted prices.** Fetch with `auto_adjust=True`. Over 13 years the difference is large and uneven across this universe — PM, XOM, DUK, and SO carry high dividend yields, so a price-return series would systematically understate them relative to AAPL or NVDA and distort every risk metric downstream.

## Re-verifying

[`scripts/verify_universe.py`](../scripts/verify_universe.py) re-checks every ticker against yfinance and exits non-zero on late inception, sparse coverage, or an early end. Run it after any edit to `universe.json`:

```bash
python scripts/verify_universe.py
```
