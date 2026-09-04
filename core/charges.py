"""
Indian Financial Markets & Brokerage Transaction Charges Engine.
Calculates regulatory and statutory charges for Angel One:
- Brokerage (flat ₹20 per order)
- STT (Securities Transaction Tax on Options)
- CTT (Commodities Transaction Tax on MCX Futures)
- Exchange Turnover Fees (NSE / MCX)
- SEBI Turnover Charges
- GST (18% on Brokerage + Txn Charges + SEBI)
- Stamp Duty
"""

from typing import Dict, Any, Optional
from core.models import OrderSide


# Standard Angel One & Statutory Fee Rates
BROKERAGE_PER_ORDER = 20.0       # Flat ₹20 per executed order
STT_OPTIONS_SELL_RATE = 0.000625  # 0.0625% on sell premium turnover
CTT_COMMODITY_SELL_RATE = 0.000125 # 0.0125% on futures sell turnover
NSE_OPTIONS_TXN_RATE = 0.0003503  # 0.03503% on premium turnover
MCX_FUTURES_TXN_RATE = 0.000021   # 0.0021% on futures turnover
SEBI_RATE = 0.000001              # ₹10 per crore (0.0001%)
GST_RATE = 0.18                   # 18% on Brokerage + Txn + SEBI
STAMP_DUTY_OPTIONS_BUY = 0.00003  # 0.003% on buy premium turnover
STAMP_DUTY_COMMODITY_BUY = 0.00002 # 0.002% on buy futures turnover


def calculate_order_charges(
    side: OrderSide,
    price: float,
    quantity: int,
    exchange: str = "NFO",
    asset_class: str = "INDEX"
) -> Dict[str, float]:
    """
    Calculates detailed regulatory and broker charges for a single executed order fill.
    """
    turnover = round(price * quantity, 2)
    is_buy = (side == OrderSide.BUY)
    is_mcx = (exchange.upper() == "MCX" or asset_class.upper() == "COMMODITY")

    brokerage = BROKERAGE_PER_ORDER

    # STT / CTT (charged only on SELL side)
    stt_ctt = 0.0
    if not is_buy:
        if is_mcx:
            stt_ctt = round(turnover * CTT_COMMODITY_SELL_RATE, 2)
        else:
            stt_ctt = round(turnover * STT_OPTIONS_SELL_RATE, 2)

    # Exchange transaction fees
    if is_mcx:
        exch_txn = round(turnover * MCX_FUTURES_TXN_RATE, 2)
    else:
        exch_txn = round(turnover * NSE_OPTIONS_TXN_RATE, 2)

    # SEBI charges
    sebi = round(turnover * SEBI_RATE, 2)

    # GST (18% on Brokerage + Exchange Txn + SEBI)
    gst = round((brokerage + exch_txn + sebi) * GST_RATE, 2)

    # Stamp duty (charged only on BUY side)
    stamp_duty = 0.0
    if is_buy:
        if is_mcx:
            stamp_duty = round(turnover * STAMP_DUTY_COMMODITY_BUY, 2)
        else:
            stamp_duty = round(turnover * STAMP_DUTY_OPTIONS_BUY, 2)

    total_charges = round(brokerage + stt_ctt + exch_txn + sebi + gst + stamp_duty, 2)

    return {
        "brokerage": brokerage,
        "stt_ctt": stt_ctt,
        "exchange_txn": exch_txn,
        "sebi": sebi,
        "gst": gst,
        "stamp_duty": stamp_duty,
        "total_charges": total_charges,
        "turnover": turnover
    }


def calculate_round_trip_charges(
    buy_price: float,
    sell_price: float,
    quantity: int,
    exchange: str = "NFO",
    asset_class: str = "INDEX"
) -> Dict[str, float]:
    """
    Calculates total round-trip charges for an entry and exit.
    """
    buy_charges = calculate_order_charges(OrderSide.BUY, buy_price, quantity, exchange, asset_class)
    sell_charges = calculate_order_charges(OrderSide.SELL, sell_price, quantity, exchange, asset_class)

    total_round_trip = round(buy_charges["total_charges"] + sell_charges["total_charges"], 2)
    turnover_total = round(buy_charges["turnover"] + sell_charges["turnover"], 2)

    return {
        "buy_charges": buy_charges["total_charges"],
        "sell_charges": sell_charges["total_charges"],
        "total_charges": total_round_trip,
        "total_brokerage": round(buy_charges["brokerage"] + sell_charges["brokerage"], 2),
        "total_stt_ctt": round(buy_charges["stt_ctt"] + sell_charges["stt_ctt"], 2),
        "total_exch_txn": round(buy_charges["exchange_txn"] + sell_charges["exchange_txn"], 2),
        "total_gst": round(buy_charges["gst"] + sell_charges["gst"], 2),
        "total_stamp_duty": round(buy_charges["stamp_duty"] + sell_charges["stamp_duty"], 2),
        "total_turnover": turnover_total
    }
