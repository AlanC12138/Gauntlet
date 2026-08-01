---
doc_id: broad-fund-look-through
category: Methodology
tickers: [SPY, QQQ, IWM, XLE, XLK, AGG, TLT]
exclusion: false
---

# Exclusions and Pooled Vehicles

An exclusion written against companies does not automatically resolve when the
exposure is held through a fund. A broad-market index fund holds the excluded
names in proportion to their index weight, so a portfolio can satisfy a screen
name-by-name while retaining indirect exposure through its passive sleeve.

Institutional practice splits three ways:

1. **Look-through** — decompose the fund and apply the screen to its holdings.
   Most rigorous, and requires constituent-level holdings data per fund per
   date.
2. **De minimis** — accept indirect exposure below a threshold of total
   portfolio value on materiality grounds.
3. **Vehicle substitution** — replace the broad fund with a screened equivalent
   (an ESG-tilted or ex-fossil-fuel index fund).

**How this project handles it:** Gauntlet does **not** perform look-through. The
compliance engine takes stated weights at face value, and each instrument's
`sector_cap_treatment` in `data/universe.json` determines whether it counts
toward a sector cap. This is a stated simplification, documented in
`data/README.md`, not an oversight.

Two consequences a screening agent should understand:

- **SPY, QQQ, IWM** hold excluded names indirectly at index weight. QQQ in
  particular is heavily concentrated in technology despite being treated as
  diversified. Worth noting as residual exposure; not a violation of a
  name-level screen.
- **XLE** is different in kind. It is a *sector* fund whose holdings are
  predominantly oil and gas producers, so holding it is a deliberate,
  concentrated allocation to an excluded activity rather than incidental index
  exposure. A fossil-fuel screen that permits XLE while excluding XOM is
  incoherent.

**AGG** and **TLT** hold debt, not equity — investment-grade aggregate bonds and
long-dated Treasuries respectively. Treasury exposure carries no corporate
issuer at all. Equity exclusions do not straightforwardly transfer to these.
