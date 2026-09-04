from core.models import Instrument, Order, OrderSide, OrderType, OrderStatus, OptionType
from brokers.paper_broker import PaperBroker


def test_paper_broker_order_execution():
    broker = PaperBroker(initial_capital=200000.0, slippage_pct=0.0)
    broker.authenticate()

    sym = "NIFTY2691024500CE"
    broker.set_ltp(sym, 100.0)

    inst = Instrument(symbol=sym, lot_size=75, option_type=OptionType.CE)

    # Buy 75 qty @ 100
    buy_order = Order(
        order_id="",
        instrument=inst,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=75
    )
    placed = broker.place_order(buy_order)

    assert placed.status == OrderStatus.FILLED
    assert placed.average_price == 100.0

    positions = broker.get_positions()
    assert sym in positions
    assert positions[sym].quantity == 75
    assert positions[sym].unrealized_pnl == 0.0

    # Market rises to 120
    broker.set_ltp(sym, 120.0)
    assert positions[sym].unrealized_pnl == (120.0 - 100.0) * 75  # 1500 profit

    # Sell 75 qty to close position
    sell_order = Order(
        order_id="",
        instrument=inst,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=75
    )
    broker.place_order(sell_order)

    assert positions[sym].quantity == 0
    assert positions[sym].realized_pnl == 1500.0

    margins = broker.get_margins()
    assert margins["gross_pnl"] == 1500.0
    assert margins["total_charges"] > 0
    assert margins["total_pnl"] == round(1500.0 - margins["total_charges"], 2)
    assert margins["net_worth"] == round(200000.0 + margins["total_pnl"], 2)


def test_paper_broker_square_off_all():
    broker = PaperBroker(initial_capital=200000.0, slippage_pct=0.0)
    broker.authenticate()

    inst1 = Instrument(symbol="NIFTY_CE", lot_size=75)
    inst2 = Instrument(symbol="NIFTY_PE", lot_size=75)
    broker.set_ltp("NIFTY_CE", 50.0)
    broker.set_ltp("NIFTY_PE", 50.0)

    # Short Straddle
    broker.place_order(Order(order_id="", instrument=inst1, side=OrderSide.SELL, order_type=OrderType.MARKET, quantity=75))
    broker.place_order(Order(order_id="", instrument=inst2, side=OrderSide.SELL, order_type=OrderType.MARKET, quantity=75))

    positions = broker.get_positions()
    assert positions["NIFTY_CE"].quantity == -75
    assert positions["NIFTY_PE"].quantity == -75

    # Trigger square-off
    exits = broker.square_off_all_positions()
    assert len(exits) == 2
    assert positions["NIFTY_CE"].quantity == 0
    assert positions["NIFTY_PE"].quantity == 0
