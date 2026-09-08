"""Walk-forward comparison of ORION 2.0 price-regime filters.

The filters are deliberately fixed before the run: EMA trend alignment, an
opening range no wider than 120 points, and an absolute overnight gap no
greater than 75 points.  A VIX filter can be enabled with an official daily
India VIX CSV; without it, no VIX-based result is claimed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict

import pandas as pd

from compare_orion_variants import VARIANTS, _summary, load_sessions, simulate


def load_vix(path: Path) -> pd.DataFrame:
    """Accept a daily VIX CSV with a date column and close/Close column."""
    vix = pd.read_csv(path)
    date_col = next((c for c in vix.columns if c.lower() in {"date", "timestamp"}), None)
    close_col = next((c for c in vix.columns if c.lower() in {"close", "vix", "value"}), None)
    if not date_col or not close_col:
        raise ValueError("VIX CSV needs date/timestamp and close/vix/value columns")
    vix = vix[[date_col, close_col]].rename(columns={date_col: "date", close_col: "vix"})
    vix["date"] = pd.to_datetime(vix["date"]).dt.date.astype(str)
    vix["vix"] = pd.to_numeric(vix["vix"], errors="coerce")
    vix = vix.dropna().sort_values("date")
    # Yesterday's 20-session mean only: never use today's close to decide today's trade.
    vix["vix_20d_prior"] = vix["vix"].rolling(20).mean().shift(1)
    return vix


def print_metrics(label: str, trades: pd.DataFrame) -> None:
    metrics = _summary(trades.to_dict("records"))
    print(f"{label:<32} {metrics['trades']:>4}   {metrics['win_rate']:>5.1f}% "
          f"Rs.{metrics['net_pnl']:>10,.2f}  {metrics['profit_factor']:>5}  Rs.{metrics['max_drawdown']:>9,.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=Path("data/historical/NIFTY_5m_365d.csv"))
    parser.add_argument("--lots", type=int, default=1)
    parser.add_argument("--vix-csv", type=Path, help="Optional official daily India VIX CSV")
    args = parser.parse_args()
    if args.lots < 1:
        parser.error("--lots must be at least one")

    variant = next(v for v in VARIANTS if v.name == "balanced_confirmation")
    sessions = load_sessions(args.csv)
    days = sorted(sessions)
    split = int(len(days) * .70)
    trades = pd.DataFrame(simulate((sessions[d] for d in days), variant, args.lots))
    train_end = str(days[split - 1])
    oos = trades[trades["date"] > train_end].copy()

    # Predefined regime rules—not fit to the results below.
    def price_filter(frame: pd.DataFrame) -> pd.DataFrame:
        return frame[
            frame["ema_aligned"]
            & (frame["opening_range"] <= 120.0)
            & (frame["gap_points"].abs() <= 75.0)
        ].copy()

    print(f"ORION 2.0 regime filter | {args.lots} lot(s) | OOS starts {days[split]}")
    print("\nFilter                           Trades  Win %   Net P&L       PF    Max DD")
    print("-" * 86)
    print_metrics("Baseline — full year", trades)
    print_metrics("EMA + gap/range — full year", price_filter(trades))
    print_metrics("Baseline — out-of-sample", oos)
    print_metrics("EMA + gap/range — out-of-sample", price_filter(oos))

    if args.vix_csv:
        vix = load_vix(args.vix_csv)
        joined = trades.merge(vix, on="date", how="inner")
        vix_filtered = joined[joined["vix"] <= joined["vix_20d_prior"] * 1.20].copy()
        print_metrics("Price + VIX — full year", price_filter(vix_filtered))
        print_metrics("Price + VIX — out-of-sample", price_filter(vix_filtered[vix_filtered["date"] > train_end]))
    else:
        print("\nVIX result not run: provide an official daily India VIX CSV with --vix-csv.")


if __name__ == "__main__":
    main()
