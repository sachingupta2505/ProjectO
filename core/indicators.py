"""
Technical Indicators & Multi-Signal Confluence Engine.
Computes Relative Strength Index (RSI), MACD (12, 26, 9), and Intraday VWAP.
Enforces institutional multi-indicator quality gates to filter out false breakouts and fake bounces.
"""

import math
from collections import deque
from typing import Dict, List, Optional, Tuple, Any
from core.logger import get_logger

logger = get_logger("Indicators")


def calculate_rsi(closes: List[float], period: int = 14) -> Tuple[float, float]:
    """
    Computes Wilder's Smoothed Relative Strength Index (RSI).
    Returns (current_rsi, rsi_slope).
    """
    if len(closes) < period + 1:
        return 50.0, 0.0

    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))

    # Initial average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    prev_rsi = 50.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0 and avg_gain == 0:
            rsi = 50.0
        elif avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        if i == len(gains) - 2:
            prev_rsi = rsi

    if avg_loss == 0 and avg_gain == 0:
        current_rsi = 50.0
    elif avg_loss == 0:
        current_rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        current_rsi = 100.0 - (100.0 / (1.0 + rs))

    rsi_slope = round(current_rsi - prev_rsi, 2)
    return round(current_rsi, 2), rsi_slope


def calculate_macd(
    closes: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> Dict[str, float]:
    """
    Computes Moving Average Convergence Divergence (MACD).
    Returns {"macd": float, "signal": float, "hist": float, "prev_hist": float}.
    """
    if len(closes) < slow_period + signal_period:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0, "prev_hist": 0.0}

    def calc_ema(values: List[float], period: int) -> List[float]:
        k = 2.0 / (period + 1)
        ema_series = [values[0]]
        for val in values[1:]:
            ema_series.append(val * k + ema_series[-1] * (1.0 - k))
        return ema_series

    fast_ema = calc_ema(closes, fast_period)
    slow_ema = calc_ema(closes, slow_period)

    # MACD Line = Fast EMA - Slow EMA
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]

    # Signal Line = 9-period EMA of MACD Line
    signal_line = calc_ema(macd_line[slow_period - 1:], signal_period)

    curr_macd = macd_line[-1]
    curr_signal = signal_line[-1]
    curr_hist = curr_macd - curr_signal

    prev_macd = macd_line[-2] if len(macd_line) >= 2 else curr_macd
    prev_signal = signal_line[-2] if len(signal_line) >= 2 else curr_signal
    prev_hist = prev_macd - prev_signal

    return {
        "macd": round(curr_macd, 2),
        "signal": round(curr_signal, 2),
        "hist": round(curr_hist, 2),
        "prev_hist": round(prev_hist, 2)
    }


def calculate_vwap(bars: List[Dict[str, Any]]) -> float:
    """
    Computes cumulative Volume Weighted Average Price (VWAP) across bars.
    Typical Price = (High + Low + Close) / 3
    """
    if not bars:
        return 0.0

    cum_pv = 0.0
    cum_vol = 0.0
    for b in bars:
        h = float(b.get("high", b.get("close", 0.0)))
        l = float(b.get("low", b.get("close", 0.0)))
        c = float(b.get("close", 0.0))
        v = float(b.get("volume", 1.0))
        if v <= 0:
            v = 1.0
        typical_price = (h + l + c) / 3.0
        cum_pv += typical_price * v
        cum_vol += v

    return round(cum_pv / cum_vol, 2) if cum_vol > 0 else 0.0


