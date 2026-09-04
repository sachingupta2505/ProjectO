"""
Support & Resistance Recalculation & Daily Pivot Engine.
Computes institutional CPR (Central Pivot Range), Floor Pivots, Camarilla Pivots,
and Previous Day High/Low anchors for NIFTY 50 and MCX CRUDE OIL.
"""

from typing import Dict, Any, List, Optional
from core.level_models import TradingLevel, LevelType, LevelAction, load_levels_config, save_levels_config
from core.logger import get_logger

logger = get_logger("SRCalculator")


def calculate_pivot_levels(
    symbol: str,
    high: float,
    low: float,
    close: float,
    prefix: str = "daily"
) -> List[TradingLevel]:
    """
    Computes mathematical daily pivot levels for a given symbol.
    - Central Pivot Range (CPR): Pivot, Top Central (TC), Bottom Central (BC)
    - Floor Pivots: R1, R2, S1, S2
    - Camarilla Pivots: H4 (Breakout Buy), H3 (Reversal Sell), L3 (Reversal Buy), L4 (Breakout Sell)
    - Previous Day High / Low (PDH, PDL)
    """
    sym = symbol.upper()
    is_nifty = "NIFTY" in sym
    target_spot = 40.0 if is_nifty else 40.0
    sl_spot = 20.0 if is_nifty else 20.0

    # 1. Central Pivot Range (CPR)
    pivot = (high + low + close) / 3.0
    bc = (high + low) / 2.0
    tc = (pivot - bc) + pivot
    cpr_low = min(bc, tc)
    cpr_high = max(bc, tc)

    # 2. Floor Pivots
    r1 = (2.0 * pivot) - low
    s1 = (2.0 * pivot) - high
    r2 = pivot + (high - low)
    s2 = pivot - (high - low)

    # 3. Camarilla Pivots
    diff = high - low
    h4 = close + (diff * 1.1 / 2.0)
    h3 = close + (diff * 1.1 / 4.0)
    l3 = close - (diff * 1.1 / 4.0)
    l4 = close - (diff * 1.1 / 2.0)

    levels: List[TradingLevel] = [
        # Daily Central Pivot Range (CPR)
        TradingLevel(
            id=f"{sym.lower()}_cpr_{prefix}",
            name=f"{sym} Daily Central Pivot Range (CPR)",
            price=round(pivot, 2),
            range_low=round(cpr_low, 2),
            range_high=round(cpr_high, 2),
            level_type=LevelType.DEMAND_ZONE.value if close > pivot else LevelType.SUPPLY_ZONE.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Previous Day High
        TradingLevel(
            id=f"{sym.lower()}_pdh_{prefix}",
            name=f"{sym} Previous Day High (PDH)",
            price=round(high, 2),
            level_type=LevelType.RESISTANCE.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Previous Day Low
        TradingLevel(
            id=f"{sym.lower()}_pdl_{prefix}",
            name=f"{sym} Previous Day Low (PDL)",
            price=round(low, 2),
            level_type=LevelType.SUPPORT.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Classical R1 Resistance
        TradingLevel(
            id=f"{sym.lower()}_r1_{prefix}",
            name=f"{sym} Classical R1 Resistance",
            price=round(r1, 2),
            level_type=LevelType.RESISTANCE.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Classical S1 Support
        TradingLevel(
            id=f"{sym.lower()}_s1_{prefix}",
            name=f"{sym} Classical S1 Support",
            price=round(s1, 2),
            level_type=LevelType.SUPPORT.value,
            action=LevelAction.BOTH.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Classical R2 Major Barrier
        TradingLevel(
            id=f"{sym.lower()}_r2_{prefix}",
            name=f"{sym} Classical R2 Resistance",
            price=round(r2, 2),
            level_type=LevelType.RESISTANCE.value,
            action=LevelAction.BREAKOUT_ONLY.value,
            target_spot_pts=target_spot * 1.5,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Classical S2 Major Floor
        TradingLevel(
            id=f"{sym.lower()}_s2_{prefix}",
            name=f"{sym} Classical S2 Support",
            price=round(s2, 2),
            level_type=LevelType.SUPPORT.value,
            action=LevelAction.BREAKOUT_ONLY.value,
            target_spot_pts=target_spot * 1.5,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Camarilla H4 Breakout Buy Level
        TradingLevel(
            id=f"{sym.lower()}_cam_h4_{prefix}",
            name=f"{sym} Camarilla H4 Breakout Zone",
            price=round(h4, 2),
            level_type=LevelType.RESISTANCE.value,
            action=LevelAction.BREAKOUT_ONLY.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
        # Camarilla L4 Breakdown Sell Level
        TradingLevel(
            id=f"{sym.lower()}_cam_l4_{prefix}",
            name=f"{sym} Camarilla L4 Breakdown Zone",
            price=round(l4, 2),
            level_type=LevelType.SUPPORT.value,
            action=LevelAction.BREAKOUT_ONLY.value,
            target_spot_pts=target_spot,
            sl_spot_pts=sl_spot,
            symbol=sym
        ),
    ]

    return levels


def refresh_daily_sr_levels(
    market_ohlc: Optional[Dict[str, Dict[str, float]]] = None
) -> Dict[str, Any]:
    """
    Recalculates daily S/R levels for NIFTY and CRUDEOIL and syncs them to levels_config.json.
    Preserves custom user levels while updating calculated daily anchors.
    """
    # Default representative recent OHLC if not passed from live broker/ticker
    default_ohlc = {
        "NIFTY": {"high": 24200.0, "low": 23850.0, "close": 24050.0},
        "CRUDEOIL": {"high": 8720.0, "low": 8490.0, "close": 8610.0},
    }

    ohlc_data = market_ohlc or default_ohlc
    new_calculated_levels: List[TradingLevel] = []

    for sym, ohlc in ohlc_data.items():
        h = float(ohlc.get("high", 0.0))
        l = float(ohlc.get("low", 0.0))
        c = float(ohlc.get("close", 0.0))
        if h > 0 and l > 0 and c > 0:
            calc_lvls = calculate_pivot_levels(sym, h, l, c)
            new_calculated_levels.extend(calc_lvls)

    # Load existing levels
    existing = load_levels_config()
    calc_ids = {lvl.id for lvl in new_calculated_levels}

    # Keep existing levels that are NOT generated pivot IDs
    merged_levels: List[TradingLevel] = [
        lvl for lvl in existing if lvl.id not in calc_ids
    ]
    # Append the freshly recalculated daily levels
    merged_levels.extend(new_calculated_levels)

    save_levels_config(merged_levels)
    logger.info(
        f"✅ S/R Levels refreshed! {len(new_calculated_levels)} daily pivot/CPR levels computed and saved."
    )

    return {
        "status": "SUCCESS",
        "refreshed_count": len(new_calculated_levels),
        "total_active_levels": len(merged_levels),
        "levels": [lvl.to_dict() for lvl in new_calculated_levels],
    }
