"""
6-Month Strict Backtest Engine: 15-Minute Retest Edge on NIFTY 50.
Simulates realistic 1-lot option execution (65 Qty @ ~₹100 premium).
Includes:
- Exact 50% retest entry timing (09:30 - 11:30 AM)
- Defined Stop Loss at First Candle Invalidation
- 1:2+ Asymmetric Target with Breakeven Trailing
- Realistic Option Delta (~0.50), Slippage (0.5 pt), and Indian Exchange Charges & STT.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import pandas as pd
import numpy as np
from pathlib import Path

# Load historical 5m candles
csv_path = Path("data/historical/NIFTY_5m_180d.csv")
df = pd.read_csv(csv_path)
df["timestamp"] = pd.to_datetime(df["timestamp"])
df["date"] = df["timestamp"].dt.date
df["time"] = df["timestamp"].dt.strftime("%H:%M")

days = sorted(df["date"].unique())
print(f"Total Trading Days in 6-Month Dataset: {len(days)}")

LOT_SIZE = 65
BASE_PREMIUM = 100.0  # ₹100 average ATM option price
DELTA = 0.50          # ATM option delta
SLIPPAGE_OPT = 0.50   # 0.5 pt slippage per order
BROKERAGE_TAX = 55.0  # ₹55 round-trip brokerage + STT + turnover + GST

trades = []

for d in days:
    day_bars = df[df["date"] == d].sort_values("timestamp").reset_index(drop=True)
    if len(day_bars) < 15:
        continue  # skip holiday or incomplete sessions

    # First 15 minutes = first 3 5-min bars (09:15, 09:20, 09:25)
    first_3 = day_bars.iloc[:3]
    o15 = first_3.iloc[0]["open"]
    c15 = first_3.iloc[2]["close"]
    h15 = first_3["high"].max()
    l15 = first_3["low"].min()
    range15 = h15 - l15
    body15 = abs(c15 - o15)
    is_green = c15 >= o15

    # Filter 1: Minimum Body Conviction (>= 30 points body)
    if body15 < 30.0 or range15 <= 0:
        continue  # Skip choppy / indecision doji days

    # Filter 2: Rejection Wick Guard
    upper_wick = h15 - max(o15, c15)
    lower_wick = min(o15, c15) - l15
    if is_green and upper_wick > (body15 * 0.85):
        continue  # Rejected at top
    if not is_green and lower_wick > (body15 * 0.85):
        continue  # Rejected at bottom

    # Define Retest Zone (40% to 65% retracement of the first 15m candle)
    retest_mid = o15 + (c15 - o15) * 0.50
    retest_min = min(o15, retest_mid)
    retest_max = max(o15, retest_mid)

    # Invalidation & Target (Spot Points)
    if is_green:
        # Bullish Call Setup
        invalidation_spot = l15 - 5.0
        target1_spot = h15
        target2_spot = h15 + (body15 * 0.80)
    else:
        # Bearish Put Setup
        invalidation_spot = h15 + 5.0
        target1_spot = l15
        target2_spot = l15 - (body15 * 0.80)

    # Scan rest of morning bars (09:30 AM to 11:30 AM) for retest entry
    in_trade = False
    entry_bar_idx = -1
    entry_spot = 0.0
    side = "CALL" if is_green else "PUT"

    for i in range(3, min(len(day_bars), 27)):  # bars up to 11:30 AM
        bar = day_bars.iloc[i]
        b_low = bar["low"]
        b_high = bar["high"]
        b_close = bar["close"]
        b_open = bar["open"]

        if is_green:
            # Did price pull back into the retest zone and show a bullish bounce?
            if b_low <= (retest_max + 5.0) and b_high >= retest_min:
                if b_close >= b_open:  # Green bounce candle on retest!
                    entry_spot = b_close
                    in_trade = True
                    entry_bar_idx = i
                    break
        else:
            # Did price rally into the retest zone and show a bearish rejection?
            if b_high >= (retest_min - 5.0) and b_low <= retest_max:
                if b_close <= b_open:  # Red rejection candle on retest!
                    entry_spot = b_close
                    in_trade = True
                    entry_bar_idx = i
                    break

    if not in_trade:
        continue  # Clean discipline: no retest, no trade!

    # Execute and manage the trade
    entry_time = day_bars.iloc[entry_bar_idx]["timestamp"]
    sl_spot_dist = abs(entry_spot - invalidation_spot)
    target1_dist = abs(target1_spot - entry_spot)
    target2_dist = abs(target2_spot - entry_spot)

    # Convert to Option Points
    opt_entry = BASE_PREMIUM + SLIPPAGE_OPT
    opt_sl_pts = max(10.0, min(22.0, sl_spot_dist * DELTA))
    opt_sl_price = opt_entry - opt_sl_pts

    opt_t1_pts = target1_dist * DELTA
    opt_t2_pts = target2_dist * DELTA
    opt_t2_price = opt_entry + opt_t2_pts

    breakeven_active = False
    exit_opt_price = 0.0
    exit_reason = ""
    exit_time = None

    # Track trade forward bar-by-bar
    for j in range(entry_bar_idx + 1, len(day_bars)):
        curr_bar = day_bars.iloc[j]
        c_high = curr_bar["high"]
        c_low = curr_bar["low"]
        c_close = curr_bar["close"]
        curr_time = curr_bar["time"]

        # Calculate current spot move
        spot_move = (c_high - entry_spot) if is_green else (entry_spot - c_low)
        spot_adverse = (entry_spot - c_low) if is_green else (c_high - entry_spot)

        # Check Breakeven ratchet (when price reaches Target 1)
        if spot_move >= target1_dist and not breakeven_active:
            breakeven_active = True
            opt_sl_price = opt_entry + 1.0  # Lock in cost + 1 pt

        # Check Stop Loss Hit
        if (entry_spot - spot_adverse) <= (entry_spot - (opt_sl_pts / DELTA)):
            exit_opt_price = opt_sl_price - SLIPPAGE_OPT
            exit_reason = "BREAKEVEN_EXIT" if breakeven_active else "STOP_LOSS"
            exit_time = curr_bar["timestamp"]
            break

        # Check Target 2 Hit (Full Take Profit)
        if spot_move >= target2_dist:
            exit_opt_price = opt_t2_price - SLIPPAGE_OPT
            exit_reason = "FULL_TARGET"
            exit_time = curr_bar["timestamp"]
            break

        # End of day square-off at 15:15
        if curr_time >= "15:15":
            exit_spot_move = (c_close - entry_spot) if is_green else (entry_spot - c_close)
            exit_opt_price = opt_entry + (exit_spot_move * DELTA) - SLIPPAGE_OPT
            exit_reason = "EOD_SQUARE_OFF"
            exit_time = curr_bar["timestamp"]
            break

    if exit_time is None:
        exit_opt_price = opt_entry - SLIPPAGE_OPT
        exit_reason = "EOD_CLOSE"
        exit_time = day_bars.iloc[-1]["timestamp"]

    # Calculate PnL
    opt_pts_gain = exit_opt_price - opt_entry
    gross_pnl = opt_pts_gain * LOT_SIZE
    net_pnl = gross_pnl - BROKERAGE_TAX

    trades.append({
        "date": d,
        "side": side,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry_spot": entry_spot,
        "exit_reason": exit_reason,
        "opt_entry": opt_entry,
        "opt_exit": exit_opt_price,
        "opt_pts": round(opt_pts_gain, 2),
        "gross_pnl": round(gross_pnl, 2),
        "charges": BROKERAGE_TAX,
        "net_pnl": round(net_pnl, 2),
        "month": d.strftime("%Y-%m")
    })

tdf = pd.DataFrame(trades)

# Performance Metrics
total_trades = len(tdf)
wins = tdf[tdf["net_pnl"] > 0]
losses = tdf[tdf["net_pnl"] <= 0]
win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0

total_gross = tdf["gross_pnl"].sum()
total_charges = tdf["charges"].sum()
total_net = tdf["net_pnl"].sum()

avg_win = wins["net_pnl"].mean() if len(wins) > 0 else 0
avg_loss = losses["net_pnl"].mean() if len(losses) > 0 else 0
profit_factor = abs(wins["net_pnl"].sum() / losses["net_pnl"].sum()) if len(losses) > 0 and losses["net_pnl"].sum() != 0 else 999

# Equity curve & Max Drawdown
tdf["cum_pnl"] = tdf["net_pnl"].cumsum()
tdf["peak"] = tdf["cum_pnl"].cummax()
tdf["drawdown"] = tdf["cum_pnl"] - tdf["peak"]
max_dd = tdf["drawdown"].min()

print("\n" + "=" * 60)
print("🏆 6-MONTH STRICT BACKTEST REPORT: 15-MINUTE RETEST EDGE")
print("=" * 60)
print(f"Dataset Period:       {days[0]} to {days[-1]} (~6 Months)")
print(f"Contract / Lot:       NIFTY 50 Options (1 Lot = 65 Qty)")
print(f"Average Premium:      ₹{BASE_PREMIUM:.2f} (~₹6,500 capital deployed per trade)")
print(f"Total Trading Days:   {len(days)}")
print(f"Trades Taken:         {total_trades} (Selective A+ Retest Setups only)")
print(f"Winning Trades:       {len(wins)}")
print(f"Losing Trades:        {len(losses)}")
print(f"Win Rate:             {win_rate:.1f}%")
print(f"Profit Factor:        {profit_factor:.2f}")
print(f"Average Win:          +₹{avg_win:,.2f}")
print(f"Average Loss:         -₹{abs(avg_loss):,.2f}")
print(f"Risk-to-Reward Ratio: 1:{abs(avg_win / avg_loss):.2f}" if avg_loss != 0 else "N/A")
print("-" * 60)
print(f"Gross P&L:            ₹{total_gross:+,.2f}")
print(f"Total Charges/Taxes:  -₹{total_charges:,.2f}")
print(f"NET PROFIT (INR):     ₹{total_net:+,.2f}")
print(f"Max Drawdown:         ₹{max_dd:,.2f} ({abs(max_dd)/6500*100:.1f}% of trade margin)")
print("=" * 60)

print("\n📅 MONTH-BY-MONTH BREAKDOWN:")
monthly = tdf.groupby("month").agg(
    Trades=("net_pnl", "count"),
    Wins=("net_pnl", lambda x: (x > 0).sum()),
    Net_PnL=("net_pnl", "sum")
)
monthly["Win_Rate"] = (monthly["Wins"] / monthly["Trades"] * 100).round(1).astype(str) + "%"
monthly["Net_PnL"] = monthly["Net_PnL"].apply(lambda x: f"₹{x:+,.2f}")
print(monthly[["Trades", "Wins", "Win_Rate", "Net_PnL"]].to_string())

print("\n🎯 EXIT REASON DISTRIBUTION:")
print(tdf["exit_reason"].value_counts().to_string())
