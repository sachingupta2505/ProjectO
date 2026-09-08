"""Compare conservative ORION-15 entry variants on NIFTY 5-minute history.

This research tool deliberately leaves the live strategy untouched.  It uses a
chronological 70/30 train/out-of-sample split and reports all variants so a
parameter is not selected only because it fit the complete sample.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd


LOT_SIZE = 65
BASE_PREMIUM = 100.0
DELTA = 0.50
SLIPPAGE_OPTION_POINTS = 0.50
ROUND_TRIP_CHARGES = 55.0


@dataclass(frozen=True)
class Variant:
    name: str
    min_opening_body: float
    retrace_low: float
    retrace_high: float
    confirmation_body_ratio: float
    entry_cutoff: str


VARIANTS = (
    # Mirrors the current production logic: opening price through 50% retrace.
    Variant("baseline_current", 30.0, 0.50, 1.00, 0.0, "11:30"),
    # Avoid the deepest retracements and demand a non-doji rejection/bounce.
    Variant("balanced_confirmation", 30.0, 0.35, 0.65, 0.15, "11:00"),
    # More selective version for trend continuation only.
    Variant("strict_trend", 40.0, 0.30, 0.60, 0.25, "10:45"),
)


def load_sessions(csv_path: Path) -> Dict[Any, pd.DataFrame]:
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.strftime("%H:%M")
    df = df[(df["time"] >= "09:15") & (df["time"] <= "15:15")].copy()

    sessions: Dict[Any, pd.DataFrame] = {}
    for day, bars in df.groupby("date", sort=True):
        bars = bars.sort_values("timestamp").reset_index(drop=True)
        # Require the precise exchange opening bars. This excludes partial or
        # malformed sessions instead of treating the first available rows as 09:15.
        if len(bars) >= 4 and list(bars.loc[:2, "time"]) == ["09:15", "09:20", "09:25"]:
            sessions[day] = bars
    return sessions


def _zone(open_price: float, close_price: float, low: float, high: float) -> tuple[float, float]:
    """Return the requested retracement band measured from the opening impulse."""
    body = abs(close_price - open_price)
    if close_price >= open_price:
        return close_price - high * body, close_price - low * body
    return close_price + low * body, close_price + high * body


def _summary(trades: List[Dict[str, Any]]) -> Dict[str, float | int]:
    if not trades:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0}
    result = pd.DataFrame(trades)
    winners = result[result["net_pnl"] > 0]
    losers = result[result["net_pnl"] <= 0]
    gross_loss = abs(losers["net_pnl"].sum())
    equity = result["net_pnl"].cumsum()
    drawdown = equity - equity.cummax()
    return {
        "trades": len(result),
        "wins": len(winners),
        "win_rate": round(len(winners) / len(result) * 100, 1),
        "net_pnl": round(result["net_pnl"].sum(), 2),
        "profit_factor": round(winners["net_pnl"].sum() / gross_loss, 2) if gross_loss else float("inf"),
        "max_drawdown": round(drawdown.min(), 2),
    }


def simulate(sessions: Iterable[pd.DataFrame], variant: Variant) -> List[Dict[str, Any]]:
    trades: List[Dict[str, Any]] = []
    for day_bars in sessions:
        first_three = day_bars.iloc[:3]
        opening_open = float(first_three.iloc[0]["open"])
        opening_close = float(first_three.iloc[2]["close"])
        opening_high = float(first_three["high"].max())
        opening_low = float(first_three["low"].min())
        body = abs(opening_close - opening_open)
        if body < variant.min_opening_body:
            continue

        is_call = opening_close >= opening_open
        opposing_wick = (
            opening_high - max(opening_open, opening_close)
            if is_call
            else min(opening_open, opening_close) - opening_low
        )
        if opposing_wick > body * 0.85:
            continue

        zone_low, zone_high = _zone(
            opening_open, opening_close, variant.retrace_low, variant.retrace_high
        )
        invalidation = opening_low - 5.0 if is_call else opening_high + 5.0
        target1 = opening_high if is_call else opening_low
        target2 = opening_high + body * 0.80 if is_call else opening_low - body * 0.80
        entry_index = None

        for index in range(3, len(day_bars)):
            bar = day_bars.iloc[index]
            if bar["time"] > variant.entry_cutoff:
                break
            overlaps_zone = bar["low"] <= zone_high and bar["high"] >= zone_low
            directional = bar["close"] >= bar["open"] if is_call else bar["close"] <= bar["open"]
            confirmation_body = abs(float(bar["close"]) - float(bar["open"]))
            if overlaps_zone and directional and confirmation_body >= body * variant.confirmation_body_ratio:
                entry_index = index
                break

        if entry_index is None:
            continue

        entry_bar = day_bars.iloc[entry_index]
        entry_spot = float(entry_bar["close"])
        sl_spot_distance = abs(entry_spot - invalidation)
        target1_distance = abs(target1 - entry_spot)
        target2_distance = abs(target2 - entry_spot)
        option_entry = BASE_PREMIUM + SLIPPAGE_OPTION_POINTS
        option_stop_points = max(10.0, min(22.0, sl_spot_distance * DELTA))
        option_stop = option_entry - option_stop_points
        option_target = option_entry + target2_distance * DELTA
        breakeven_active = False
        exit_price = option_entry - SLIPPAGE_OPTION_POINTS
        exit_reason = "EOD_CLOSE"

        for index in range(entry_index + 1, len(day_bars)):
            bar = day_bars.iloc[index]
            favorable_move = (bar["high"] - entry_spot) if is_call else (entry_spot - bar["low"])
            adverse_move = (entry_spot - bar["low"]) if is_call else (bar["high"] - entry_spot)

            # Conservative OHLC assumption: when both adverse and favourable
            # levels occur in one bar, treat the protective stop as first.
            if adverse_move >= option_stop_points / DELTA:
                exit_price = (option_entry + 1.0 if breakeven_active else option_stop) - SLIPPAGE_OPTION_POINTS
                exit_reason = "BREAKEVEN_EXIT" if breakeven_active else "STOP_LOSS"
                break
            if favorable_move >= target1_distance:
                breakeven_active = True
            if favorable_move >= target2_distance:
                exit_price = option_target - SLIPPAGE_OPTION_POINTS
                exit_reason = "FULL_TARGET"
                break
            if bar["time"] >= "15:15":
                close_move = (bar["close"] - entry_spot) if is_call else (entry_spot - bar["close"])
                exit_price = option_entry + close_move * DELTA - SLIPPAGE_OPTION_POINTS
                exit_reason = "EOD_SQUARE_OFF"
                break

        trades.append({
            "date": str(entry_bar["date"]),
            "side": "CALL" if is_call else "PUT",
            "entry_time": entry_bar["time"],
            "exit_reason": exit_reason,
            "net_pnl": round((exit_price - option_entry) * LOT_SIZE - ROUND_TRIP_CHARGES, 2),
        })
    return trades


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=Path("data/historical/NIFTY_5m_365d.csv"))
    args = parser.parse_args()
    sessions = load_sessions(args.csv)
    ordered_days = sorted(sessions)
    split = int(len(ordered_days) * 0.70)
    train_days, test_days = ordered_days[:split], ordered_days[split:]

    print(f"Sessions: {len(ordered_days)} | Train: {train_days[0]} to {train_days[-1]} ({len(train_days)}) | "
          f"Out-of-sample: {test_days[0]} to {test_days[-1]} ({len(test_days)})")
    print("\nVariant                 Sample          Trades  Win %   Net P&L       PF    Max DD")
    print("-" * 83)
    for variant in VARIANTS:
        for sample, days in (("train", train_days), ("out-of-sample", test_days), ("full year", ordered_days)):
            metrics = _summary(simulate((sessions[day] for day in days), variant))
            print(f"{variant.name:<23} {sample:<14} {metrics['trades']:>4}   {metrics['win_rate']:>5.1f}% "
                  f"Rs.{metrics['net_pnl']:>10,.2f}  {metrics['profit_factor']:>5}  Rs.{metrics['max_drawdown']:>9,.2f}")


if __name__ == "__main__":
    main()
