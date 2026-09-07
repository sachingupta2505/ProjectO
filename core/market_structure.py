"""
Market Structure Engine
Identifies intraday market structure (BULLISH, BEARISH, SIDEWAYS) using
20-period Exponential Moving Average (EMA-20), Swing High/Low sequence,
and Volume confirmation. Enforces strict trend alignment for S/R trades.
"""

from collections import deque
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from core.logger import get_logger

logger = get_logger("MarketStructure")


class StructureRegime(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    SIDEWAYS = "SIDEWAYS"


class MarketStructureEngine:
    def __init__(self, ema_period: int = 20, history_len: int = 50):
        self.ema_period = ema_period
        self.history_len = history_len

        # Bar histories per asset ("NIFTY", "CRUDEOIL")
        self.bars: Dict[str, deque] = {
            "NIFTY": deque(maxlen=history_len),
            "CRUDEOIL": deque(maxlen=history_len)
        }

        # Cached indicator states
        self.ema_values: Dict[str, float] = {}
        self.prev_ema_values: Dict[str, float] = {}
        self.regimes: Dict[str, StructureRegime] = {
            "NIFTY": StructureRegime.SIDEWAYS,
            "CRUDEOIL": StructureRegime.SIDEWAYS
        }
        self.swing_highs: Dict[str, List[float]] = {"NIFTY": [], "CRUDEOIL": []}
        self.swing_lows: Dict[str, List[float]] = {"NIFTY": [], "CRUDEOIL": []}

    def update_bar(self, bar: Dict[str, Any]) -> StructureRegime:
        """
        Ingest a completed 1-minute candle bar and update market structure.
        Bar dictionary must contain: "symbol", "close", "high", "low", "volume".
        """
        raw_sym = bar.get("symbol", "NIFTY").upper()
        asset_key = "CRUDEOIL" if "CRUDE" in raw_sym else "NIFTY"

        close = float(bar.get("close", 0.0))
        high = float(bar.get("high", close))
        low = float(bar.get("low", close))
        vol = float(bar.get("volume", 0.0))

        if close <= 0:
            return self.regimes[asset_key]

        history = self.bars[asset_key]
        history.append({
            "close": close,
            "high": high,
            "low": low,
            "volume": vol,
            "timestamp": bar.get("timestamp")
        })

        # 1. Update Exponential Moving Average (EMA-20)
        multiplier = 2.0 / (self.ema_period + 1)
        if asset_key not in self.ema_values:
            # Seed with simple moving average once we have enough bars, or initial close
            closes = [b["close"] for b in history]
            self.ema_values[asset_key] = sum(closes) / len(closes)
            self.prev_ema_values[asset_key] = self.ema_values[asset_key]
        else:
            self.prev_ema_values[asset_key] = self.ema_values[asset_key]
            current_ema = (close - self.ema_values[asset_key]) * multiplier + self.ema_values[asset_key]
            self.ema_values[asset_key] = round(current_ema, 2)

        ema = self.ema_values[asset_key]
        prev_ema = self.prev_ema_values[asset_key]
        ema_slope = ema - prev_ema

        # 2. Detect local Swing Highs & Swing Lows (over 3-bar window)
        if len(history) >= 5:
            recent_highs = [b["high"] for b in list(history)[-5:]]
            recent_lows = [b["low"] for b in list(history)[-5:]]
            mid_idx = 2  # middle of 5 bars
            if recent_highs[mid_idx] == max(recent_highs):
                self.swing_highs[asset_key].append(recent_highs[mid_idx])
                if len(self.swing_highs[asset_key]) > 5:
                    self.swing_highs[asset_key].pop(0)
            if recent_lows[mid_idx] == min(recent_lows):
                self.swing_lows[asset_key].append(recent_lows[mid_idx])
                if len(self.swing_lows[asset_key]) > 5:
                    self.swing_lows[asset_key].pop(0)

        # 3. Determine Structure Regime
        # Check swing sequence if available
        sw_highs = self.swing_highs[asset_key]
        sw_lows = self.swing_lows[asset_key]
        higher_highs = len(sw_highs) >= 2 and sw_highs[-1] > sw_highs[-2]
        higher_lows = len(sw_lows) >= 2 and sw_lows[-1] > sw_lows[-2]
        lower_highs = len(sw_highs) >= 2 and sw_highs[-1] < sw_highs[-2]
        lower_lows = len(sw_lows) >= 2 and sw_lows[-1] < sw_lows[-2]

        buffer = 3.0 if asset_key == "NIFTY" else 10.0

        if (close > ema + buffer and ema_slope >= -0.10) or (higher_highs and higher_lows and close >= ema):
            regime = StructureRegime.BULLISH
        elif (close < ema - buffer and ema_slope <= 0.10) or (lower_highs and lower_lows and close <= ema):
            regime = StructureRegime.BEARISH
        else:
            regime = StructureRegime.SIDEWAYS

        prev_regime = self.regimes[asset_key]
        self.regimes[asset_key] = regime

        if regime != prev_regime:
            logger.info(f"🏛️ [MARKET STRUCTURE - {asset_key}] Regime Shift: {prev_regime.value} ➔ {regime.value} | Price: ₹{close:.2f} | EMA-20: ₹{ema:.2f} (Slope: {ema_slope:+.2f})")

        return regime

    def validate_setup_alignment(self, setup_direction: str, asset_key: str = "NIFTY") -> Tuple[bool, str]:
        """
        Enforce strict market structure alignment.
        - setup_direction: "BULLISH" (Calls / Long) or "BEARISH" (Puts / Short).
        Returns (is_allowed, reason).
        """
        regime = self.regimes.get(asset_key, StructureRegime.SIDEWAYS)
        ema = self.ema_values.get(asset_key, 0.0)

        if setup_direction == "BULLISH":
            if regime == StructureRegime.BEARISH:
                return False, f"Market Structure is BEARISH (Price below EMA-20 ₹{ema:.1f}). Suppressing counter-trend Call/Long."
            return True, f"Structure is {regime.value} - Bullish alignment confirmed."

        elif setup_direction == "BEARISH":
            if regime == StructureRegime.BULLISH:
                return False, f"Market Structure is BULLISH (Price above EMA-20 ₹{ema:.1f}). Suppressing counter-trend Put/Short."
            return True, f"Structure is {regime.value} - Bearish alignment confirmed."

        return True, "Structure is Neutral/Sideways."

    def get_market_structure(self, asset_key: str = "NIFTY") -> Dict[str, Any]:
        """Get summary snapshot of market structure."""
        return {
            "asset": asset_key,
            "regime": self.regimes.get(asset_key, StructureRegime.SIDEWAYS).value,
            "ema_20": self.ema_values.get(asset_key, 0.0),
            "prev_ema_20": self.prev_ema_values.get(asset_key, 0.0),
            "ema_slope": round(self.ema_values.get(asset_key, 0.0) - self.prev_ema_values.get(asset_key, 0.0), 2),
            "bars_count": len(self.bars.get(asset_key, []))
        }
