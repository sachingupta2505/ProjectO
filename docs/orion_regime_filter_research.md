# ORION 2.0 Regime Filter Research

Run date: 8 September 2026. Dataset: NIFTY 5-minute candles from 8 September
2025 through 8 September 2026. Position model: three NIFTY lots, the same
conservative option-delta, slippage, charge, and stop-first assumptions as the
ORION comparison backtest.

## Fixed price-regime filter

An ORION entry must satisfy all of the following in addition to its normal
opening and retest rules:

1. Entry direction agrees with the EMA-20 and its slope.
2. Opening 15-minute range is no wider than 120 NIFTY points.
3. Absolute overnight gap is no wider than 75 NIFTY points.

These values were specified before the result run; they were not selected to
maximize the reported P&L.

## Result

| Sample | Baseline ORION | Price-regime filtered ORION |
| --- | --- | --- |
| Full year | 64 trades, ₹45,940, PF 1.42, max DD -₹45,361 | 17 trades, ₹20,380, PF 1.95, max DD -₹11,753 |
| Out of sample | 13 trades, ₹20,010, PF 2.35, max DD -₹11,526 | 3 trades, ₹7,442, PF 111.26, max DD -₹68 |

The apparent out-of-sample improvement is **not deployable evidence** because
only three filtered trades remain. The filter is intentionally not connected to
the live paper bot yet.

## Next test

Supply an official daily India VIX CSV to:

`python scripts/backtest_orion_regime_filters.py --lots 3 --vix-csv path/to/india_vix.csv`

The script applies a pre-specified VIX guard: take the price-regime entry only
when the prior 20-session average India VIX is not exceeded by more than 20%.
Use the full out-of-sample result and require a materially larger trade sample
before considering paper deployment.
