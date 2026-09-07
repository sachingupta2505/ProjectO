from datetime import datetime
from core.models import Order, OrderSide, OrderType, Instrument
from core.risk_manager import RiskManager


def test_rms_daily_loss_kill_switch():
    rms = RiskManager(max_daily_loss=5000.0, max_daily_profit=15000.0)

    # Safe loss
    breached, _ = rms.evaluate_daily_pnl(-2000.0)
    assert not breached
    assert not rms.kill_switch_active

    # Breach max loss
    breached, reason = rms.evaluate_daily_pnl(-5500.0)
    assert breached
    assert rms.kill_switch_active
    assert "Max Daily Loss" in reason

    # After breach, new order should be rejected
    inst = Instrument(symbol="NIFTY24500CE")
    order = Order(order_id="1", instrument=inst, side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=75)
    valid, reject_msg = rms.validate_new_order(order)
    assert not valid
    assert "Kill Switch is active" in reject_msg


def test_leg_stop_loss_calculation():
    rms = RiskManager()
    # SELL entry @ 100. 25% SL should be 125.0
    sl_sell = rms.calculate_leg_stop_loss(100.0, OrderSide.SELL, 0.25)
    assert sl_sell == 125.0

    # BUY entry @ 100. 25% SL should be 75.0
    sl_buy = rms.calculate_leg_stop_loss(100.0, OrderSide.BUY, 0.25)
    assert sl_buy == 75.0


def test_rms_daily_limits():
    # Test configured settings (₹11,000 Target & ₹6,000 Stop Loss)
    rms = RiskManager(max_daily_loss=6000.0, max_daily_profit=11000.0)
    assert rms.max_daily_loss == 6000.0
    assert rms.max_daily_profit == 11000.0

    # PnL within bounds
    breached, _ = rms.evaluate_daily_pnl(3000.0)
    assert not breached

    # Max Loss breached (-6,000)
    breached, reason = rms.evaluate_daily_pnl(-6050.0)
    assert breached
    assert rms.kill_switch_active
    assert "Max Daily Loss" in reason


def test_rms_multi_session_timing():
    rms = RiskManager()
    # 15:20 IST -> Past Nifty square-off (15:15), but before Crude Oil square-off (23:15)
    t_afternoon = datetime(2026, 9, 4, 15, 20, 0)
    assert rms.check_time_for_square_off(t_afternoon, symbol="NIFTY") is True
    assert rms.check_time_for_square_off(t_afternoon, symbol="CRUDEOIL") is False

    # 23:20 IST -> Past Crude Oil square-off
    t_night = datetime(2026, 9, 4, 23, 20, 0)
    assert rms.check_time_for_square_off(t_night, symbol="CRUDEOIL") is True

