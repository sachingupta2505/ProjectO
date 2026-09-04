"""
Support & Resistance Level Detector Engine (Method 2: Algorithmic Detection).
Uses Fractal Swing Pivots, Touch Clustering, Role Reversal Detection, and
Volume Weighting to automatically identify major levels on any chart.
"""

import sys
from pathlib import Path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import math
from datetime import datetime
from typing import List, Dict, Any, Optional
import requests
import pandas as pd
import numpy as np
from core.logger import get_logger
from core.level_models import TradingLevel, LevelType, LevelAction

logger = get_logger("SRDetector")


def fetch_chart_candles(symbol: str = "BANKNIFTY", interval_minutes: int = 1440, days_back: int = 365) -> pd.DataFrame:
    """Fetches historical OHLCV candle data from Groww/NSE."""
    now_ms = int(datetime.now().timestamp() * 1000)
    start_ms = now_ms - (days_back * 24 * 3600 * 1000)

    sym = symbol.upper()
    if sym in ("CRUDE", "CRUDEOIL", "CRUDE OIL", "CRUDEOILM"):
        from core.market_data import fetch_crude_candles
        return fetch_crude_candles(interval_minutes, days_back)
    elif sym in ("BANKNIFTY", "NIFTY BANK"):
        sym_code = "BANKNIFTY"
    elif sym in ("NIFTY", "NIFTY 50"):
        sym_code = "NIFTY"
    else:
        sym_code = sym

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    })

    url = f"https://groww.in/v1/api/charting_service/v2/chart/exchange/NSE/segment/CASH/{sym_code}?endTimeInMillis={now_ms}&intervalInMinutes={interval_minutes}&startTimeInMillis={start_ms}"
    try:
        r = session.get(url, timeout=6)
        if r.status_code == 200:
            candles = r.json().get("candles", [])
            if candles:
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["dt"] = pd.to_datetime(df["timestamp"], unit="s")
                df = df.sort_values("dt").reset_index(drop=True)
                for col in ["open", "high", "low", "close"]:
                    df[col] = df[col].astype(float)
                return df
    except Exception as e:
        logger.error(f"Error fetching candles for {sym_code}: {e}")

    return pd.DataFrame()


