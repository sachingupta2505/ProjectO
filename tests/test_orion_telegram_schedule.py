"""Telegram cadence tests for the quiet ORION monitoring workflow."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from main import TradingBotRunner


def _runner_with_telegram():
    runner = TradingBotRunner.__new__(TradingBotRunner)
    runner.telegram = MagicMock()
    runner._orion_opening_alert_date = None
    runner._orion_opening_pending_alert_date = None
    runner._orion_decision_alert_date = None
    return runner


def test_orion_telegram_cadence_is_0930_and_1100_only():
    runner = _runner_with_telegram()
    strategy = SimpleNamespace(
        setup_valid=True,
        setup_side="PUT",
        retest_min=23697.2,
        retest_max=23718.4,
        invalidation_spot=23763.9,
        target1_spot=23669.2,
        target2_spot=23612.7,
        entry_cutoff=datetime.strptime("11:00", "%H:%M").time(),
        in_trade=False,
        trade_closed_today=False,
        opening_decision_reason="Opening structure qualifies; awaiting the retest and confirmation.",
    )

    runner._send_orion_scheduled_alerts(strategy, datetime(2026, 9, 9, 9, 30))
    runner._send_orion_scheduled_alerts(strategy, datetime(2026, 9, 9, 10, 5))
    runner._send_orion_scheduled_alerts(strategy, datetime(2026, 9, 9, 11, 0))
    runner._send_orion_scheduled_alerts(strategy, datetime(2026, 9, 9, 11, 5))

    assert runner.telegram.send_notification.call_count == 2
    opening_alert = runner.telegram.send_notification.call_args_list[0].args[0]
    decision_alert = runner.telegram.send_notification.call_args_list[1].args[0]
    assert "09:30" in opening_alert
    assert "Retest entry zone" in opening_alert
    assert "11:00" in decision_alert
    assert "No trade taken" in decision_alert


def test_orion_pending_data_is_not_reported_as_a_final_no_setup():
    runner = _runner_with_telegram()
    strategy = SimpleNamespace(
        setup_valid=False,
        opening_data_pending=True,
        opening_decision_reason="Waiting for Angel's verified opening candle; retry 2/3 at 09:35 IST.",
    )

    runner._send_orion_scheduled_alerts(strategy, datetime(2026, 9, 9, 9, 30))

    message = runner.telegram.send_notification.call_args.args[0]
    assert "Opening Data Delayed" in message
    assert "No decision has been made" in message
    assert runner._orion_opening_alert_date is None
