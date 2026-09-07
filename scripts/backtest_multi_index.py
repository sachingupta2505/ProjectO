"""
Multi-Index 15-Minute Opening Retest Backtesting Engine.
Tests the Opening Retest Strategy across:
1. NIFTY 50
2. BANKNIFTY
3. FINNIFTY
4. MIDCPNIFTY
5. SENSEX

Uses official 5-minute historical candles from Angel One SmartAPI and
applies the full Black-Scholes Greeks Engine with dynamic DTE and index-specific contract specifications.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, time
import pandas as pd
import numpy as np

# Ensure project root is in path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from core.option_chain import calculate_black_scholes, get_next_weekly_expiry, get_atm_strike
from core.models import OptionType

INDEX_SPECS = {
    "NIFTY": {
        "file": "data/historical/NIFTY_5m_180d.csv",
        "lot_size": 65,
        "min_body": 30.0,
        "strike_step": 50,
        "expiry_weekday": 3,  # Thursday
        "iv": 0.135,
        "base_premium": 100.0,
        "retest_leeway": 5.0
    },
    "BANKNIFTY": {
        "file": "data/historical/BANKNIFTY_5m_180d.csv",
        "lot_size": 15,
        "min_body": 80.0,
        "strike_step": 100,
        "expiry_weekday": 2,  # Wednesday
        "iv": 0.155,
        "base_premium": 250.0,
        "retest_leeway": 15.0
    },
    "FINNIFTY": {
        "file": "data/historical/FINNIFTY_5m_180d.csv",
        "lot_size": 40,
        "min_body": 30.0,
        "strike_step": 50,
        "expiry_weekday": 1,  # Tuesday
        "iv": 0.140,
        "base_premium": 100.0,
        "retest_leeway": 5.0
    },
    "MIDCPNIFTY": {
        "file": "data/historical/MIDCPNIFTY_5m_180d.csv",
        "lot_size": 50,
        "min_body": 20.0,
        "strike_step": 25,
        "expiry_weekday": 0,  # Monday
        "iv": 0.145,
        "base_premium": 60.0,
        "retest_leeway": 3.0
    },
    "SENSEX": {
        "file": "data/historical/SENSEX_5m_180d.csv",
        "lot_size": 10,
        "min_body": 120.0,
        "strike_step": 100,
        "expiry_weekday": 4,  # Friday
        "iv": 0.135,
        "base_premium": 300.0,
        "retest_leeway": 25.0
    }
}

BROKERAGE_TAX = 55.0  # ₹55 round-trip statutory charges per lot


def backtest_index(symbol: str, config: dict) -> pd.DataFrame:
    csv_path = project_root / config["file"]
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        return pd.DataFrame()

    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    days = sorted(df["date"].unique())

    lot_size = config["lot_size"]
    min_body = config["min_body"]
    strike_step = config["strike_step"]
    expiry_weekday = config["expiry_weekday"]
    iv = config["iv"]
    leeway = config["retest_leeway"]

    trades = []

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

        # Filter 1: Conviction Body
        if body15 < min_body or range15 <= 0:
            continue

        # Filter 2: Rejection Wick Guard
        upper_wick = h15 - max(o15, c15)
        lower_wick = min(o15, c15) - l15
        if is_green and upper_wick > (body15 * 0.85):
            continue
        if not is_green and lower_wick > (body15 * 0.85):
            continue

        # Retest zone (40% - 65% retracement of 15m candle)
        retest_mid = o15 + (c15 - o15) * 0.50
        retest_min = min(o15, retest_mid)
        retest_max = max(o15, retest_mid)

        invalidation_spot = (l15 - leeway) if is_green else (h15 + leeway)
        target1_spot = h15 if is_green else l15
        target2_spot = (h15 + body15 * 0.80) if is_green else (l15 - body15 * 0.80)

        in_trade = False
        entry_bar_idx = -1
        entry_spot = 0.0

        for i in range(3, min(len(day_bars), 27)):
            bar = day_bars.iloc[i]
            if is_green:
                if bar["low"] <= (retest_max + leeway) and bar["high"] >= retest_min and bar["close"] >= bar["open"]:
                    entry_spot = bar["close"]
                    in_trade = True
                    entry_bar_idx = i
                    break
            else:
                if bar["high"] >= (retest_min - leeway) and bar["low"] <= retest_max and bar["close"] <= bar["open"]:
                    entry_spot = bar["close"]
                    in_trade = True
                    entry_bar_idx = i
                    break

        if not in_trade:
            continue

        expiry_date = get_next_weekly_expiry(d, weekday=expiry_weekday)
        entry_ts = day_bars.iloc[entry_bar_idx]["timestamp"]
        expiry_ts = datetime.combine(expiry_date, time(15, 30))
        time_to_exp_entry = max(0.0001, (expiry_ts - entry_ts).total_seconds() / (365.25 * 86400))

        atm_strike = get_atm_strike(entry_spot, strike_step)
        opt_type = OptionType.CE if is_green else OptionType.PE

        bs_entry = calculate_black_scholes(entry_spot, atm_strike, time_to_exp_entry, iv, 0.07, opt_type)
        opt_entry_price = bs_entry["price"] + 0.50

        sl_spot_dist = abs(entry_spot - invalidation_spot)
        target1_dist = abs(target1_spot - entry_spot)
        target2_dist = abs(target2_spot - entry_spot)

        be_active = False
        exit_opt_price = 0.0
        reason = ""
        exit_time = None

        for j in range(entry_bar_idx + 1, len(day_bars)):
            curr_bar = day_bars.iloc[j]
            curr_ts = curr_bar["timestamp"]
            c_high = curr_bar["high"]
            c_low = curr_bar["low"]
            c_close = curr_bar["close"]

            spot_move = (c_high - entry_spot) if is_green else (entry_spot - c_low)
            spot_adverse = (entry_spot - c_low) if is_green else (c_high - entry_spot)

            if spot_move >= target1_dist and not be_active:
                be_active = True

            t_to_exp = max(0.00001, (expiry_ts - curr_ts).total_seconds() / (365.25 * 86400))

            if spot_move >= target2_dist:
                reason = "TARGET 2"
                exit_spot = target2_spot
                bs_exit = calculate_black_scholes(exit_spot, atm_strike, t_to_exp, iv, 0.07, opt_type)
                exit_opt_price = bs_exit["price"] - 0.50
                exit_time = curr_bar["time"]
                break
            elif (not be_active and spot_adverse >= sl_spot_dist) or (be_active and ((is_green and c_low <= entry_spot) or (not is_green and c_high >= entry_spot))):
                if be_active:
                    reason = "BREAKEVEN"
                    bs_exit = calculate_black_scholes(entry_spot, atm_strike, t_to_exp, iv, 0.07, opt_type)
                    exit_opt_price = bs_exit["price"] - 0.50
                else:
                    reason = "STOP LOSS"
                    bs_exit = calculate_black_scholes(invalidation_spot, atm_strike, t_to_exp, iv, 0.07, opt_type)
                    exit_opt_price = bs_exit["price"] - 0.50
                exit_time = curr_bar["time"]
                break
            elif curr_bar["time"] >= time(15, 10):
                reason = "EOD"
                bs_exit = calculate_black_scholes(c_close, atm_strike, t_to_exp, iv, 0.07, opt_type)
                exit_opt_price = bs_exit["price"] - 0.50
                exit_time = curr_bar["time"]
                break

        net_pnl = (exit_opt_price - opt_entry_price) * lot_size - BROKERAGE_TAX
        trades.append({
            "index": symbol,
            "date": d,
            "side": "CALL" if is_green else "PUT",
            "entry_time": day_bars.iloc[entry_bar_idx]["time"].strftime("%H:%M"),
            "exit_time": exit_time.strftime("%H:%M") if exit_time else "",
            "reason": reason,
            "opt_entry": opt_entry_price,
            "opt_exit": exit_opt_price,
            "net_pnl": net_pnl
        })

    return pd.DataFrame(trades)


def run_all_indices():
    results = {}
    summary = []

    for sym, cfg in INDEX_SPECS.items():
        df_trades = backtest_index(sym, cfg)
        results[sym] = df_trades

        if len(df_trades) == 0:
            continue

        n_trades = len(df_trades)
        wins = df_trades[df_trades["net_pnl"] > 0]
        losses = df_trades[df_trades["net_pnl"] <= 0]
        win_rate = (len(wins) / n_trades) * 100.0
        tot_pnl = df_trades["net_pnl"].sum()
        avg_win = wins["net_pnl"].mean() if len(wins) > 0 else 0.0
        avg_loss = losses["net_pnl"].mean() if len(losses) > 0 else 0.0
        profit_factor = abs(wins["net_pnl"].sum() / losses["net_pnl"].sum()) if losses["net_pnl"].sum() != 0 else np.nan

        df_trades["cum_pnl"] = df_trades["net_pnl"].cumsum()
        peak = df_trades["cum_pnl"].cummax()
        dd = peak - df_trades["cum_pnl"]
        max_dd = dd.max()

        summary.append({
            "Index": sym,
            "Trades": n_trades,
            "Win Rate (%)": round(win_rate, 1),
            "Total Net P&L (₹)": round(tot_pnl, 2),
            "Avg Win (₹)": round(avg_win, 2),
            "Avg Loss (₹)": round(avg_loss, 2),
            "Profit Factor": round(profit_factor, 2),
            "Max Drawdown (₹)": round(max_dd, 2)
        })

    sum_df = pd.DataFrame(summary)
    print("\n" + "=" * 80)
    print("      MULTI-INDEX 15-MINUTE OPENING RETEST PERFORMANCE SUMMARY (6 MONTHS)")
    print("=" * 80)
    print(sum_df.to_string(index=False))
    print("=" * 80)
    print(f"PORTFOLIO COMBINED NET P&L (1 Lot each): Rs. {sum_df['Total Net P&L (₹)'].sum():,.2f}")
    print("=" * 80)


if __name__ == "__main__":
    run_all_indices()
