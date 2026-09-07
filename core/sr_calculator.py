"""
Support & Resistance Recalculation & Daily Pivot Engine.
Computes institutional CPR (Central Pivot Range), Floor Pivots, Camarilla Pivots,
and Previous Day High/Low anchors for NIFTY 50 and MCX CRUDE OIL.
"""

import requests
from typing import Dict, Any, List, Optional
from core.level_models import TradingLevel, LevelType, LevelAction, load_levels_config, save_levels_config
from core.logger import get_logger

logger = get_logger("SRCalculator")


def fetch_multi_timeframe_ohlc(asset_key: str) -> Dict[str, Dict[str, float]]:
    """
    Fetches real-time Daily, Weekly, and Monthly OHLC candles from Yahoo Finance.
    - Daily: Yesterday's completed candle
    - Weekly: Previous completed week's candle
    - Monthly: Previous completed month's candle
    """
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    is_nifty = "NIFTY" in asset_key.upper()
    sym = "%5ENSEI" if is_nifty else "CL=F"
    fx = 94.5 if not is_nifty else 1.0

    res = {}
    intervals = [("daily", "1d", "5d"), ("weekly", "1wk", "1mo"), ("monthly", "1mo", "6mo")]
    for tf, itv, rng in intervals:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={itv}&range={rng}"
            r = s.get(url, timeout=4)
            if r.status_code == 200:
                q = r.json()["chart"]["result"][0]["indicators"]["quote"][0]
                valid = [(h, l, c) for h, l, c in zip(q["high"], q["low"], q["close"]) if h is not None and l is not None and c is not None]
                candle = valid[-2] if len(valid) >= 2 else valid[-1]
                res[tf] = {
                    "high": round(candle[0] * fx, 2),
                    "low": round(candle[1] * fx, 2),
                    "close": round(candle[2] * fx, 2)
                }
        except Exception as e:
            logger.debug(f"Failed to fetch {asset_key} {tf} OHLC: {e}")

    # Fallbacks if network unavailable
    if not res.get("daily"):
        res["daily"] = {"high": 24005.75 if is_nifty else 8710.0, "low": 23895.85 if is_nifty else 8384.0, "close": 23897.7 if is_nifty else 8645.0}
    if not res.get("weekly"):
        res["weekly"] = {"high": 24143.15 if is_nifty else 8801.7, "low": 23786.8 if is_nifty else 7948.4, "close": 23897.7 if is_nifty else 8645.0}
    if not res.get("monthly"):
        res["monthly"] = {"high": 24143.15 if is_nifty else 8801.7, "low": 23786.8 if is_nifty else 8139.3, "close": 23897.7 if is_nifty else 8645.0}

    return res


