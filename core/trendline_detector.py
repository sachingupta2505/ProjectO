"""
Algorithmic Trendline Detector Engine.
Identifies descending resistance trendlines, ascending support trendlines,
role-reversal broken lines, and converging triangle/wedge patterns from OHLCV data.
"""

import sys
from pathlib import Path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import math
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np
from core.logger import get_logger
from core.sr_detector import fetch_chart_candles

logger = get_logger("TrendlineDetector")


def detect_fractal_pivots(df: pd.DataFrame, window: int = 3) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Identifies swing high and swing low fractal pivots."""
    highs = []
    lows = []
    n = len(df)

    for i in range(window, n - window):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]
        dt = df["dt"].iloc[i]
        dt_str = dt.strftime("%Y-%m-%d")

        is_high = True
        for j in range(1, window + 1):
            if h < df["high"].iloc[i - j] or h < df["high"].iloc[i + j]:
                is_high = False
                break
        if is_high:
            highs.append({"idx": i, "date": dt_str, "price": float(h), "timestamp": dt})

        is_low = True
        for j in range(1, window + 1):
            if l > df["low"].iloc[i - j] or l > df["low"].iloc[i + j]:
                is_low = False
                break
        if is_low:
            lows.append({"idx": i, "date": dt_str, "price": float(l), "timestamp": dt})

    return highs, lows


def detect_trendlines(
    symbol: str = "NIFTY",
    interval_minutes: int = 1440,
    days_back: int = 180,
    window: int = 3,
    touch_tolerance_pct: float = 0.0025,
    max_breaches: int = 2
) -> Dict[str, Any]:
    """Detects macro and tactical trendlines algorithmically."""
    df = fetch_chart_candles(symbol, interval_minutes, days_back)
    if df.empty:
        return {"error": f"Failed to fetch data for {symbol}", "trendlines": []}

    n = len(df)
    curr_idx = n - 1
    spot = float(df["close"].iloc[-1])
    tolerance_pts = spot * touch_tolerance_pct

    highs, lows = detect_fractal_pivots(df, window=window)

    # 1. Evaluate Descending Resistance Trendlines
    resistance_lines = []
    for a in range(len(highs)):
        for b in range(a + 1, len(highs)):
            p1 = highs[a]
            p2 = highs[b]
            dx = p2["idx"] - p1["idx"]
            if dx < 4:
                continue

            dy = p2["price"] - p1["price"]
            slope = dy / dx

            if slope >= 0:
                continue

            touches = 0
            breaches = 0
            touch_details = []

            for k in range(p1["idx"], n):
                line_y = p1["price"] + slope * (k - p1["idx"])
                actual_h = df["high"].iloc[k]
                diff = actual_h - line_y

                if diff > tolerance_pts:
                    breaches += 1
                elif abs(diff) <= tolerance_pts:
                    touches += 1
                    touch_details.append({
                        "idx": k,
                        "date": df["dt"].iloc[k].strftime("%Y-%m-%d"),
                        "price": round(actual_h, 1),
                        "line_val": round(line_y, 1)
                    })

            if breaches <= max_breaches and touches >= 2:
                proj_today = p1["price"] + slope * (curr_idx - p1["idx"])
                proj_tomorrow = p1["price"] + slope * (curr_idx + 1 - p1["idx"])
                dist = proj_today - spot
                score = (touches * 15) - (breaches * 20)
                if 0 <= dist <= tolerance_pts * 3:
                    score += 25

                resistance_lines.append({
                    "type": "DESCENDING_RESISTANCE",
                    "origin_date": p1["date"],
                    "origin_price": round(p1["price"], 1),
                    "anchor_date": p2["date"],
                    "anchor_price": round(p2["price"], 1),
                    "slope_per_bar": round(slope, 2),
                    "current_value": round(proj_today, 1),
                    "next_bar_value": round(proj_tomorrow, 1),
                    "touches": touches,
                    "breaches": breaches,
                    "distance_from_spot": round(dist, 1),
                    "distance_pct": round((dist / spot) * 100, 2),
                    "status": "ACTIVE_OVERHEAD" if dist >= 0 else "BROKEN_ABOVE",
                    "score": score,
                    "touch_details": touch_details
                })

    # 2. Evaluate Ascending Support Trendlines
    support_lines = []
    for a in range(len(lows)):
        for b in range(a + 1, len(lows)):
            p1 = lows[a]
            p2 = lows[b]
            dx = p2["idx"] - p1["idx"]
            if dx < 4:
                continue

            dy = p2["price"] - p1["price"]
            slope = dy / dx

            if slope <= 0:
                continue

            touches = 0
            breaches = 0
            touch_details = []

            for k in range(p1["idx"], n):
                line_y = p1["price"] + slope * (k - p1["idx"])
                actual_l = df["low"].iloc[k]
                diff = line_y - actual_l

                if diff > tolerance_pts:
                    breaches += 1
                elif abs(diff) <= tolerance_pts:
                    touches += 1
                    touch_details.append({
                        "idx": k,
                        "date": df["dt"].iloc[k].strftime("%Y-%m-%d"),
                        "price": round(actual_l, 1),
                        "line_val": round(line_y, 1)
                    })

            if touches >= 2:
                proj_today = p1["price"] + slope * (curr_idx - p1["idx"])
                proj_tomorrow = p1["price"] + slope * (curr_idx + 1 - p1["idx"])
                dist = proj_today - spot
                status = "ACTIVE_SUPPORT" if dist <= 0 and breaches <= max_breaches else "BROKEN_DOWN"
                score = (touches * 15) - (breaches * 10)

                support_lines.append({
                    "type": "ASCENDING_SUPPORT",
                    "origin_date": p1["date"],
                    "origin_price": round(p1["price"], 1),
                    "anchor_date": p2["date"],
                    "anchor_price": round(p2["price"], 1),
                    "slope_per_bar": round(slope, 2),
                    "current_value": round(proj_today, 1),
                    "next_bar_value": round(proj_tomorrow, 1),
                    "touches": touches,
                    "breaches": breaches,
                    "distance_from_spot": round(abs(dist), 1),
                    "distance_pct": round((abs(dist) / spot) * 100, 2),
                    "status": status,
                    "score": score,
                    "touch_details": touch_details
                })

    resistance_lines.sort(key=lambda x: x["score"], reverse=True)
    support_lines.sort(key=lambda x: x["score"], reverse=True)

    def dedupe(lines):
        result = []
        for l in lines:
            if not any(abs(l["current_value"] - r["current_value"]) < 30 for r in result):
                result.append(l)
        return result

    best_resistances = dedupe(resistance_lines)[:5]
    best_supports = dedupe(support_lines)[:5]

    convergence = None
    if best_resistances and best_supports:
        top_res = best_resistances[0]
        top_sup = best_supports[0]
        m1 = top_res["slope_per_bar"]
        y1 = top_res["current_value"]
        m2 = top_sup["slope_per_bar"]
        y2 = top_sup["current_value"]

        if abs(m1 - m2) > 0.001:
            bars_to_apex = (y2 - y1) / (m1 - m2)
            apex_price = y1 + m1 * bars_to_apex
            apex_date = (df["dt"].iloc[-1] + timedelta(days=int(bars_to_apex))).strftime("%Y-%m-%d")
            convergence = {
                "pattern": "CONVERGING_TRIANGLE_WEDGE",
                "bars_to_apex": round(bars_to_apex, 1),
                "apex_target_date": apex_date,
                "apex_price": round(apex_price, 1),
                "resistance_trendline": f"{top_res['origin_date']} @ {top_res['origin_price']} (Slope: {top_res['slope_per_bar']} pts/bar)",
                "support_trendline": f"{top_sup['origin_date']} @ {top_sup['origin_price']} (Slope: {top_sup['slope_per_bar']} pts/bar)"
            }

    return {
        "symbol": symbol.upper(),
        "spot": spot,
        "timeframe": "Daily (1D)" if interval_minutes == 1440 else f"{interval_minutes}m",
        "total_candles": n,
        "resistances": best_resistances,
        "supports": best_supports,
        "convergence": convergence
    }


if __name__ == "__main__":
    res = detect_trendlines("NIFTY", interval_minutes=1440, days_back=180)
    print(f"=== TRENDLINE ANALYSIS FOR {res['symbol']} ({res['timeframe']}) ===")
    print(f"Spot: ₹{res['spot']:,.2f}\n")

    print("🔴 TOP RESISTANCE TRENDLINES:")
    for r in res["resistances"]:
        print(f"  • Origin: {r['origin_date']} (₹{r['origin_price']}) -> Anchor: {r['anchor_date']} (₹{r['anchor_price']})")
        print(f"    Slope: {r['slope_per_bar']} pts/day | Current: ₹{r['current_value']} (Distance: +{r['distance_from_spot']} pts)")
        print(f"    Touches: {r['touches']} | Breaches: {r['breaches']} | Status: {r['status']}\n")

    print("🟢 TOP SUPPORT TRENDLINES:")
    for s in res["supports"]:
        print(f"  • Origin: {s['origin_date']} (₹{s['origin_price']}) -> Anchor: {s['anchor_date']} (₹{s['anchor_price']})")
        print(f"    Slope: {s['slope_per_bar']} pts/day | Current: ₹{s['current_value']} (Distance: {s['distance_from_spot']} pts)")
        print(f"    Touches: {s['touches']} | Breaches: {s['breaches']} | Status: {s['status']}\n")

    if res.get("convergence"):
        c = res["convergence"]
        print(f"📐 CONVERGENCE / APEX PATTERN:")
        print(f"  • Pattern: {c['pattern']}")
        print(f"  • Apex Date: {c['apex_target_date']} | Apex Price: ₹{c['apex_price']}")
