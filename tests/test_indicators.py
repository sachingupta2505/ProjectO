"""Unit tests for Technical Indicators (RSI, MACD, VWAP) & TechnicalConfluenceEngine."""

import pytest
from core.indicators import (
    calculate_rsi,
    calculate_macd,
    calculate_vwap,
    TechnicalConfluenceEngine
)


def test_rsi_calculation():
    # Constant price -> RSI = 50.0
    flat_prices = [100.0] * 20
    rsi, slope = calculate_rsi(flat_prices, period=14)
    assert rsi == 50.0

    # Strictly rising prices -> RSI = 100.0
    rising_prices = [100.0 + i * 2.0 for i in range(25)]
    rsi_up, slope_up = calculate_rsi(rising_prices, period=14)
    assert rsi_up > 80.0
    assert slope_up >= 0.0

    # Strictly falling prices -> RSI < 20.0
    falling_prices = [200.0 - i * 3.0 for i in range(25)]
    rsi_down, slope_down = calculate_rsi(falling_prices, period=14)
    assert rsi_down < 20.0
    assert slope_down <= 0.0


def test_macd_calculation():
    # Test flat prices
    flat = [100.0] * 40
    macd_res = calculate_macd(flat)
    assert macd_res["macd"] == 0.0
    assert macd_res["signal"] == 0.0
    assert macd_res["hist"] == 0.0

    # Test trending prices (rising)
    rising = [100.0 + (i ** 1.2) for i in range(40)]
    macd_up = calculate_macd(rising)
    assert macd_up["macd"] > 0.0
    assert macd_up["signal"] > 0.0


def test_vwap_calculation():
    bars = [
        {"high": 105.0, "low": 95.0, "close": 100.0, "volume": 1000},  # TP = 100, PV = 100,000
        {"high": 115.0, "low": 105.0, "close": 110.0, "volume": 1000}, # TP = 110, PV = 110,000
    ]
    # Total PV = 210,000 / Total Vol = 2,000 -> VWAP = 105.0
    vwap = calculate_vwap(bars)
    assert vwap == 105.0


def test_confluence_engine_scoring():
    engine = TechnicalConfluenceEngine(history_len=50)

    # 1. Feed strong Bullish bars: rising price with high volume
    prices = [23800.0 + i * 5.0 for i in range(25)]
    for p in prices:
        engine.update_bar({
            "symbol": "NIFTY",
            "open": p - 2.0,
            "high": p + 3.0,
            "low": p - 3.0,
            "close": p,
            "volume": 15000
        })

    # Test Bullish Breakout Confluence
    approved, score, reason, details = engine.evaluate_confluence(
        direction="BULLISH",
        setup_type="BREAKOUT",
        current_price=prices[-1],
        asset_key="NIFTY"
    )
    assert approved is True
    assert score >= 0.55
    assert "PASS" in reason

    # Test Bearish Breakdown on this uptrend -> MUST FAIL
    approved_bear, score_bear, reason_bear, _ = engine.evaluate_confluence(
        direction="BEARISH",
        setup_type="BREAKDOWN",
        current_price=prices[-1],
        asset_key="NIFTY"
    )
    assert approved_bear is False
    assert score_bear < 0.55
    assert "REJECTED" in reason_bear
