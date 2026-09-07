"""
0-DTE Expiry Day Afternoon Theta Decay Scalp Strategy Module.
Exploits rapid non-linear exponential time decay on expiry afternoon (12:45 - 14:45 PM)
using OTM short strangles with strict 25% stop-loss caps per leg.
"""

import time
from datetime import datetime, date, time as dtime
from typing import Dict, Any, Optional, List
import pandas as pd

from core.logger import get_logger
from brokers.base_broker import BaseBroker
from core.risk_manager import RiskManager
from core.models import Order, OrderSide, OrderType, OrderStatus, Instrument, Tick
from core.option_chain import get_next_weekly_expiry, OptionType, get_atm_strike
from core.market_data import get_live_option_quote
from config.settings import settings

logger = get_logger("ThetaDecayTrader")


class ThetaDecayTraderStrategy:
    """
    0-DTE Expiry Theta Engine:
    - Runs exclusively on weekly expiry days (NIFTY: Tuesday).
    - Entry Window: 12:45 PM.
    - Sells OTM CE (+100 pts) and OTM PE (-100 pts).
    - Stop Loss: 25% premium expansion per leg.
    - Profit Target: 50% premium decay or 14:45 PM time exit.
    """

    def __init__(
        self,
        broker: BaseBroker,
        risk_manager: RiskManager,
        lots: int = 2,
        symbol: str = "NIFTY",
        telegram_notifier=None
    ):
        self.name = "THETA-0DTE"
        self.broker = broker
        self.risk_manager = risk_manager
        self.lots = lots
        self.symbol = symbol
        self.telegram = telegram_notifier

        self.trade_date: Optional[date] = None
        self.is_expiry_day = False
        self.in_trade = False
        self.trade_closed_today = False

        # Leg management
        self.ce_symbol = ""
        self.pe_symbol = ""
        self.ce_entry_price = 0.0
        self.pe_entry_price = 0.0
        self.ce_sl_price = 0.0
        self.pe_sl_price = 0.0
        self.ce_open = False
        self.pe_open = False
        self.ce_order: Optional[Order] = None
        self.pe_order: Optional[Order] = None

        self.daily_pnl = 0.0
        self.is_active = True

    def initialize(self):
        now = datetime.now()
        self.trade_date = now.date()
        self.check_expiry_status(now)
        logger.info(f"🎯 [{self.name}] Initialized for {self.symbol}. Expiry Day: {self.is_expiry_day}")

    def check_expiry_status(self, now: datetime):
        """NIFTY weekly expiry is Tuesday (weekday == 1)."""
        # 0 = Monday, 1 = Tuesday, 2 = Wednesday, 3 = Thursday, 4 = Friday
        self.is_expiry_day = (now.weekday() == 1) if self.symbol == "NIFTY" else (now.weekday() == 3)

    def on_tick(self, tick: Tick):
        if not self.is_active:
            return

        now = tick.timestamp
        if self.trade_date != now.date():
            self.trade_date = now.date()
            self.trade_closed_today = False
            self.in_trade = False
            self.check_expiry_status(now)

        if self.in_trade:
            self.manage_active_strangle(now)

    def on_bar(self, bar: Dict[str, Any]):
        """Evaluates entry at 12:45 PM on expiry days."""
        if not self.is_active or self.in_trade or self.trade_closed_today:
            return

        ts = bar["timestamp"]
        if isinstance(ts, str):
            ts = pd.to_datetime(ts)
        t = ts.time()

        # Only on Expiry Day between 12:45 PM and 13:15 PM
        if self.is_expiry_day and dtime(12, 45) <= t <= dtime(13, 15):
            self.enter_theta_strangle(bar["close"], ts)

    def enter_theta_strangle(self, spot_price: float, current_dt: datetime):
        """Sells OTM CE (+100) and OTM PE (-100)."""
        if self.in_trade or self.trade_closed_today:
            return

        atm = get_atm_strike(spot_price, step=settings.NIFTY_STRIKE_STEP)
        ce_strike = atm + 100
        pe_strike = atm - 100
        expiry = get_next_weekly_expiry(symbol="NIFTY", as_string=True)

        self.ce_symbol = f"NIFTY{expiry}{ce_strike}CE"
        self.pe_symbol = f"NIFTY{expiry}{pe_strike}PE"

        ce_quote = get_live_option_quote(symbol="nifty", strike=ce_strike, opt_type="CE")
        pe_quote = get_live_option_quote(symbol="nifty", strike=pe_strike, opt_type="PE")

        self.ce_entry_price = ce_quote.get("price", 25.0)
        self.pe_entry_price = pe_quote.get("price", 25.0)

        # 25% Stop Loss
        self.ce_sl_price = round(self.ce_entry_price * 1.25, 2)
        self.pe_sl_price = round(self.pe_entry_price * 1.25, 2)

        contract_qty = self.lots * settings.NIFTY_LOT_SIZE

        # Place CE Sell Order
        ce_ord = Order(
            order_id=f"THETA_CE_{int(time.time())}",
            instrument=Instrument(symbol=self.ce_symbol, exchange="NFO", lot_size=settings.NIFTY_LOT_SIZE),
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=contract_qty,
            price=self.ce_entry_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag="THETA_0DTE_CE"
        )
        pe_ord = Order(
            order_id=f"THETA_PE_{int(time.time())}",
            instrument=Instrument(symbol=self.pe_symbol, exchange="NFO", lot_size=settings.NIFTY_LOT_SIZE),
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=contract_qty,
            price=self.pe_entry_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag="THETA_0DTE_PE"
        )

        p_ce = self.broker.place_order(ce_ord)
        p_pe = self.broker.place_order(pe_ord)

        self.in_trade = True
        self.ce_open = True
        self.pe_open = True
        self.ce_order = p_ce
        self.pe_order = p_pe

        logger.info(f"🚀 [0-DTE THETA STRANGLE ENTERED] Sold {self.ce_symbol} @ ₹{self.ce_entry_price:.2f} & {self.pe_symbol} @ ₹{self.pe_entry_price:.2f}")

        if self.telegram:
            self.telegram.send_notification(
                f"⏳ <b>0-DTE THETA STRANGLE ENTERED</b>\n"
                f"• CE Leg: <b>{self.ce_symbol}</b> @ ₹{self.ce_entry_price:.2f} (SL: ₹{self.ce_sl_price:.2f})\n"
                f"• PE Leg: <b>{self.pe_symbol}</b> @ ₹{self.pe_entry_price:.2f} (SL: ₹{self.pe_sl_price:.2f})\n"
                f"• Target: 50% Decay (₹{(self.ce_entry_price + self.pe_entry_price)*0.5:.2f})\n"
                f"• Exit Time: 14:45 PM IST"
            )

    def manage_active_strangle(self, current_dt: datetime):
        if not self.in_trade:
            return

        # Fetch current quotes
        atm_ce = int(self.ce_symbol[-7:-2]) if len(self.ce_symbol) >= 7 else 24000
        atm_pe = int(self.pe_symbol[-7:-2]) if len(self.pe_symbol) >= 7 else 23800

        ce_q = get_live_option_quote(symbol="nifty", strike=atm_ce, opt_type="CE")
        pe_q = get_live_option_quote(symbol="nifty", strike=atm_pe, opt_type="PE")

        curr_ce = ce_q.get("price", self.ce_entry_price)
        curr_pe = pe_q.get("price", self.pe_entry_price)

        # 1. Stop Loss Checks
        if self.ce_open and curr_ce >= self.ce_sl_price:
            self._close_leg("CE", curr_ce, "SL HIT (25% Expansion)", current_dt)

        if self.pe_open and curr_pe >= self.pe_sl_price:
            self._close_leg("PE", curr_pe, "SL HIT (25% Expansion)", current_dt)

        # 2. Combined 50% Profit Target
        comb_entry = self.ce_entry_price + self.pe_entry_price
        comb_curr = (curr_ce if self.ce_open else 0.0) + (curr_pe if self.pe_open else 0.0)
        if comb_curr <= (comb_entry * 0.50):
            self.close_all("TARGET ACHIEVED (50% Premium Decayed)", current_dt)
            return

        # 3. Time-based Exit at 14:45 PM
        if current_dt.time() >= dtime(14, 45):
            self.close_all("EXPIRY CLOSE (14:45 PM Time Exit)", current_dt)
            return

    def _close_leg(self, leg: str, exit_price: float, reason: str, current_dt: datetime):
        is_ce = (leg == "CE")
        ord_obj = self.ce_order if is_ce else self.pe_order
        if not ord_obj:
            return

        close_ord = Order(
            order_id=f"EX_THETA_{leg}_{int(time.time())}",
            instrument=ord_obj.instrument,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=ord_obj.quantity,
            price=exit_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag=f"THETA_EXIT_{leg}"
        )
        self.broker.place_order(close_ord)

        entry_p = self.ce_entry_price if is_ce else self.pe_entry_price
        # Short option PnL = (Entry - Exit) * Qty
        pnl = round((entry_p - exit_price) * ord_obj.quantity, 2)
        self.daily_pnl += pnl

        if is_ce:
            self.ce_open = False
        else:
            self.pe_open = False

        logger.info(f"🏁 [0-DTE {leg} LEG CLOSED: {reason}] PnL: ₹{pnl:+,.2f}")

        if not self.ce_open and not self.pe_open:
            self.in_trade = False
            self.trade_closed_today = True

    def close_all(self, reason: str, current_dt: datetime):
        if self.ce_open:
            self._close_leg("CE", max(1.0, self.ce_entry_price * 0.5), reason, current_dt)
        if self.pe_open:
            self._close_leg("PE", max(1.0, self.pe_entry_price * 0.5), reason, current_dt)

        self.in_trade = False
        self.trade_closed_today = True

        try:
            from core.strategy_ledger import strategy_ledger
            strategy_ledger.record_trade("theta", {
                "trade_id": f"THETA_{int(time.time())}",
                "strategy": "THETA-0DTE",
                "index": self.symbol,
                "date": current_dt.strftime("%Y-%m-%d"),
                "side": "SHORT_STRANGLE",
                "entry_time": "12:45:00",
                "exit_time": current_dt.strftime("%H:%M:%S"),
                "net_pnl": self.daily_pnl,
                "reason": reason,
                "mode": "PAPER",
                "notes": f"Expiry 0-DTE Scalp. Closed via {reason}."
            })
        except Exception as e:
            logger.warning(f"Failed to record to Theta ledger: {e}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "strategy": self.name,
            "is_expiry_day": self.is_expiry_day,
            "in_trade": self.in_trade,
            "daily_pnl": self.daily_pnl
        }
