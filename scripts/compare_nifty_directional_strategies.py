"""Fair, conservative comparison of three NIFTY directional option setups.

All variants use the same 5-minute NIFTY spot data, one-lot ATM option
approximation, option delta, slippage, charges, stop-first OHLC convention,
and chronological 70/30 split.  This is research only: actual historical
option bid/ask and IV are required before live deployment.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd


LOT_SIZE = 65
DELTA = 0.50
ENTRY_SLIPPAGE = 0.50
EXIT_SLIPPAGE = 0.50
ROUND_TRIP_CHARGES = 55.0


@dataclass
class TradePlan:
    entry_index: int
    side: str
    entry: float
    stop: float
    target1: float
    target2: float


def load_sessions(path: Path) -> Dict[Any, pd.DataFrame]:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.strftime("%H:%M")
    # EMA is calculated before selecting a session, so it uses only prices
    # available up to the current bar (including the prior session).
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df = df[(df["time"] >= "09:15") & (df["time"] <= "15:15")].copy()

    sessions: Dict[Any, pd.DataFrame] = {}
    prev_high: Optional[float] = None
    prev_low: Optional[float] = None
    for day, bars in df.groupby("date", sort=True):
        bars = bars.sort_values("timestamp").reset_index(drop=True)
        valid_open = len(bars) >= 4 and list(bars.loc[:2, "time"]) == ["09:15", "09:20", "09:25"]
        if valid_open and prev_high is not None:
            bars["previous_high"] = prev_high
            bars["previous_low"] = prev_low
            sessions[day] = bars
        if valid_open:
            prev_high, prev_low = float(bars["high"].max()), float(bars["low"].min())
    return sessions


def make_orion_plan(bars: pd.DataFrame) -> Optional[TradePlan]:
    opening = bars.iloc[:3]
    o, c = float(opening.iloc[0]["open"]), float(opening.iloc[2]["close"])
    high, low = float(opening["high"].max()), float(opening["low"].min())
    body = abs(c - o)
    if body < 30:
        return None
    is_call = c >= o
    opposing_wick = high - max(o, c) if is_call else min(o, c) - low
    if opposing_wick > body * 0.85:
        return None
    # Production ORION 2.0's 35-65% impulse retracement and 15% confirmation.
    zone_low, zone_high = (c - 0.65 * body, c - 0.35 * body) if is_call else (c + 0.35 * body, c + 0.65 * body)
    for i in range(3, len(bars)):
        bar = bars.iloc[i]
        if bar["time"] > "11:00":
            break
        overlaps = float(bar["low"]) <= zone_high and float(bar["high"]) >= zone_low
        directional = float(bar["close"]) >= float(bar["open"]) if is_call else float(bar["close"]) <= float(bar["open"])
        confirmed = abs(float(bar["close"]) - float(bar["open"])) >= body * 0.15
        if overlaps and directional and confirmed:
            entry = float(bar["close"])
            return TradePlan(i, "CALL" if is_call else "PUT", entry, low - 5 if is_call else high + 5,
                             high if is_call else low, high + .8 * body if is_call else low - .8 * body)
    return None


def make_orb_ema_plan(bars: pd.DataFrame) -> Optional[TradePlan]:
    """15-min opening-range breakout, then first EMA-aligned retest."""
    opening = bars.iloc[:3]
    range_high, range_low = float(opening["high"].max()), float(opening["low"].min())
    if range_high - range_low < 25:
        return None
    side: Optional[str] = None
    for i in range(3, len(bars)):
        bar = bars.iloc[i]
        if bar["time"] > "12:00":
            break
        close, high, low, ema = (float(bar[x]) for x in ("close", "high", "low", "ema20"))
        if side is None:
            if close > range_high and close > ema:
                side = "CALL"
            elif close < range_low and close < ema:
                side = "PUT"
            continue
        if side == "CALL" and low <= range_high + 2 and close > range_high and close > ema:
            stop = min(low - 5, range_low - 5)
            risk = close - stop
            if 8 <= risk <= 90:
                return TradePlan(i, side, close, stop, close + risk, close + 2 * risk)
        if side == "PUT" and high >= range_low - 2 and close < range_low and close < ema:
            stop = max(high + 5, range_high + 5)
            risk = stop - close
            if 8 <= risk <= 90:
                return TradePlan(i, side, close, stop, close - risk, close - 2 * risk)
    return None


def make_pdh_pdl_plan(bars: pd.DataFrame) -> Optional[TradePlan]:
    """Previous-day high/low breakout followed by the first level retest."""
    pdh, pdl = float(bars.iloc[0]["previous_high"]), float(bars.iloc[0]["previous_low"])
    side: Optional[str] = None
    level = 0.0
    for i in range(3, len(bars)):
        bar = bars.iloc[i]
        if bar["time"] > "14:00":
            break
        close, high, low, ema = (float(bar[x]) for x in ("close", "high", "low", "ema20"))
        if side is None:
            if close > pdh and close > ema:
                side, level = "CALL", pdh
            elif close < pdl and close < ema:
                side, level = "PUT", pdl
            continue
        if side == "CALL" and low <= level + 3 and close > level and close > ema:
            stop, risk = low - 5, close - (low - 5)
            if 8 <= risk <= 90:
                return TradePlan(i, side, close, stop, close + risk, close + 2 * risk)
        if side == "PUT" and high >= level - 3 and close < level and close < ema:
            stop, risk = high + 5, (high + 5) - close
            if 8 <= risk <= 90:
                return TradePlan(i, side, close, stop, close - risk, close - 2 * risk)
    return None


def simulate(sessions: Iterable[pd.DataFrame], planner: Callable[[pd.DataFrame], Optional[TradePlan]]) -> List[Dict[str, Any]]:
    trades: List[Dict[str, Any]] = []
    for bars in sessions:
        plan = planner(bars)
        if not plan:
            continue
        entry_option = 100.0 + ENTRY_SLIPPAGE
        spot_stop_distance = abs(plan.entry - plan.stop)
        option_stop_points = max(10.0, min(22.0, spot_stop_distance * DELTA))
        option_exit = entry_option - EXIT_SLIPPAGE
        breakeven = False
        reason = "EOD_CLOSE"
        for i in range(plan.entry_index + 1, len(bars)):
            bar = bars.iloc[i]
            favorable = float(bar["high"]) - plan.entry if plan.side == "CALL" else plan.entry - float(bar["low"])
            adverse = plan.entry - float(bar["low"]) if plan.side == "CALL" else float(bar["high"]) - plan.entry
            # Conservative: a bar touching both sides is stopped first.
            if adverse >= spot_stop_distance:
                option_exit = (entry_option + 1.0 if breakeven else entry_option - option_stop_points) - EXIT_SLIPPAGE
                reason = "BREAKEVEN" if breakeven else "STOP"
                break
            if favorable >= abs(plan.target1 - plan.entry):
                breakeven = True
            if favorable >= abs(plan.target2 - plan.entry):
                option_exit = entry_option + abs(plan.target2 - plan.entry) * DELTA - EXIT_SLIPPAGE
                reason = "TARGET_2"
                break
            if bar["time"] >= "15:15":
                move = float(bar["close"]) - plan.entry if plan.side == "CALL" else plan.entry - float(bar["close"])
                option_exit = entry_option + move * DELTA - EXIT_SLIPPAGE
                reason = "EOD"
                break
        trades.append({"date": str(bars.iloc[0]["date"]), "net_pnl": round((option_exit - entry_option) * LOT_SIZE - ROUND_TRIP_CHARGES, 2), "reason": reason})
    return trades


def summary(trades: List[Dict[str, Any]]) -> Dict[str, float | int]:
    if not trades:
        return dict(trades=0, win_rate=0.0, net_pnl=0.0, profit_factor=0.0, max_drawdown=0.0)
    df = pd.DataFrame(trades)
    winners, losers = df[df.net_pnl > 0], df[df.net_pnl <= 0]
    gross_loss = abs(float(losers.net_pnl.sum()))
    equity = df.net_pnl.cumsum()
    return dict(
        trades=len(df), win_rate=round(len(winners) / len(df) * 100, 1), net_pnl=round(float(df.net_pnl.sum()), 2),
        profit_factor=round(float(winners.net_pnl.sum()) / gross_loss, 2) if gross_loss else float("inf"),
        max_drawdown=round(float((equity - equity.cummax()).min()), 2),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=Path("data/historical/NIFTY_5m_365d.csv"))
    args = parser.parse_args()
    sessions = load_sessions(args.csv)
    days = sorted(sessions)
    split = int(len(days) * .70)
    samples = (("Train", days[:split]), ("Out-of-sample", days[split:]), ("Full year", days))
    strategies = (("ORION 2.0", make_orion_plan), ("ORB + EMA retest", make_orb_ema_plan), ("PDH/PDL retest", make_pdh_pdl_plan))
    print(f"Sessions: {len(days)} | Train: {days[0]} to {days[split-1]} | OOS: {days[split]} to {days[-1]}")
    print("\nStrategy             Sample           Trades  Win %   Net P&L       PF    Max DD")
    print("-" * 84)
    for name, planner in strategies:
        for sample, selected in samples:
            metrics = summary(simulate((sessions[d] for d in selected), planner))
            print(f"{name:<20} {sample:<15} {metrics['trades']:>4}   {metrics['win_rate']:>5.1f}% "
                  f"Rs.{metrics['net_pnl']:>10,.2f}  {metrics['profit_factor']:>5}  Rs.{metrics['max_drawdown']:>9,.2f}")


if __name__ == "__main__":
    main()
