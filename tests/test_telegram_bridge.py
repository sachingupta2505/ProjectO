from telegram_bridge.bot import TelegramBridge
from brokers.paper_broker import PaperBroker
from core.risk_manager import RiskManager
from strategies.short_straddle import ShortStraddleStrategy
from core.models import Instrument, Order, OrderSide, OrderType


class MockRunner:
    def __init__(self):
        self.is_paper = True
        self.broker_type = "angel"
        self.lots = 1
        self.risk_manager = RiskManager(max_daily_loss=5000.0)
        self.broker = PaperBroker(initial_capital=200000.0)
        self.broker.authenticate()
        self.strategy = ShortStraddleStrategy(self.broker, self.risk_manager, lots=1)
        self.strategy.initialize()


def test_telegram_authorization_guard():
    bridge = TelegramBridge(token="mock_token", chat_id="12345678", runner=MockRunner())

    # Unauthorized sender
    resp = bridge.handle_command("/status", sender_chat_id="99999999")
    assert "Unauthorized" in resp

    # Authorized sender
    resp_auth = bridge.handle_command("/status", sender_chat_id="12345678")
    assert "Trading Bot Status" in resp_auth


def test_telegram_commands():
    runner = MockRunner()
    bridge = TelegramBridge(token="mock_token", chat_id="12345678", runner=runner)

    # Test /help
    help_resp = bridge.handle_command("/help", sender_chat_id="12345678")
    assert "/status" in help_resp
    assert "/squareoff" in help_resp

    # Test /pnl
    pnl_resp = bridge.handle_command("/pnl", sender_chat_id="12345678")
    assert "Portfolio PnL Overview" in pnl_resp

    # Test /setlots
    lots_resp = bridge.handle_command("/setlots 3", sender_chat_id="12345678")
    assert "Lots updated to <b>3</b>" in lots_resp
    assert runner.lots == 3

    # Test /setsl
    sl_resp = bridge.handle_command("/setsl 30", sender_chat_id="12345678")
    assert "Strategy Stop Loss updated to <b>30.0%</b>" in sl_resp
    assert runner.strategy.sl_pct == 0.30

    # Test /squareoff
    # Place a dummy position first
    runner.broker.place_order(Order(
        order_id="1", instrument=Instrument(symbol="NIFTY_CE"), side=OrderSide.SELL,
        order_type=OrderType.MARKET, quantity=75
    ))
    assert len(runner.broker.get_positions()) == 1

    sq_resp = bridge.handle_command("/squareoff", sender_chat_id="12345678")
    assert "EMERGENCY SQUARE-OFF EXECUTED" in sq_resp
    assert runner.risk_manager.kill_switch_active is True
