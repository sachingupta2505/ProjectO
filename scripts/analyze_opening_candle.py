"""
Quantitative Research: First Candle (5m, 15m, 30m, 1h) Correlation & Strategy Backtester.
Tests hypothesis:
- Does First Candle color predict Day's trend?
- Does Candle body size / wick ratio predict breakout vs fakeout?
- Retest depth distributions: How much does NIFTY retest before continuing?
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

def analyze_timeframe(csv_path: str, tf_name: str):
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date

    days = df["date"].unique()
    stats = []

    for d in days:
        day_df = df[df["date"] == d].sort_values("timestamp")
        if len(day_df) < 4:
            continue

        first_bar = day_df.iloc[0]
        rest_bars = day_df.iloc[1:]

        day_open = first_bar["open"]
        day_close = day_df.iloc[-1]["close"]
        c_open = first_bar["open"]
        c_high = first_bar["high"]
        c_low = first_bar["low"]
        c_close = first_bar["close"]
        c_range = c_high - c_low
        c_body = c_close - c_open
        abs_body = abs(c_body)
        is_green = c_close >= c_open

        rest_high = rest_bars["high"].max()
        rest_low = rest_bars["low"].min()

        broke_high = rest_high > c_high
        broke_low = rest_low < c_low

        day_dir = "GREEN" if day_close >= day_open else "RED"
        expansion_up = max(0.0, rest_high - c_high)
        expansion_down = max(0.0, c_low - rest_low)
        
        # Retest depth from first close
        pullback = (c_close - rest_low) if is_green else (rest_high - c_close)

        stats.append({
            "date": d,
            "is_green": is_green,
            "c_range": c_range,
            "abs_body": abs_body,
            "body_ratio": (abs_body / c_range) if c_range > 0 else 0,
            "day_dir": day_dir,
            "broke_high": broke_high,
            "broke_low": broke_low,
            "expansion_up": expansion_up,
            "expansion_down": expansion_down,
            "pullback": pullback,
            "c_high": c_high,
            "c_low": c_low,
            "c_close": c_close
        })

    res = pd.DataFrame(stats)
    green = res[res["is_green"]]
    red = res[~res["is_green"]]

    print(f"\n=======================================================")
    print(f"📊 {tf_name.upper()} FIRST CANDLE STATISTICAL AUDIT (Total Days: {len(res)})")
    print(f"=======================================================")

    print(f"\n🟢 FIRST {tf_name} IS GREEN ({len(green)} Days / {len(green)/len(res)*100:.1f}%):")
    print(f"  • Day Finishes GREEN (EOD Continuation):     {(green['day_dir'] == 'GREEN').mean()*100:.1f}%")
    print(f"  • Breaks First Candle High (Bullish Move):   {green['broke_high'].mean()*100:.1f}%")
    print(f"  • Fails & Breaks First Candle Low (Trap):    {green['broke_low'].mean()*100:.1f}%")
    print(f"  • Avg Subsequent Move Above High:           +{green['expansion_up'].mean():.1f} pts")
    print(f"  • Median Pullback from First Close:          {green['pullback'].median():.1f} pts")

    print(f"\n🔴 FIRST {tf_name} IS RED ({len(red)} Days / {len(red)/len(res)*100:.1f}%):")
    print(f"  • Day Finishes RED (EOD Continuation):       {(red['day_dir'] == 'RED').mean()*100:.1f}%")
    print(f"  • Breaks First Candle Low (Bearish Move):    {red['broke_low'].mean()*100:.1f}%")
    print(f"  • Fails & Breaks First Candle High (Trap):   {red['broke_high'].mean()*100:.1f}%")
    print(f"  • Avg Subsequent Move Below Low:            -{red['expansion_down'].mean():.1f} pts")
    print(f"  • Median Pullback from First Close:          {red['pullback'].median():.1f} pts")

    # Body Size Analysis (> 40 pts body)
    strong_green = green[green["abs_body"] >= 40.0]
    strong_red = red[red["abs_body"] >= 40.0]
    print(f"\n⚡ STRONG BODY CONVICTION FILTER (Body >= 40 Points):")
    if len(strong_green) > 0:
        print(f"  • Strong Green ({len(strong_green)} days) -> Breaks High: {strong_green['broke_high'].mean()*100:.1f}% | Avg Move: +{strong_green['expansion_up'].mean():.1f} pts | Trap Rate: {strong_green['broke_low'].mean()*100:.1f}%")
    if len(strong_red) > 0:
        print(f"  • Strong Red   ({len(strong_red)} days) -> Breaks Low:  {strong_red['broke_low'].mean()*100:.1f}% | Avg Move: -{strong_red['expansion_down'].mean():.1f} pts | Trap Rate: {strong_red['broke_high'].mean()*100:.1f}%")

    return res

if __name__ == "__main__":
    analyze_timeframe("data/historical/NIFTY_5m_60d.csv", "5-Minute")
    analyze_timeframe("data/historical/NIFTY_15m_90d.csv", "15-Minute")
