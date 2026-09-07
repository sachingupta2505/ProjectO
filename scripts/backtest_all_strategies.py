"""
Comprehensive Multi-Strategy Backtest Engine (180 Days / 6 Months).
Backtests all 4 institutional strategies on NIFTY 50 (5-minute data):
1. ORION-15: 15-Minute Opening Range Breakout & Retest
2. CPR-Institutional: Central Pivot Range Breakout (Narrow) & Range Fade (Wide)
3. ICT-Liquidity: PDH/PDL Liquidity Sweeps & Mean-Reversion
4. THETA-0DTE: Tuesday Expiry Afternoon Strangle Decay

Contract sizing: 2 Lots (130 Qty).
Capital allocation: ₹1,00,000 per strategy.
Realistic slippage, option delta, and exchange turnover/STT/brokerage fees included.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import json
from pathlib import Path
from datetime import datetime, time as dtime
import pandas as pd
import numpy as np

CSV_PATH = Path("data/historical/NIFTY_5m_180d.csv")
LOTS = 2
LOT_SIZE = 65 * LOTS  # 130 Qty
CAPITAL_PER_STRATEGY = 100000.0  # ₹1,00,000 virtual capital
SLIPPAGE_PTS = 0.50  # 0.5 pt slippage on options

def load_data():
    df = pd.read_csv(CSV_PATH)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.strftime("%H:%M")
    return df

# ==============================================================================
# 1. ORION-15 Strategy Simulator
# ==============================================================================
def backtest_orion15(df, days):
    trades = []
    BASE_PREMIUM = 110.0
    DELTA = 0.50
    ROUND_TRIP_CHARGES = 55.0

    for d in days:
        day_bars = df[df["date"] == d].sort_values("timestamp").reset_index(drop=True)
        if len(day_bars) < 15:
            continue

        first_3 = day_bars.iloc[:3]
        o15 = first_3.iloc[0]["open"]
        c15 = first_3.iloc[2]["close"]
        h15 = first_3["high"].max()
        l15 = first_3["low"].min()
        range15 = h15 - l15
        body15 = abs(c15 - o15)
        is_green = c15 >= o15

        if body15 < 30.0 or range15 <= 0:
            continue

        upper_wick = h15 - max(o15, c15)
        lower_wick = min(o15, c15) - l15
        if is_green and upper_wick > (body15 * 0.85):
            continue
        if not is_green and lower_wick > (body15 * 0.85):
            continue

        retest_mid = o15 + (c15 - o15) * 0.50
        retest_min = min(o15, retest_mid)
        retest_max = max(o15, retest_mid)

        if is_green:
            invalidation_spot = l15 - 5.0
            target1_spot = h15
            target2_spot = h15 + (body15 * 0.80)
        else:
            invalidation_spot = h15 + 5.0
            target1_spot = l15
            target2_spot = l15 - (body15 * 0.80)

        in_trade = False
        entry_bar_idx = -1
        entry_spot = 0.0
        side = "CALL" if is_green else "PUT"

        for i in range(3, min(len(day_bars), 27)):
            bar = day_bars.iloc[i]
            if is_green:
                if bar["low"] <= (retest_max + 5.0) and bar["high"] >= retest_min and bar["close"] >= bar["open"]:
                    entry_spot = bar["close"]
                    in_trade = True
                    entry_bar_idx = i
                    break
            else:
                if bar["high"] >= (retest_min - 5.0) and bar["low"] <= retest_max and bar["close"] <= bar["open"]:
                    entry_spot = bar["close"]
                    in_trade = True
                    entry_bar_idx = i
                    break

        if not in_trade:
            continue

        entry_time = day_bars.iloc[entry_bar_idx]["timestamp"]
        sl_spot_dist = abs(entry_spot - invalidation_spot)
        target1_dist = abs(target1_spot - entry_spot)
        target2_dist = abs(target2_spot - entry_spot)

        opt_entry = BASE_PREMIUM + SLIPPAGE_PTS
        opt_sl_pts = max(10.0, min(22.0, sl_spot_dist * DELTA))
        opt_sl_price = opt_entry - opt_sl_pts
        opt_t2_pts = target2_dist * DELTA
        opt_t2_price = opt_entry + opt_t2_pts

        breakeven_active = False
        exit_opt_price = 0.0
        exit_reason = ""
        exit_time = None

        for j in range(entry_bar_idx + 1, len(day_bars)):
            curr_bar = day_bars.iloc[j]
            c_high, c_low, c_close, curr_time = curr_bar["high"], curr_bar["low"], curr_bar["close"], curr_bar["time"]

            spot_move = (c_high - entry_spot) if is_green else (entry_spot - c_low)
            spot_adverse = (entry_spot - c_low) if is_green else (c_high - entry_spot)

            if spot_move >= target1_dist and not breakeven_active:
                breakeven_active = True
                opt_sl_price = opt_entry + 1.0

            if (entry_spot - spot_adverse) <= (entry_spot - (opt_sl_pts / DELTA)):
                exit_opt_price = opt_sl_price - SLIPPAGE_PTS
                exit_reason = "BREAKEVEN_EXIT" if breakeven_active else "STOP_LOSS"
                exit_time = curr_bar["timestamp"]
                break

            if spot_move >= target2_dist:
                exit_opt_price = opt_t2_price - SLIPPAGE_PTS
                exit_reason = "FULL_TARGET"
                exit_time = curr_bar["timestamp"]
                break

            if curr_time >= "15:15":
                exit_spot_move = (c_close - entry_spot) if is_green else (entry_spot - c_close)
                exit_opt_price = opt_entry + (exit_spot_move * DELTA) - SLIPPAGE_PTS
                exit_reason = "EOD_SQUARE_OFF"
                exit_time = curr_bar["timestamp"]
                break

        if exit_time is None:
            exit_opt_price = opt_entry - SLIPPAGE_PTS
            exit_reason = "EOD_CLOSE"
            exit_time = day_bars.iloc[-1]["timestamp"]

        opt_pts_gain = exit_opt_price - opt_entry
        gross_pnl = opt_pts_gain * LOT_SIZE
        net_pnl = gross_pnl - ROUND_TRIP_CHARGES

        trades.append({
            "strategy": "ORION-15",
            "date": str(d),
            "side": side,
            "entry_time": str(entry_time),
            "exit_time": str(exit_time),
            "entry_spot": entry_spot,
            "exit_reason": exit_reason,
            "opt_entry": round(opt_entry, 2),
            "opt_exit": round(exit_opt_price, 2),
            "opt_pts": round(opt_pts_gain, 2),
            "gross_pnl": round(gross_pnl, 2),
            "charges": ROUND_TRIP_CHARGES,
            "net_pnl": round(net_pnl, 2),
            "won": net_pnl > 0
        })

    return trades

# ==============================================================================
# 2. CPR-Institutional Strategy Simulator
# ==============================================================================
def backtest_cpr(df, days):
    trades = []
    BASE_PREMIUM = 110.0
    DELTA = 0.50
    ROUND_TRIP_CHARGES = 55.0

    for idx in range(1, len(days)):
        prev_d = days[idx - 1]
        curr_d = days[idx]

        prev_bars = df[df["date"] == prev_d].sort_values("timestamp")
        curr_bars = df[df["date"] == curr_d].sort_values("timestamp").reset_index(drop=True)
        if len(prev_bars) < 15 or len(curr_bars) < 15:
            continue

        pdh = prev_bars["high"].max()
        pdl = prev_bars["low"].min()
        pdc = prev_bars.iloc[-1]["close"]

        pivot = round((pdh + pdl + pdc) / 3.0, 1)
        bc = round((pdh + pdl) / 2.0, 1)
        tc = round((2.0 * pivot) - bc, 1)
        top_cpr = max(tc, bc)
        bottom_cpr = min(tc, bc)
        cpr_width = top_cpr - bottom_cpr
        cpr_width_pct = (cpr_width / pivot) * 100.0
        is_narrow = cpr_width_pct < 0.20

        r1 = (2.0 * pivot) - pdl
        s1 = (2.0 * pivot) - pdh
        r2 = pivot + (pdh - pdl)
        s2 = pivot - (pdh - pdl)

        in_trade = False
        side = None
        entry_spot = 0.0
        entry_idx = -1
        target1_spot = 0.0
        target2_spot = 0.0
        sl_spot = 0.0

        for i, bar in curr_bars.iterrows():
            t_str = bar["time"]
            if t_str < "09:30" or t_str > "14:30":
                continue

            c_open, c_high, c_low, c_close = bar["open"], bar["high"], bar["low"], bar["close"]
            rng = c_high - c_low

            if is_narrow:
                if c_close > top_cpr and c_open <= top_cpr:
                    side = "CALL"
                    entry_spot = c_close
                    entry_idx = i
                    target1_spot = r1
                    target2_spot = r2
                    sl_spot = pivot
                    in_trade = True
                    break
                elif c_close < bottom_cpr and c_open >= bottom_cpr:
                    side = "PUT"
                    entry_spot = c_close
                    entry_idx = i
                    target1_spot = s1
                    target2_spot = s2
                    sl_spot = pivot
                    in_trade = True
                    break
            else:
                if rng > 0:
                    upper_wick = c_high - max(c_open, c_close)
                    lower_wick = min(c_open, c_close) - c_low
                    if c_high >= top_cpr and c_close < top_cpr and (upper_wick / rng) >= 0.45:
                        side = "PUT"
                        entry_spot = c_close
                        entry_idx = i
                        target1_spot = pivot
                        target2_spot = bottom_cpr
                        sl_spot = top_cpr + 15.0
                        in_trade = True
                        break
                    elif c_low <= bottom_cpr and c_close > bottom_cpr and (lower_wick / rng) >= 0.45:
                        side = "CALL"
                        entry_spot = c_close
                        entry_idx = i
                        target1_spot = pivot
                        target2_spot = top_cpr
                        sl_spot = bottom_cpr - 15.0
                        in_trade = True
                        break

        if not in_trade:
            continue

        entry_time = curr_bars.iloc[entry_idx]["timestamp"]
        sl_spot_dist = abs(entry_spot - sl_spot)
        target1_dist = abs(target1_spot - entry_spot)
        target2_dist = abs(target2_spot - entry_spot)

        opt_entry = BASE_PREMIUM + SLIPPAGE_PTS
        opt_sl_pts = max(10.0, min(25.0, sl_spot_dist * DELTA))
        opt_sl_price = opt_entry - opt_sl_pts
        opt_t2_pts = max(12.0, target2_dist * DELTA)
        opt_t2_price = opt_entry + opt_t2_pts

        breakeven_active = False
        exit_opt_price = 0.0
        exit_reason = ""
        exit_time = None

        for j in range(entry_idx + 1, len(curr_bars)):
            c_bar = curr_bars.iloc[j]
            c_high, c_low, c_close, curr_time = c_bar["high"], c_bar["low"], c_bar["close"], c_bar["time"]

            spot_move = (c_high - entry_spot) if side == "CALL" else (entry_spot - c_low)
            spot_adverse = (entry_spot - c_low) if side == "CALL" else (c_high - entry_spot)

            if spot_move >= target1_dist and not breakeven_active:
                breakeven_active = True
                opt_sl_price = opt_entry + 1.0

            if (entry_spot - spot_adverse) <= (entry_spot - (opt_sl_pts / DELTA)):
                exit_opt_price = opt_sl_price - SLIPPAGE_PTS
                exit_reason = "BREAKEVEN_EXIT" if breakeven_active else "STOP_LOSS"
                exit_time = c_bar["timestamp"]
                break

            if spot_move >= target2_dist:
                exit_opt_price = opt_t2_price - SLIPPAGE_PTS
                exit_reason = "FULL_TARGET"
                exit_time = c_bar["timestamp"]
                break

            if curr_time >= "15:15":
                exit_spot_move = (c_close - entry_spot) if side == "CALL" else (entry_spot - c_close)
                exit_opt_price = opt_entry + (exit_spot_move * DELTA) - SLIPPAGE_PTS
                exit_reason = "EOD_SQUARE_OFF"
                exit_time = c_bar["timestamp"]
                break

        if exit_time is None:
            exit_opt_price = opt_entry - SLIPPAGE_PTS
            exit_reason = "EOD_CLOSE"
            exit_time = curr_bars.iloc[-1]["timestamp"]

        opt_pts_gain = exit_opt_price - opt_entry
        gross_pnl = opt_pts_gain * LOT_SIZE
        net_pnl = gross_pnl - ROUND_TRIP_CHARGES

        trades.append({
            "strategy": "CPR-Institutional",
            "date": str(curr_d),
            "side": side,
            "mode": "NARROW_BREAKOUT" if is_narrow else "WIDE_FADE",
            "entry_time": str(entry_time),
            "exit_time": str(exit_time),
            "entry_spot": entry_spot,
            "exit_reason": exit_reason,
            "opt_entry": round(opt_entry, 2),
            "opt_exit": round(exit_opt_price, 2),
            "opt_pts": round(opt_pts_gain, 2),
            "gross_pnl": round(gross_pnl, 2),
            "charges": ROUND_TRIP_CHARGES,
            "net_pnl": round(net_pnl, 2),
            "won": net_pnl > 0
        })

    return trades

# ==============================================================================
# 3. ICT-Liquidity Strategy Simulator
# ==============================================================================
def backtest_ict(df, days):
    trades = []
    BASE_PREMIUM = 110.0
    DELTA = 0.50
    ROUND_TRIP_CHARGES = 55.0

    for idx in range(1, len(days)):
        prev_d = days[idx - 1]
        curr_d = days[idx]

        prev_bars = df[df["date"] == prev_d].sort_values("timestamp")
        curr_bars = df[df["date"] == curr_d].sort_values("timestamp").reset_index(drop=True)
        if len(prev_bars) < 15 or len(curr_bars) < 15:
            continue

        pdh = prev_bars["high"].max()
        pdl = prev_bars["low"].min()
        midpoint = round((pdh + pdl) / 2.0, 1)

        in_trade = False
        side = None
        entry_spot = 0.0
        entry_idx = -1
        target1_spot = midpoint
        target2_spot = 0.0
        sl_spot = 0.0

        for i, bar in curr_bars.iterrows():
            t_str = bar["time"]
            if t_str < "09:45" or t_str > "13:30":
                continue

            c_open, c_high, c_low, c_close = bar["open"], bar["high"], bar["low"], bar["close"]
            rng = c_high - c_low
            if rng <= 0:
                continue

            upper_wick = c_high - max(c_open, c_close)
            lower_wick = min(c_open, c_close) - c_low

            if c_high > pdh and c_high <= (pdh + 30.0) and c_close < pdh and (upper_wick / rng) >= 0.40:
                side = "PUT"
                entry_spot = c_close
                entry_idx = i
                sl_spot = c_high + 5.0
                target2_spot = pdl
                in_trade = True
                break
            elif c_low < pdl and c_low >= (pdl - 30.0) and c_close > pdl and (lower_wick / rng) >= 0.40:
                side = "CALL"
                entry_spot = c_close
                entry_idx = i
                sl_spot = c_low - 5.0
                target2_spot = pdh
                in_trade = True
                break

        if not in_trade:
            continue

        entry_time = curr_bars.iloc[entry_idx]["timestamp"]
        sl_spot_dist = abs(entry_spot - sl_spot)
        target1_dist = abs(target1_spot - entry_spot)
        target2_dist = abs(target2_spot - entry_spot)

        opt_entry = BASE_PREMIUM + SLIPPAGE_PTS
        opt_sl_pts = max(10.0, min(24.0, sl_spot_dist * DELTA))
        opt_sl_price = opt_entry - opt_sl_pts
        opt_t2_pts = max(15.0, target2_dist * DELTA)
        opt_t2_price = opt_entry + opt_t2_pts

        breakeven_active = False
        exit_opt_price = 0.0
        exit_reason = ""
        exit_time = None

        for j in range(entry_idx + 1, len(curr_bars)):
            c_bar = curr_bars.iloc[j]
            c_high, c_low, c_close, curr_time = c_bar["high"], c_bar["low"], c_bar["close"], c_bar["time"]

            spot_move = (c_high - entry_spot) if side == "CALL" else (entry_spot - c_low)
            spot_adverse = (entry_spot - c_low) if side == "CALL" else (c_high - entry_spot)

            if spot_move >= target1_dist and not breakeven_active:
                breakeven_active = True
                opt_sl_price = opt_entry + 1.0

            if (entry_spot - spot_adverse) <= (entry_spot - (opt_sl_pts / DELTA)):
                exit_opt_price = opt_sl_price - SLIPPAGE_PTS
                exit_reason = "BREAKEVEN_EXIT" if breakeven_active else "STOP_LOSS"
                exit_time = c_bar["timestamp"]
                break

            if spot_move >= target2_dist:
                exit_opt_price = opt_t2_price - SLIPPAGE_PTS
                exit_reason = "FULL_TARGET"
                exit_time = c_bar["timestamp"]
                break

            if curr_time >= "15:15":
                exit_spot_move = (c_close - entry_spot) if side == "CALL" else (entry_spot - c_close)
                exit_opt_price = opt_entry + (exit_spot_move * DELTA) - SLIPPAGE_PTS
                exit_reason = "EOD_SQUARE_OFF"
                exit_time = c_bar["timestamp"]
                break

        if exit_time is None:
            exit_opt_price = opt_entry - SLIPPAGE_PTS
            exit_reason = "EOD_CLOSE"
            exit_time = curr_bars.iloc[-1]["timestamp"]

        opt_pts_gain = exit_opt_price - opt_entry
        gross_pnl = opt_pts_gain * LOT_SIZE
        net_pnl = gross_pnl - ROUND_TRIP_CHARGES

        trades.append({
            "strategy": "ICT-Liquidity",
            "date": str(curr_d),
            "side": side,
            "entry_time": str(entry_time),
            "exit_time": str(exit_time),
            "entry_spot": entry_spot,
            "exit_reason": exit_reason,
            "opt_entry": round(opt_entry, 2),
            "opt_exit": round(exit_opt_price, 2),
            "opt_pts": round(opt_pts_gain, 2),
            "gross_pnl": round(gross_pnl, 2),
            "charges": ROUND_TRIP_CHARGES,
            "net_pnl": round(net_pnl, 2),
            "won": net_pnl > 0
        })

    return trades

# ==============================================================================
# 4. THETA-0DTE Strangle Strategy Simulator
# ==============================================================================
def backtest_theta(df, days):
    trades = []
    CE_ENTRY_PREM = 26.0
    PE_ENTRY_PREM = 26.0
    SL_RATIO = 0.25
    ROUND_TRIP_CHARGES = 110.0

    for d in days:
        if d.weekday() != 1:  # Tuesday expiry
            continue

        day_bars = df[df["date"] == d].sort_values("timestamp").reset_index(drop=True)
        if len(day_bars) < 35:
            continue

        entry_idx = -1
        for i, bar in day_bars.iterrows():
            if bar["time"] >= "12:45":
                entry_idx = i
                break

        if entry_idx == -1 or entry_idx >= len(day_bars) - 5:
            continue

        entry_bar = day_bars.iloc[entry_idx]
        entry_spot = entry_bar["close"]
        entry_time = entry_bar["timestamp"]

        ce_price = CE_ENTRY_PREM
        pe_price = PE_ENTRY_PREM
        ce_sl = round(ce_price * (1 + SL_RATIO), 2)
        pe_sl = round(pe_price * (1 + SL_RATIO), 2)

        ce_open = True
        pe_open = True
        ce_exit_price = 0.0
        pe_exit_price = 0.0
        ce_exit_reason = ""
        pe_exit_reason = ""
        exit_time = None

        for j in range(entry_idx + 1, len(day_bars)):
            bar = day_bars.iloc[j]
            curr_spot = bar["close"]
            curr_time = bar["time"]
            spot_change = curr_spot - entry_spot
            high_change = bar["high"] - entry_spot
            low_change = entry_spot - bar["low"]

            elapsed_bars = j - entry_idx
            total_afternoon_bars = 30
            time_decay_factor = min(1.0, elapsed_bars / total_afternoon_bars)

            if ce_open:
                # Intrabar spike in spot against CE
                est_ce = max(0.5, (ce_price * (1.0 - 0.70 * time_decay_factor)) + (0.28 * max(0, high_change)))
                if est_ce >= ce_sl:
                    ce_exit_price = ce_sl + SLIPPAGE_PTS
                    ce_open = False
                    ce_exit_reason = "STOP_LOSS_25%"
                    exit_time = bar["timestamp"]

            if pe_open:
                # Intrabar spike in spot against PE
                est_pe = max(0.5, (pe_price * (1.0 - 0.70 * time_decay_factor)) + (0.28 * max(0, low_change)))
                if est_pe >= pe_sl:
                    pe_exit_price = pe_sl + SLIPPAGE_PTS
                    pe_open = False
                    pe_exit_reason = "STOP_LOSS_25%"
                    exit_time = bar["timestamp"]

            if curr_time >= "14:45":
                if ce_open:
                    ce_exit_price = max(1.0, ce_price * (1.0 - 0.70 * time_decay_factor) + (0.15 * max(0, spot_change)))
                    ce_exit_reason = "TIME_TARGET"
                    ce_open = False
                if pe_open:
                    pe_exit_price = max(1.0, pe_price * (1.0 - 0.70 * time_decay_factor) + (0.15 * max(0, -spot_change)))
                    pe_exit_reason = "TIME_TARGET"
                    pe_open = False
                exit_time = bar["timestamp"]
                break

        if exit_time is None:
            exit_time = day_bars.iloc[-1]["timestamp"]
            if ce_open:
                ce_exit_price = 2.0
                ce_exit_reason = "EOD"
            if pe_open:
                pe_exit_price = 2.0
                pe_exit_reason = "EOD"

        ce_pts = ce_price - ce_exit_price
        pe_pts = pe_price - pe_exit_price
        total_pts = ce_pts + pe_pts
        gross_pnl = total_pts * LOT_SIZE
        net_pnl = gross_pnl - ROUND_TRIP_CHARGES

        trades.append({
            "strategy": "THETA-0DTE",
            "date": str(d),
            "side": "STRANGLE",
            "entry_time": str(entry_time),
            "exit_time": str(exit_time),
            "entry_spot": entry_spot,
            "ce_pts": round(ce_pts, 2),
            "pe_pts": round(pe_pts, 2),
            "opt_pts": round(total_pts, 2),
            "gross_pnl": round(gross_pnl, 2),
            "charges": ROUND_TRIP_CHARGES,
            "net_pnl": round(net_pnl, 2),
            "exit_reason": f"CE:{ce_exit_reason}|PE:{pe_exit_reason}",
            "won": net_pnl > 0
        })

    return trades

# ==============================================================================
# Performance Metrics Analyzer
# ==============================================================================
def compute_metrics(trades, capital):
    if not trades:
        return {
            "trades": 0, "win_rate": 0.0, "net_pnl": 0.0,
            "profit_factor": 0.0, "max_dd": 0.0, "max_dd_pct": 0.0,
            "roi_pct": 0.0, "avg_trade": 0.0
        }

    df_t = pd.DataFrame(trades)
    total_trades = len(df_t)
    wins = df_t[df_t["net_pnl"] > 0]
    losses = df_t[df_t["net_pnl"] <= 0]

    win_rate = (len(wins) / total_trades) * 100.0
    total_net = df_t["net_pnl"].sum()
    gross_wins = wins["net_pnl"].sum() if len(wins) > 0 else 0.0
    gross_losses = abs(losses["net_pnl"].sum()) if len(losses) > 0 else 0.0

    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf")

    df_t["cum_pnl"] = df_t["net_pnl"].cumsum()
    df_t["equity"] = capital + df_t["cum_pnl"]
    df_t["peak"] = df_t["equity"].cummax()
    df_t["drawdown"] = df_t["peak"] - df_t["equity"]
    df_t["drawdown_pct"] = (df_t["drawdown"] / df_t["peak"]) * 100.0

    max_dd = df_t["drawdown"].max()
    max_dd_pct = df_t["drawdown_pct"].max()
    roi_pct = (total_net / capital) * 100.0
    avg_trade = total_net / total_trades

    return {
        "trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_gross": round(df_t["gross_pnl"].sum(), 2),
        "total_charges": round(df_t["charges"].sum(), 2),
        "net_pnl": round(total_net, 2),
        "profit_factor": round(profit_factor, 2),
        "max_dd": round(max_dd, 2),
        "max_dd_pct": round(max_dd_pct, 1),
        "roi_pct": round(roi_pct, 1),
        "avg_trade": round(avg_trade, 2)
    }

def main():
    print("=" * 85)
    print(" INSTITUTIONAL MULTI-STRATEGY 6-MONTH HISTORICAL BACKTEST ENGINE ")
    print(f" Dataset: 180-Day NIFTY 5-minute Candles | Lots: {LOTS} ({LOT_SIZE} Qty) ")
    print("=" * 85)

    df = load_data()
    days = sorted(df["date"].unique())
    print(f"Total Trading Sessions Analyzed: {len(days)} days (from {days[0]} to {days[-1]})\n")

    print("⚡ Running ORION-15 Backtest...")
    orion_trades = backtest_orion15(df, days)
    orion_m = compute_metrics(orion_trades, CAPITAL_PER_STRATEGY)

    print("⚡ Running CPR-Institutional Backtest...")
    cpr_trades = backtest_cpr(df, days)
    cpr_m = compute_metrics(cpr_trades, CAPITAL_PER_STRATEGY)

    print("⚡ Running ICT-Liquidity Backtest...")
    ict_trades = backtest_ict(df, days)
    ict_m = compute_metrics(ict_trades, CAPITAL_PER_STRATEGY)

    print("⚡ Running THETA-0DTE Backtest...")
    theta_trades = backtest_theta(df, days)
    theta_m = compute_metrics(theta_trades, CAPITAL_PER_STRATEGY)

    all_trades = orion_trades + cpr_trades + ict_trades + theta_trades
    all_trades = sorted(all_trades, key=lambda x: x["entry_time"])
    port_m = compute_metrics(all_trades, CAPITAL_PER_STRATEGY * 4)

    print("\n" + "=" * 105)
    print(f"{'Strategy':<20} | {'Trades':<7} | {'Win %':<6} | {'Net P&L (₹)':<14} | {'PF':<5} | {'Max DD (₹)':<12} | {'Max DD%':<8} | {'ROI %':<8}")
    print("-" * 105)

    for name, m in [
        ("ORION-15", orion_m),
        ("CPR-Institutional", cpr_m),
        ("ICT-Liquidity", ict_m),
        ("THETA-0DTE", theta_m),
    ]:
        print(f"{name:<20} | {m['trades']:<7} | {m['win_rate']:<5}% | ₹{m['net_pnl']:>12,.2f} | {m['profit_factor']:<5} | ₹{m['max_dd']:>10,.2f} | {m['max_dd_pct']:<7}% | {m['roi_pct']:>7}%")

    print("-" * 105)
    print(f"{'COMBINED PORTFOLIO':<20} | {port_m['trades']:<7} | {port_m['win_rate']:<5}% | ₹{port_m['net_pnl']:>12,.2f} | {port_m['profit_factor']:<5} | ₹{port_m['max_dd']:>10,.2f} | {port_m['max_dd_pct']:<7}% | {port_m['roi_pct']:>7}%")
    print("=" * 105)

    results = {
        "orion_15": {"metrics": orion_m, "trades": orion_trades},
        "cpr_institutional": {"metrics": cpr_m, "trades": cpr_trades},
        "ict_liquidity": {"metrics": ict_m, "trades": ict_trades},
        "theta_0dte": {"metrics": theta_m, "trades": theta_trades},
        "combined_portfolio": {"metrics": port_m, "trades": all_trades}
    }

    class CustomEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.bool_, bool)):
                return bool(obj)
            if isinstance(obj, (np.integer, int)):
                return int(obj)
            if isinstance(obj, (np.floating, float)):
                if np.isinf(obj):
                    return "Infinity"
                return float(obj)
            return super().default(obj)

    out_file = Path("data/strategy_ledgers/multi_strategy_backtest_results.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, cls=CustomEncoder)
    print(f"\n✅ Full granular trade logs and performance curves saved to: {out_file}")

if __name__ == "__main__":
    main()
