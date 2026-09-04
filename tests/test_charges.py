"""Unit tests for transaction charges calculation engine."""

import pytest
from core.models import OrderSide
from core.charges import calculate_order_charges, calculate_round_trip_charges


def test_nifty_option_charges():
    # 2 Lots Nifty Option = 130 Qty @ 100 Buy, 120 Sell
    buy_chg = calculate_order_charges(OrderSide.BUY, 100.0, 130, exchange="NFO", asset_class="INDEX")
    assert buy_chg["brokerage"] == 20.0
    assert buy_chg["stt_ctt"] == 0.0  # STT only on sell
    assert buy_chg["stamp_duty"] > 0.0
    assert buy_chg["total_charges"] > 20.0

    sell_chg = calculate_order_charges(OrderSide.SELL, 120.0, 130, exchange="NFO", asset_class="INDEX")
    assert sell_chg["brokerage"] == 20.0
    assert sell_chg["stt_ctt"] > 0.0  # STT charged on sell
    assert sell_chg["stamp_duty"] == 0.0

    rt = calculate_round_trip_charges(100.0, 120.0, 130, exchange="NFO", asset_class="INDEX")
    assert rt["total_brokerage"] == 40.0
    # Expected round trip on 130 qty @ 100/120 is between Rs.60 and Rs.75
    assert 60.0 <= rt["total_charges"] <= 75.0


def test_crude_oil_charges():
    # 4 Lots Crude Mini = 40 bbl @ 8600 Buy, 8665 Sell
    rt = calculate_round_trip_charges(8600.0, 8665.0, 40, exchange="MCX", asset_class="COMMODITY")
    assert rt["total_brokerage"] == 40.0
    assert rt["total_stt_ctt"] > 0.0  # CTT
    assert rt["total_charges"] > 100.0  # Around Rs. 110 - 125