class TechnicalConfluenceEngine:
    """
    Evaluates multi-signal technical confirmation (RSI, MACD, VWAP)
    before permitting execution on any S/R level setup.
    """

    def __init__(self, history_len: int = 100):
        self.history_len = history_len
        self.bars: Dict[str, deque] = {
            "NIFTY": deque(maxlen=history_len),
            "CRUDEOIL": deque(maxlen=history_len)
        }

    def update_bar(self, bar: Dict[str, Any]) -> None:
        raw_sym = bar.get("symbol", "NIFTY").upper()
        asset_key = "CRUDEOIL" if "CRUDE" in raw_sym else "NIFTY"
        self.bars[asset_key].append(bar)

    def get_indicators(self, asset_key: str = "NIFTY") -> Dict[str, Any]:
        """Returns snapshot of current technical indicators."""
        bars_list = list(self.bars.get(asset_key, []))
        closes = [float(b["close"]) for b in bars_list if "close" in b]

        rsi, rsi_slope = calculate_rsi(closes)
        macd_data = calculate_macd(closes)
        vwap = calculate_vwap(bars_list)

        return {
            "rsi": rsi,
            "rsi_slope": rsi_slope,
            "macd": macd_data["macd"],
            "macd_signal": macd_data["signal"],
            "macd_hist": macd_data["hist"],
            "macd_prev_hist": macd_data["prev_hist"],
            "vwap": vwap,
            "sample_size": len(closes)
        }

    def evaluate_confluence(
        self,
        direction: str,            # "BULLISH" or "BEARISH"
        setup_type: str,           # "BREAKOUT" or "BOUNCE"
        current_price: float,
        asset_key: str = "NIFTY",
        min_confluence_score: float = 0.55
    ) -> Tuple[bool, float, str, Dict[str, Any]]:
        """
        Calculates confluence score [0.0 - 1.0].
        Returns (is_approved, score, reason, details).
        """
        ind = self.get_indicators(asset_key)
        rsi = ind["rsi"]
        rsi_slope = ind["rsi_slope"]
        hist = ind["macd_hist"]
        prev_hist = ind["macd_prev_hist"]
        macd = ind["macd"]
        signal = ind["macd_signal"]
        vwap = ind["vwap"]
        sample_size = ind["sample_size"]

        # If insufficient bars, allow baseline execution with neutral score
        if sample_size < 15:
            return True, 0.70, "Insufficient indicator warmup bars (<15); baseline permitted", ind

        score = 0.0
        notes = []

        if direction.upper() == "BULLISH":
            # 1. RSI Scoring
            if setup_type == "BREAKOUT":
                if 50.0 <= rsi <= 72.0:
                    score += 0.35
                    notes.append(f"RSI bullish expansion ({rsi:.1f})")
                elif 45.0 <= rsi < 50.0 and rsi_slope > 0:
                    score += 0.20
                    notes.append(f"RSI crossing 50 ({rsi:.1f})")
                elif rsi > 75.0:
                    score += 0.0  # Overbought exhaustion penalty
                    notes.append(f"⚠️ RSI overbought exhaustion ({rsi:.1f} > 75)")
                else:
                    notes.append(f"RSI weak ({rsi:.1f})")
            else:  # BOUNCE
                if 32.0 <= rsi <= 60.0 and rsi_slope >= 0:
                    score += 0.35
                    notes.append(f"RSI turning up from support ({rsi:.1f}, slope: {rsi_slope:+.1f})")
                elif rsi < 30.0:
                    score += 0.05  # Oversold knife
                    notes.append(f"⚠️ RSI oversold freefall ({rsi:.1f})")
                else:
                    score += 0.15

            # 2. MACD Scoring
            if macd >= signal:
                score += 0.25
                notes.append("MACD line >= Signal")
            if hist >= 0 or hist > prev_hist:
                score += 0.15
                notes.append(f"MACD Hist rising/positive ({hist:+.2f})")
            else:
                notes.append(f"⚠️ MACD Hist negative expansion ({hist:+.2f})")

            # 3. VWAP Scoring
            if vwap > 0:
                vwap_buffer = 8.0 if asset_key == "NIFTY" else 15.0
                if current_price >= vwap:
                    score += 0.25
                    notes.append(f"Price above VWAP (₹{current_price:.1f} >= ₹{vwap:.1f})")
                elif current_price >= (vwap - vwap_buffer) and setup_type == "BOUNCE":
                    score += 0.15
                    notes.append(f"Price near VWAP (within {vwap_buffer}pts)")
                else:
                    notes.append(f"⚠️ Price underwater below VWAP (₹{current_price:.1f} < ₹{vwap:.1f})")
            else:
                score += 0.20  # Neutral if no VWAP

        else:  # BEARISH
            # 1. RSI Scoring
            if setup_type == "BREAKDOWN":
                if 28.0 <= rsi <= 50.0:
                    score += 0.35
                    notes.append(f"RSI bearish expansion ({rsi:.1f})")
                elif 50.0 < rsi <= 55.0 and rsi_slope < 0:
                    score += 0.20
                    notes.append(f"RSI breaking below 50 ({rsi:.1f})")
                elif rsi < 25.0:
                    score += 0.0  # Oversold bounce risk
                    notes.append(f"⚠️ RSI oversold exhaustion ({rsi:.1f} < 25)")
                else:
                    notes.append(f"RSI buoyant ({rsi:.1f})")
            else:  # REJECTION
                if 40.0 <= rsi <= 68.0 and rsi_slope <= 0:
                    score += 0.35
                    notes.append(f"RSI turning down from resistance ({rsi:.1f}, slope: {rsi_slope:+.1f})")
                elif rsi > 70.0:
                    score += 0.05
                    notes.append(f"⚠️ RSI overbought spike ({rsi:.1f})")
                else:
                    score += 0.15

            # 2. MACD Scoring
            if macd <= signal:
                score += 0.25
                notes.append("MACD line <= Signal")
            if hist <= 0 or hist < prev_hist:
                score += 0.15
                notes.append(f"MACD Hist falling/negative ({hist:+.2f})")
            else:
                notes.append(f"⚠️ MACD Hist positive expansion ({hist:+.2f})")

            # 3. VWAP Scoring
            if vwap > 0:
                vwap_buffer = 8.0 if asset_key == "NIFTY" else 15.0
                if current_price <= vwap:
                    score += 0.25
                    notes.append(f"Price below VWAP (₹{current_price:.1f} <= ₹{vwap:.1f})")
                elif current_price <= (vwap + vwap_buffer) and setup_type == "BOUNCE":
                    score += 0.15
                    notes.append(f"Price near VWAP (within {vwap_buffer}pts)")
                else:
                    notes.append(f"⚠️ Price above VWAP (₹{current_price:.1f} > ₹{vwap:.1f})")
            else:
                score += 0.20  # Neutral if no VWAP

        score = round(min(1.0, max(0.0, score)), 2)
        approved = (score >= min_confluence_score)
        reason_text = f"Confluence Score: {score:.2f}/{min_confluence_score:.2f} ({'PASS' if approved else 'REJECTED'}) | " + "; ".join(notes)

        return approved, score, reason_text, ind
