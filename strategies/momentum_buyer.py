"""
Directional Momentum Option Buyer Strategy.
Identifies trend momentum on Nifty using EMA crossovers (9/21 EMA) and trades ATM Call/Put options
with tight stop loss and dynamic profit trailing.
"""

from datetime import datetime, time, date
from typing import Dict, Any, List, Optional
from collections import deque
from core.models import (
    Instrument, Order, OrderSide, OrderType, ProductType, OptionType, Tick
)
from core.option_chain import get_atm_strike, get_next_weekly_expiry, format_nifty_symbol
from core.logger import get_logger
from brokers.base_broker import BaseBroker
from core.risk_manager import RiskManager
from strategies.base_strategy import BaseStrategy
from config.settings import settings

logger = get_logger("MomentumBuyer")


class MomentumBuyerStrategy(BaseStrategy):
    def __init__(
        self,
        broker: BaseBroker,
        risk_manager: RiskManager,
        fast_ema_period: int = 9,
        slow_ema_period: int = 21,
        sl_points: float = 20.0,
        target_points: float = 40.0,
        trailing_step: float = 10.0,
        trailing_move: float = 10.0,
        lots: int = 1
    ):
        super().__init__("Directional Momentum Buyer", broker, risk_manager)
        self.fast_ema_period = fast_ema_period
        self.slow_ema_period = slow_ema_period
        self.sl_points = sl_points
        self.target_points = target_points
        self.trailing_step = trailing_step
        self.trailing_move = trailing_move
        self.lots = lots
        self.lot_size = settings.NIFTY_LOT_SIZE

        # Indicator state
        self.price_history = deque(maxlen=200)
        self.fast_ema: Optional[float] = None
        self.slow_ema: Optional[float] = None
        self.prev_fast_ema: Optional[float] = None
        self.prev_slow_ema: Optional[float] = None

        # Position state
        self.active_position: Optional[OptionType] = None
        self.active_instrument: Optional[Instrument] = None
        self.entry_price: float = 0.0
        self.current_sl: float = 0.0
        self.target_price: float = 0.0
        self.highest_price_seen: float = 0.0
        self.trades_count: int = 0
        self.max_trades_per_day: int = 3

    def initialize(self):
        self.expiry_date = get_next_weekly_expiry()
        self.price_history.clear()
        self.active_position = None
        self.is_active = True
        logger.info(f"🎯 Strategy '{self.name}' initialized. Fast EMA: {self.fast_ema_period}, Slow EMA: {self.slow_ema_period}")

    def on_tick(self, tick: Tick):
        # Update trailing SL if we hold a position in this option
        if self.active_instrument and tick.symbol == self.active_instrument.symbol:
            current_price = tick.ltp

            # Check Stop Loss
            if current_price <= self.current_sl:
                self._exit_position(current_price, f"SL Hit @ ₹{current_price:.2f}")
                return

            # Check Target
            if current_price >= self.target_price:
                self._exit_position(current_price, f"Target Reached @ ₹{current_price:.2f}")
                return

            # Check Trailing SL
            if current_price > self.highest_price_seen:
                self.highest_price_seen = current_price
                gain = current_price - self.entry_price
                if gain >= self.trailing_step:
                    trailed_sl = self.entry_price + ((gain // self.trailing_step) * self.trailing_move) - self.sl_points
                    if trailed_sl > self.current_sl:
                        self.current_sl = round(trailed_sl, 2)
                        logger.info(f"📈 Trailing SL moved up to ₹{self.current_sl:.2f} for {tick.symbol}")

    def on_bar(self, bar: Dict[str, Any]):
        """Feed completed 5-minute bar to update indicators and detect crossovers."""
        close = bar["close"]
        self.price_history.append(close)

        self._update_emas(close)
        self._check_crossover_signals(close)

    def _update_emas(self, price: float):
        k_fast = 2.0 / (self.fast_ema_period + 1)
        k_slow = 2.0 / (self.slow_ema_period + 1)

        self.prev_fast_ema = self.fast_ema
        self.prev_slow_ema = self.slow_ema

        if self.fast_ema is None:
            self.fast_ema = price
        else:
            self.fast_ema = (price * k_fast) + (self.fast_ema * (1 - k_fast))

        if self.slow_ema is None:
            self.slow_ema = price
        else:
            self.slow_ema = (price * k_slow) + (self.slow_ema * (1 - k_slow))

    def _check_crossover_signals(self, spot_price: float):
        if self.active_position is not None or self.trades_count >= self.max_trades_per_day:
            return

        if self.prev_fast_ema is None or self.prev_slow_ema is None:
            return

        # Bullish Crossover: Fast crosses above Slow
        bullish = (self.prev_fast_ema <= self.prev_slow_ema) and (self.fast_ema > self.slow_ema)

        # Bearish Crossover: Fast crosses below Slow
        bearish = (self.prev_fast_ema >= self.prev_slow_ema) and (self.fast_ema < self.slow_ema)

        if bullish:
            self._enter_trade(OptionType.CE, spot_price)
        elif bearish:
            self._enter_trade(OptionType.PE, spot_price)

    def check_entry_conditions(self, current_dt: datetime, spot_price: float):
        # Continuous spot feed simulated as bars or ticks
        if self.active_position is None and len(self.price_history) >= self.slow_ema_period:
            self._check_crossover_signals(spot_price)

    def _enter_trade(self, opt_type: OptionType, spot_price: float):
        atm_strike = get_atm_strike(spot_price, settings.NIFTY_STRIKE_STEP)
        symbol = format_nifty_symbol(self.expiry_date, atm_strike, opt_type)

        inst = Instrument(
            symbol=symbol, strike=float(atm_strike), expiry=self.expiry_date,
            option_type=opt_type, lot_size=self.lot_size
        )
        qty = self.lots * self.lot_size

        buy_order = Order(
            order_id="",
            instrument=inst,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=qty,
            tag=f"MOM_{opt_type.value}"
        )

        valid, reason = self.risk_manager.validate_new_order(buy_order)
        if not valid:
            logger.warning(f"RMS blocked Momentum {opt_type.value} entry: {reason}")
            return

        placed = self.broker.place_order(buy_order)
        self.entry_price = placed.average_price or self.broker.get_ltp(symbol)
        self.current_sl = max(0.05, round(self.entry_price - self.sl_points, 2))
        self.target_price = round(self.entry_price + self.target_points, 2)
        self.highest_price_seen = self.entry_price
        self.active_position = opt_type
        self.active_instrument = inst
        self.trades_count += 1

        logger.info(
            f"🚀 MOMENTUM BUY: {opt_type.value} {symbol} @ ₹{self.entry_price:.2f} | "
            f"SL: ₹{self.current_sl:.2f} | Target: ₹{self.target_price:.2f}"
        )

    def _exit_position(self, exit_price: float, reason: str):
        if not self.active_instrument or self.active_position is None:
            return

        qty = self.lots * self.lot_size
        exit_order = Order(
            order_id="",
            instrument=self.active_instrument,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=qty,
            tag="EXIT_MOM"
        )
        self.broker.place_order(exit_order)

        logger.info(f"🛑 Closed Momentum {self.active_position.value}: {self.active_instrument.symbol} - {reason}")
        self.active_position = None
        self.active_instrument = None

    def check_exit_conditions(self, current_dt: datetime):
        if self.risk_manager.check_time_for_square_off(current_dt):
            if self.active_instrument:
                ltp = self.broker.get_ltp(self.active_instrument.symbol)
                self._exit_position(ltp, "End-of-day Auto Square-Off")

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "active_position": self.active_position.value if self.active_position else "FLAT",
            "symbol": self.active_instrument.symbol if self.active_instrument else "-",
            "entry_price": self.entry_price,
            "current_sl": self.current_sl,
            "target_price": self.target_price,
            "trades_today": self.trades_count,
            "fast_ema": round(self.fast_ema, 2) if self.fast_ema else None,
            "slow_ema": round(self.slow_ema, 2) if self.slow_ema else None,
        }
