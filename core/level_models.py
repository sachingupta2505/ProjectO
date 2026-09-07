"""
Data structures, Enums, and Persistence for Support & Resistance Level Trading.
"""

import json
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict

CONFIG_FILE = Path(__file__).resolve().parent.parent / "data" / "levels_config.json"


class LevelType(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"
    DEMAND_ZONE = "DEMAND_ZONE"
    SUPPLY_ZONE = "SUPPLY_ZONE"


class LevelAction(str, Enum):
    BOUNCE_ONLY = "BOUNCE_ONLY"
    BREAKOUT_ONLY = "BREAKOUT_ONLY"
    BOTH = "BOTH"


@dataclass
class TradingLevel:
    id: str
    name: str
    price: float
    level_type: str = LevelType.RESISTANCE.value
    action: str = LevelAction.BOTH.value
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    target_pct: float = 0.10       # 10% option target by default (asymmetric 2:1 RR)
    sl_pct: float = 0.05           # 5% option stop loss by default
    target_spot_pts: float = 40.0  # Spot points target
    sl_spot_pts: float = 20.0      # Spot points stop loss
    trail_sl_pct: float = 0.025    # 2.5% trailing stop distance for options
    breakeven_pct: float = 0.04    # Move SL to breakeven once +4.0% is reached
    trail_sl_pts: float = 15.0     # 15 pts trailing stop distance for Crude Oil
    breakeven_pts: float = 20.0    # 20 pts to trigger breakeven for Crude Oil
    is_active: bool = True
    volume_multiplier: float = 1.3 # Volume > 1.3x 20-SMA for breakout confirmation
    symbol: str = "NIFTY"          # "NIFTY" or "CRUDEOIL"

    def is_in_zone(self, spot: float, buffer: float = 5.0) -> bool:
        """Check if price is touching or inside this level/zone."""
        if self.range_low is not None and self.range_high is not None:
            return (self.range_low - buffer) <= spot <= (self.range_high + buffer)
        return abs(spot - self.price) <= buffer

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingLevel":
        clean_data = dict(data)
        if "symbol" not in clean_data:
            clean_data["symbol"] = "NIFTY"
        return cls(**clean_data)


# Default levels accurately extracted from the user's TradingView Daily Chart
DEFAULT_LEVELS: List[TradingLevel] = [
    TradingLevel(
        id="lvl_24528",
        name="Major Peak Resistance",
        price=24528.20,
        level_type=LevelType.RESISTANCE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_24385",
        name="Upper Swing Resistance",
        price=24385.25,
        level_type=LevelType.RESISTANCE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_24358",
        name="Pivot High Resistance",
        price=24358.70,
        level_type=LevelType.RESISTANCE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_24286",
        name="Horizontal Resistance",
        price=24286.35,
        level_type=LevelType.RESISTANCE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_24175",
        name="Key Resistance / Trendline Convergence",
        price=24175.50,
        level_type=LevelType.RESISTANCE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_24110",
        name="EMA 15 Overhead Resistance",
        price=24110.80,
        level_type=LevelType.RESISTANCE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_supply_zone",
        name="Upper Supply Zone (24000 - 24050)",
        price=24025.00,
        range_low=24000.00,
        range_high=24050.00,
        level_type=LevelType.SUPPLY_ZONE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_23975",
        name="EMA 5 Dynamic Resistance",
        price=23975.00,
        level_type=LevelType.RESISTANCE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_23966",
        name="Immediate Pivot Level",
        price=23966.50,
        level_type=LevelType.RESISTANCE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_demand_zone",
        name="Lower Demand Zone (23800 - 23850)",
        price=23825.00,
        range_low=23800.00,
        range_high=23850.00,
        level_type=LevelType.DEMAND_ZONE.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
    TradingLevel(
        id="lvl_23625",
        name="Lower Key Support (23600 - 23650)",
        price=23625.00,
        range_low=23600.00,
        range_high=23650.00,
        level_type=LevelType.SUPPORT.value,
        action=LevelAction.BOTH.value,
        target_pct=0.05,
        sl_pct=0.025
    ),
]

DEFAULT_CRUDE_LEVELS: List[TradingLevel] = [
    TradingLevel(id="crude_res_8802", name="Crude Overhead Resistance", price=8801.7, level_type=LevelType.RESISTANCE.value, target_spot_pts=50.0, sl_spot_pts=25.0, symbol="CRUDEOIL"),
    TradingLevel(id="crude_res_8716", name="Crude Swing High Supply", price=8715.7, level_type=LevelType.RESISTANCE.value, target_spot_pts=40.0, sl_spot_pts=20.0, symbol="CRUDEOIL"),
    TradingLevel(id="crude_res_8642", name="Crude Immediate Resistance", price=8641.7, level_type=LevelType.RESISTANCE.value, target_spot_pts=35.0, sl_spot_pts=20.0, symbol="CRUDEOIL"),
    TradingLevel(id="crude_res_8592", name="Crude Pivot High Barrier", price=8591.5, level_type=LevelType.RESISTANCE.value, target_spot_pts=30.0, sl_spot_pts=15.0, symbol="CRUDEOIL"),
    TradingLevel(id="crude_sup_8516", name="Crude Intraday Support Floor", price=8516.3, level_type=LevelType.SUPPORT.value, target_spot_pts=30.0, sl_spot_pts=15.0, symbol="CRUDEOIL"),
    TradingLevel(id="crude_sup_8464", name="Crude Swing Low Support", price=8464.4, level_type=LevelType.SUPPORT.value, target_spot_pts=35.0, sl_spot_pts=20.0, symbol="CRUDEOIL"),
    TradingLevel(id="crude_sup_8268", name="Crude Major Demand Zone", price=8267.8, level_type=LevelType.DEMAND_ZONE.value, range_low=8250.0, range_high=8285.0, target_spot_pts=50.0, sl_spot_pts=25.0, symbol="CRUDEOIL"),
]


def load_levels_config(symbol: Optional[str] = None) -> List[TradingLevel]:
    """Load levels configuration from disk or initialize with defaults."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    all_levels: List[TradingLevel] = []

    if CONFIG_FILE.exists():
        try:
            content = CONFIG_FILE.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, list) and len(data) > 0:
                all_levels = [TradingLevel.from_dict(d) for d in data]
        except Exception:
            all_levels = []

    if not all_levels:
        all_levels = DEFAULT_LEVELS + DEFAULT_CRUDE_LEVELS
        save_levels_config(all_levels)
    else:
        # Check if CRUDEOIL levels are missing; if so, append them
        has_crude = any(lvl.symbol.upper() == "CRUDEOIL" for lvl in all_levels)
        if not has_crude:
            all_levels.extend(DEFAULT_CRUDE_LEVELS)
            save_levels_config(all_levels)

    if symbol:
        return [lvl for lvl in all_levels if lvl.symbol.upper() == symbol.upper()]
    return all_levels


def save_levels_config(levels: List[TradingLevel]):
    """Persist levels configuration to disk."""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [lvl.to_dict() for lvl in levels]
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Failed to save levels config: {e}")