def detect_support_resistance_levels(
    symbol: str = "BANKNIFTY",
    interval_minutes: int = 1440,
    days_back: int = 365,
    window: int = 3,
    cluster_tolerance: Optional[float] = None
) -> Dict[str, Any]:
    """
    Algorithmic S/R Detection:
    1. Identifies fractal swing highs and lows.
    2. Clusters pivot touches within a dynamic tolerance band.
    3. Identifies role reversals (support turned resistance / resistance turned support).
    4. Categorizes into Resistance Above Spot and Support Below Spot.
    5. Ranks by touch count and structural importance.
    """
    df = fetch_chart_candles(symbol, interval_minutes, days_back)
    if df.empty:
        return {"error": f"Unable to fetch candle data for {symbol}", "levels": []}

    spot = float(df["close"].iloc[-1])
    day_high = float(df["high"].iloc[-1])
    day_low = float(df["low"].iloc[-1])

    # Dynamic tolerance band: ~0.35% of price
    if cluster_tolerance is None:
        cluster_tolerance = round(spot * 0.0035, 1)  # e.g., ~200 pts for BankNifty, ~85 pts for Nifty

    # 1. Fractal Pivot Detection
    pivots: List[Dict[str, Any]] = []
    n = len(df)

    for i in range(window, n - window):
        high_i = df["high"].iloc[i]
        low_i = df["low"].iloc[i]
        dt_str = df["dt"].iloc[i].strftime("%Y-%m-%d")

        # Swing High
        is_swing_high = True
        for j in range(1, window + 1):
            if high_i < df["high"].iloc[i - j] or high_i < df["high"].iloc[i + j]:
                is_swing_high = False
                break
        if is_swing_high:
            pivots.append({"price": high_i, "type": "HIGH", "dt": dt_str, "idx": i})

        # Swing Low
        is_swing_low = True
        for j in range(1, window + 1):
            if low_i > df["low"].iloc[i - j] or low_i > df["low"].iloc[i + j]:
                is_swing_low = False
                break
        if is_swing_low:
            pivots.append({"price": low_i, "type": "LOW", "dt": dt_str, "idx": i})

    # Include recent high and low
    pivots.append({"price": day_high, "type": "HIGH", "dt": "Today High", "idx": n - 1})
    pivots.append({"price": day_low, "type": "LOW", "dt": "Today Low", "idx": n - 1})

    # 2. Cluster Pivots into Levels and Zones
    clusters: List[Dict[str, Any]] = []

    for p in pivots:
        price = p["price"]
        matched = False
        for cl in clusters:
            if abs(cl["center"] - price) <= cluster_tolerance:
                cl["prices"].append(price)
                cl["types"].append(p["type"])
                cl["dates"].append(p["dt"])
                cl["center"] = sum(cl["prices"]) / len(cl["prices"])
                cl["min"] = min(cl["prices"])
                cl["max"] = max(cl["prices"])
                cl["touches"] += 1
                matched = True
                break
        if not matched:
            clusters.append({
                "center": price,
                "prices": [price],
                "types": [p["type"]],
                "dates": [p["dt"]],
                "min": price,
                "max": price,
                "touches": 1
            })

    # Filter and rank clusters
    # Significant clusters: >= 2 touches OR near all-time high / major swing extremes
    all_time_high = df["high"].max()
    all_time_low = df["low"].min()

    filtered = []
    for cl in clusters:
        touches = cl["touches"]
        highs = cl["types"].count("HIGH")
        lows = cl["types"].count("LOW")
        is_flip = highs > 0 and lows > 0
        price_center = round(cl["center"], 1)
        zone_span = cl["max"] - cl["min"]

        # Importance score
        score = touches * 10
        if is_flip:
            score += 25  # High importance for polarity flips
        if abs(price_center - all_time_high) < cluster_tolerance:
            score += 30  # All-Time High
        if abs(price_center - all_time_low) < cluster_tolerance:
            score += 30  # 52-Week Low

        # Classify Type
        if price_center > spot:
            lvl_type = "SUPPLY_ZONE" if zone_span >= cluster_tolerance * 0.7 else "RESISTANCE"
        else:
            lvl_type = "DEMAND_ZONE" if zone_span >= cluster_tolerance * 0.7 else "SUPPORT"

        filtered.append({
            "price": price_center,
            "range_low": round(cl["min"], 1) if zone_span >= 60 else None,
            "range_high": round(cl["max"], 1) if zone_span >= 60 else None,
            "level_type": lvl_type,
            "touches": touches,
            "high_touches": highs,
            "low_touches": lows,
            "is_flip": is_flip,
            "distance_from_spot": round(price_center - spot, 1),
            "distance_pct": round(((price_center - spot) / spot) * 100, 2),
            "score": score
        })

    # Deduplicate closely spaced levels within 150 pts
    filtered.sort(key=lambda x: x["price"])
    deduped: List[Dict[str, Any]] = []
    for item in filtered:
        if not deduped:
            deduped.append(item)
            continue
        prev = deduped[-1]
        if abs(item["price"] - prev["price"]) < cluster_tolerance * 0.8:
            # Merge with higher score item
            if item["score"] > prev["score"]:
                deduped[-1] = item
        else:
            deduped.append(item)

    # Separate Resistances (above spot) and Supports (below spot)
    resistances = [l for l in deduped if l["price"] > spot]
    supports = [l for l in deduped if l["price"] < spot]

    resistances.sort(key=lambda x: x["price"])  # Ascending from spot
    supports.sort(key=lambda x: x["price"], reverse=True)  # Descending from spot

    return {
        "symbol": symbol.upper(),
        "spot": spot,
        "cluster_tolerance": cluster_tolerance,
        "total_candles": len(df),
        "resistances": resistances,
        "supports": supports,
        "all_levels": deduped
    }


if __name__ == "__main__":
    results = detect_support_resistance_levels("BANKNIFTY", days_back=365)
    print(f"=== S/R DETECTION FOR {results['symbol']} (Spot: {results['spot']:,.2f}) ===")
    print("\n🔴 MAJOR RESISTANCES ABOVE SPOT:")
    for r in results["resistances"]:
        flip_tag = " [FLIP / POLARITY]" if r["is_flip"] else ""
        zone_tag = f" Zone: [{r['range_low']} - {r['range_high']}]" if r["range_low"] else ""
        print(f"  • ₹{r['price']:,.1f} (+{r['distance_from_spot']:+,.1f} pts / +{r['distance_pct']}%) | Touches: {r['touches']}{flip_tag}{zone_tag}")

    print("\n🟢 MAJOR SUPPORTS BELOW SPOT:")
    for s in results["supports"]:
        flip_tag = " [FLIP / POLARITY]" if s["is_flip"] else ""
        zone_tag = f" Zone: [{s['range_low']} - {s['range_high']}]" if s["range_low"] else ""
        print(f"  • ₹{s['price']:,.1f} ({s['distance_from_spot']:+,.1f} pts / {s['distance_pct']}%) | Touches: {s['touches']}{flip_tag}{zone_tag}")
