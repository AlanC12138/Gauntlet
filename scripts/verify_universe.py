"""Verify every ticker in data/universe.json actually has continuous price history
covering the declared backtest window. Fails loudly rather than silently truncating."""
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

UNIVERSE = Path(__file__).resolve().parent.parent / "data" / "universe.json"

with open(UNIVERSE) as f:
    u = json.load(f)

tickers = [c["ticker"] for c in u["constituents"]]
win_start = pd.Timestamp(u["backtest_window"]["start"])
win_end = pd.Timestamp(u["backtest_window"]["end"])

print(f"{len(tickers)} tickers | window {win_start.date()} -> {win_end.date()}\n")

# Pull from well before the window so we can see each ticker's true inception.
raw = yf.download(
    tickers, start="1990-01-01", end="2026-07-31",
    auto_adjust=True, progress=False, group_by="column", threads=True,
)
close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]

# Expected trading days in-window, taken from the ticker with the densest coverage.
in_win = close.loc[win_start:win_end]
expected_days = int(in_win.notna().sum().max())

rows, problems = [], []
for t in tickers:
    if t not in close.columns:
        problems.append(f"{t}: NOT RETURNED by yfinance")
        continue
    s = close[t].dropna()
    if s.empty:
        problems.append(f"{t}: no data at all")
        continue

    first, last = s.index[0], s.index[-1]
    sw = s.loc[win_start:win_end]
    n = len(sw)
    coverage = n / expected_days if expected_days else 0.0

    status = "ok"
    if first.tz_localize(None) > win_start:
        status = "LATE INCEPTION"
        problems.append(f"{t}: first bar {first.date()} is after window start {win_start.date()}")
    elif coverage < 0.98:
        status = f"SPARSE ({coverage:.1%})"
        problems.append(f"{t}: only {n}/{expected_days} bars in window ({coverage:.1%})")
    if last.tz_localize(None) < win_end - pd.Timedelta(days=10):
        status = "EARLY END"
        problems.append(f"{t}: last bar {last.date()} well before window end {win_end.date()}")

    rows.append({
        "ticker": t, "inception": first.date(), "last": last.date(),
        "bars_in_window": n, "coverage": f"{coverage:.1%}", "status": status,
    })

df = pd.DataFrame(rows).sort_values("inception", ascending=False)
pd.set_option("display.max_rows", None, "display.width", 200)
print(df.to_string(index=False))

print(f"\nExpected trading days in window: {expected_days}")
print("Latest inception among all constituents:", df["inception"].max())

print("\n" + "=" * 70)
if problems:
    print(f"{len(problems)} PROBLEM(S):")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("PASS - all tickers cover the full declared window.")
