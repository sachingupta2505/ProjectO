"""
9:20 AM Intraday Short Straddle / Strangle Strategy.
Captures intra-day theta decay on Nifty 50 weekly options with individual leg-level Stop Loss.
"""

from datetime import datetime, time, date
from typing import Dict, Any, Optional
from core.models import (
    Instrument, Order, OrderSide, OrderType, ProductType, OptionType, Tick
)
from core.option_chain import get_atm_strike, get_next_weekly_expiry, format_nifty_symbol
from core.logger import get_logger
from brokers.base_broker import BaseBroker
from core.risk_manager import RiskManager
from strategies.base_strategy import BaseStrategy
from config.settings import settings

logger = get_logger("ShortStraddle")


class ShortStraddleStrategy(BaseStrategy):
    def __init__(
        self,
        broker: BaseBroker,
        risk_manager: RiskManager,
        entry_time_str: str = "09:20:00",
        exit_time_str: str = "15:15:00",
        sl_pct: float = 0.25,
        target_pct: float = 0.80,
        lots: int = 1,
        strike_offset: int = 0  # 0 = Straddle (ATM), +1 = Strangle (1 strike OTM)
    ):
        super().__init__("9:20 Short Straddle", broker, risk_manager)
        self.entry_time_str = entry_time_str
        self.exit_time_str = exit_time_str
        self.sl_pct = sl_pct
        self.target_pct = target_pct
        self.lots = lots
        self.lot_size = settings.NIFTY_LOT_SIZE
        self.strike_offset = strike_offset

        # State tracking
        self.entry_done = False
        self.exit_done = False
        self.atm_strike: Optional[int] = None
        self.expiry_date: Optional[date] = None

        # CE Leg details
        self.ce_instrument: Optional[Instrument] = None
        self.ce_entry_price: float = 0.0
        self.ce_sl_price: float = 0.0
        self.ce_target_price: float = 0.0
        self.ce_exited: bool = False
        self.ce_exit_reason: str = ""

        # PE Leg details
        self.pe_instrument: Optional[Instrument] = None
        self.pe_entry_price: float = 0.0
        self.pe_sl_price: float = 0.0
        self.pe_target_price: float = 0.0
        self.pe_exited: bool = False
        self.pe_exit_reason: str = ""

    def initialize(self):
        self.expiry_date = get_next_weekly_expiry()
        self.entry_done = False
        self.exit_done = False
        self.is_active = True
        logger.info(f"🎯 Strategy '{self.name}' initialized. Target Expiry: {self.expiry_date}. Entry time: {self.entry_time_str}")

    def check_entry_conditions(self, current_dt: datetime, spot_price: float):
        if self.entry_done or not self.is_active:
            return

        h, m, s = map(int, self.entry_time_str.split(":"))
        target_time = time(h, m, s)

        if current_dt.time() >= target_time:
            self._execute_entry(spot_price)

    def _execute_entry(self, spot_price: float):
        self.atm_strike = get_atm_strike(spot_price, settings.NIFTY_STRIKE_STEP)
        ce_strike = self.atm_strike + (self.strike_offset * settings.NIFTY_STRIKE_STEP)
        pe_strike = self.atm_strike - (self.strike_offset * settings.NIFTY_STRIKE_STEP)

        ce_sym = format_nifty_symbol(self.expiry_date, ce_strike, OptionType.CE)
        pe_sym = format_nifty_symbol(self.expiry_date, pe_strike, OptionType.PE)

        self.ce_instrument = Instrument(
            symbol=ce_sym, strike=float(ce_strike), expiry=self.expiry_date,
            option_type=OptionType.CE, lot_size=self.lot_size
        )
        self.pe_instrument = Instrument(
            symbol=pe_sym, strike=float(pe_strike), expiry=self.expiry_date,
            option_type=OptionType.PE, lot_size=self.lot_size
        )

        qty = self.lots * self.lot_size

        # Place CE Sell Order
        ce_order = Order(
            order_id="",
            instrument=self.ce_instrument,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=qty,
            tag="STRADDLE_CE"
        )
        valid_ce, reason_ce = self.risk_manager.validate_new_order(ce_order)
        if not valid_ce:
            logger.warning(f"RMS blocked CE entry: {reason_ce}")
            return

        # Place PE Sell Order
        pe_order = Order(
            order_id="",
            instrument=self.pe_instrument,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=qty,
            tag="STRADDLE_PE"
        )
        valid_pe, reason_pe = self.risk_manager.validate_new_order(pe_order)
        if not valid_pe:
            logger.warning(f"RMS blocked PE entry: {reason_pe}")
            return

        placed_ce = self.broker.place_order(ce_order)
        placed_pe = self.broker.place_order(pe_order)

        self.ce_entry_price = placed_ce.average_price or self.broker.get_ltp(ce_sym)
        self.pe_entry_price = placed_pe.average_price or self.broker.get_ltp(pe_sym)

        self.ce_sl_price = self.risk_manager.calculate_leg_stop_loss(self.ce_entry_price, OrderSide.SELL, self.sl_pct)
        self.pe_sl_price = self.risk_manager.calculate_leg_stop_loss(self.pe_entry_price, OrderSide.SELL, self.sl_pct)

        self.ce_target_price = round(self.ce_entry_price * (1.0 - self.target_pct), 2)
        self.pe_target_price = round(self.pe_entry_price * (1.0 - self.target_pct), 2)

        self.entry_done = True
        logger.info(
            f"🔥 Straddle entered at Spot {spot_price}!\n"
            f"   CE: {ce_sym} @ ₹{self.ce_entry_price:.2f} | SL: ₹{self.ce_sl_price:.2f} | Target: ₹{self.ce_target_price:.2f}\n"
            f"   PE: {pe_sym} @ ₹{self.pe_entry_price:.2f} | SL: ₹{self.pe_sl_price:.2f} | Target: ₹{self.pe_target_price:.2f}"
        )

    def on_tick(self, tick: Tick):
        if not self.entry_done or self.exit_done:
            return

        # Monitor CE Leg
        if self.ce_instrument and tick.symbol == self.ce_instrument.symbol and not self.ce_exited:
            if tick.ltp >= self.ce_sl_price:
                self._exit_leg(OptionType.CE, tick.ltp, f"SL Hit @ ₹{tick.ltp:.2f}")
            elif tick.ltp <= self.ce_target_price:
                self._exit_leg(OptionType.CE, tick.ltp, f"Target Hit @ ₹{tick.ltp:.2f}")

        # Monitor PE Leg
        if self.pe_instrument and tick.symbol == self.pe_instrument.symbol and not self.pe_exited:
            if tick.ltp >= self.pe_sl_price:
                self._exit_leg(OptionType.PE, tick.ltp, f"SL Hit @ ₹{tick.ltp:.2f}")
            elif tick.ltp <= self.pe_target_price:
                self._exit_leg(OptionType.PE, tick.ltp, f"Target Hit @ ₹{tick.ltp:.2f}")

        if self.ce_exited and self.pe_exited:
            self.exit_done = True
            logger.info("🏁 Both Straddle legs closed.")

    def on_bar(self, bar: Dict[str, Any]):
        pass

    def check_exit_conditions(self, current_dt: datetime):
        if not self.entry_done or self.exit_done:
            return

        h, m, s = map(int, self.exit_time_str.split(":"))
        exit_time = time(h, m, s)

        if current_dt.time() >= exit_time:
            logger.info(f"⏰ Reached square-off time ({self.exit_time_str}). Closing open straddle legs.")
            if not self.ce_exited and self.ce_instrument:
                self._exit_leg(OptionType.CE, self.broker.get_ltp(self.ce_instrument.symbol), "Time Square-Off")
            if not self.pe_exited and self.pe_instrument:
                self._exit_leg(OptionType.PE, self.broker.get_ltp(self.pe_instrument.symbol), "Time Square-Off")
            self.exit_done = True

    def _exit_leg(self, option_type: OptionType, current_price: float, reason: str):
        qty = self.lots * self.lot_size
        if option_type == OptionType.CE and self.ce_instrument and not self.ce_exited:
            exit_order = Order(
                order_id="",
                instrument=self.ce_instrument,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=qty,
                tag="EXIT_CE"
            )
            self.broker.place_order(exit_order)
            self.ce_exited = True
            self.ce_exit_reason = reason
            logger.info(f"🛑 Closed CE Leg: {self.ce_instrument.symbol} - Reason: {reason}")

        elif option_type == OptionType.PE and self.pe_instrument and not self.pe_exited:
            exit_order = Order(
                order_id="",
                instrument=self.pe_instrument,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=qty,
                tag="EXIT_PE"
            )
            self.broker.place_order(exit_order)
            self.pe_exited = True
            self.pe_exit_reason = reason
            logger.info(f"🛑 Closed PE Leg: {self.pe_instrument.symbol} - Reason: {reason}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entry_done": self.entry_done,
            "exit_done": self.exit_done,
            "atm_strike": self.atm_strike,
            "ce_leg": {
                "symbol": self.ce_instrument.symbol if self.ce_instrument else "-",
                "entry_price": self.ce_entry_price,
                "sl_price": self.ce_sl_price,
                "target_price": self.ce_target_price,
                "exited": self.ce_exited,
                "exit_reason": self.ce_exit_reason
            },
            "pe_leg": {
                "symbol": self.pe_instrument.symbol if self.pe_instrument else "-",
                "entry_price": self.pe_entry_price,
                "sl_price": self.pe_sl_price,
                "target_price": self.pe_target_price,
                "exited": self.pe_exited,
                "exit_reason": self.pe_exit_reason
            }
        }