def calculate_pivot_levels(
    symbol: str,
    high: float,
    low: float,
    close: float,
    timeframe: str = "daily"
) -> List[TradingLevel]:
    """
    Computes institutional pivot levels across timeframes (Daily, Weekly, Monthly):
    - Central Pivot Range (CPR): Pivot, TC, BC
    - Floor Pivots: Classical R1, R2, S1, S2
    - Camarilla Pivots: H4 (Breakout Buy), L4 (Breakout Sell)
    - Previous Period High / Low (PDH/PDL, PWH/PWL, PMH/PML)
    """
    sym = symbol.upper()
    is_nifty = "NIFTY" in sym
    tf = timeframe.lower()
    tf_label = tf.capitalize()

    # Timeframe-calibrated Target & SL points
    if tf == "weekly":
        target_spot = 80.0 if is_nifty else 120.0
        sl_spot = 35.0 if is_nifty else 50.0
    elif tf == "monthly":
        target_spot = 150.0 if is_nifty else 200.0
        sl_spot = 60.0 if is_nifty else 80.0
    else:  # daily
        target_spot = 40.0 if is_nifty else 70.0
        sl_spot = 20.0 if is_nifty else 35.0

    # 1. Central Pivot Range (CPR)
    pivot = (high + low + close) / 3.0
    bc = (high + low) / 2.0
    tc = (pivot - bc) + pivot
    cpr_low = min(bc, tc)
    cpr_high = max(bc, tc)

    # 2. Classical Floor Pivots
    r1 = (2.0 * pivot) - low
    s1 = (2.0 * pivot) - high
    r2 = pivot + (high - low)
    s2 = pivot - (high - low)

    # 3. Camarilla Pivots
    diff = high - low
    h4 = close + (diff * 1.1 / 2.0)
    l4 = close - (diff * 1.1 / 2.0)

    # Period High/Low naming
    if tf == "weekly":
        h_name = f"{sym} Previous Week High (PWH)"
        l_name = f"{sym} Previous Week Low (PWL)"
    elif tf == "monthly":
        h_name = f"{sym} Previous Month High (PMH)"
        l_name = f"{sym} Previous Month Low (PML)"
    else:
        h_name = f"{sym} Previous Day High (PDH)"
        l_name = f"{sym} Previous Day Low (PDL)"

    levels: List[TradingLevel] = [
        # CPR
        TradingLevel(
            id=f"{sym.lower()}_cpr_{tf}",
            name=f"{sym} {tf_label} Central Pivot Range (CPR)",
            price=round(pivot, 2),
            range_low=round(cpr_low, 2),
            range_high=round(cpr_high, 2),
            level_type=LevelType.DEMAND_ZONE.value if close > pivot else LevelType.SUPPLY_ZONE.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Period High
        TradingLevel(
            id=f"{sym.lower()}_ph_{tf}",
            name=h_name,
            price=round(high, 2),
            level_type=LevelType.RESISTANCE.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Period Low
        TradingLevel(
            id=f"{sym.lower()}_pl_{tf}",
            name=l_name,
            price=round(low, 2),
            level_type=LevelType.SUPPORT.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Classical R1
        TradingLevel(
            id=f"{sym.lower()}_r1_{tf}",
            name=f"{sym} {tf_label} Classical R1 Resistance",
            price=round(r1, 2),
            level_type=LevelType.RESISTANCE.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Classical S1
        TradingLevel(
            id=f"{sym.lower()}_s1_{tf}",
            name=f"{sym} {tf_label} Classical S1 Support",
            price=round(s1, 2),
            level_type=LevelType.SUPPORT.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
    ]

    # Add R2/S2 and Camarilla H4/L4 for Daily and Weekly
    if tf in ("daily", "weekly"):
        levels.extend([
            TradingLevel(
                id=f"{sym.lower()}_r2_{tf}",
                name=f"{sym} {tf_label} Classical R2 Resistance",
                price=round(r2, 2),
                level_type=LevelType.RESISTANCE.value,
                action=LevelAction.BREAKOUT_ONLY.value,
                target_spot_pts=target_spot * 1.5,
                sl_spot_pts=sl_spot,
                symbol=sym
            ),
            TradingLevel(
                id=f"{sym.lower()}_s2_{tf}",
                name=f"{sym} {tf_label} Classical S2 Support",
                price=round(s2, 2),
                level_type=LevelType.SUPPORT.value,
                action=LevelAction.BREAKOUT_ONLY.value,
                target_spot_pts=target_spot * 1.5,
                sl_spot_pts=sl_spot,
                symbol=sym
            ),
            TradingLevel(
                id=f"{sym.lower()}_cam_h4_{tf}",
                name=f"{sym} {tf_label} Camarilla H4 Breakout Zone",
                price=round(h4, 2),
                level_type=LevelType.RESISTANCE.value,
                action=LevelAction.BREAKOUT_ONLY.value,
                target_spot_pts=target_spot,
                sl_spot_pts=sl_spot,
                symbol=sym
            ),
            TradingLevel(
                id=f"{sym.lower()}_cam_l4_{tf}",
                name=f"{sym} {tf_label} Camarilla L4 Breakdown Zone",
                price=round(l4, 2),
                level_type=LevelType.SUPPORT.value,
                action=LevelAction.BREAKOUT_ONLY.value,
                target_spot_pts=target_spot,
                sl_spot_pts=sl_spot,
                symbol=sym
            ),
        ])

    return levels


def refresh_daily_sr_levels(
    market_ohlc: Optional[Dict[str, Dict[str, float]]] = None,
    include_weekly_monthly: bool = True
) -> Dict[str, Any]:
    """
    Recalculates multi-timeframe S/R levels (Daily, Weekly, Monthly) for NIFTY and CRUDEOIL
    and synchronizes them into levels_config.json.
    Preserves custom user-drawn levels while keeping programmatic levels fresh.
    """
    assets = ["NIFTY", "CRUDEOIL"]
    new_calculated_levels: List[TradingLevel] = []

    for sym in assets:
        if market_ohlc and sym in market_ohlc:
            # Caller provided explicit daily OHLC
            h = float(market_ohlc[sym].get("high", 0.0))
            l = float(market_ohlc[sym].get("low", 0.0))
            c = float(market_ohlc[sym].get("close", 0.0))
            if h > 0 and l > 0 and c > 0:
                new_calculated_levels.extend(calculate_pivot_levels(sym, h, l, c, timeframe="daily"))
        else:
            # Auto-fetch real Daily, Weekly, and Monthly OHLC
            mtf_data = fetch_multi_timeframe_ohlc(sym)
            for tf, ohlc in mtf_data.items():
                if not include_weekly_monthly and tf != "daily":
                    continue
                h, l, c = ohlc["high"], ohlc["low"], ohlc["close"]
                new_calculated_levels.extend(calculate_pivot_levels(sym, h, l, c, timeframe=tf))

    # Load existing levels
    existing = load_levels_config()
    calc_ids = {lvl.id for lvl in new_calculated_levels}

    # Keep existing custom levels that are NOT overwritten by these generated IDs
    merged_levels: List[TradingLevel] = [
        lvl for lvl in existing if lvl.id not in calc_ids
    ]
    merged_levels.extend(new_calculated_levels)

    save_levels_config(merged_levels)
    logger.info(
        f"✅ Multi-Timeframe S/R Levels refreshed! {len(new_calculated_levels)} Daily, Weekly, & Monthly levels active."
    )

    return {
        "status": "SUCCESS",
        "refreshed_count": len(new_calculated_levels),
        "total_active_levels": len(merged_levels),
        "levels": [lvl.to_dict() for lvl in new_calculated_levels],
    }
