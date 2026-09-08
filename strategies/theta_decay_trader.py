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
from core.option_chain import get_next_weekly_expiry, OptionType, get_atm_strike, format_nifty_symbol
from core.market_data import get_live_option_quote
from config.settings import settings

logger = get_logger("ThetaDecayTrader")


class ThetaDecayTraderStrategy:
    """
    0-DTE Expiry Theta Engine:
    - Runs exclusively on weekly expiry days (NIFTY: Tuesday).
    - Entry Window: 12:45 PM to 13:15 PM IST.
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
        self.ce_strike: int = 0
        self.pe_strike: int = 0
        self.ce_symbol: str = ""
        self.pe_symbol: str = ""
        self.ce_entry_price: float = 0.0
        self.pe_entry_price: float = 0.0
        self.ce_sl_price: float = 0.0
        self.pe_sl_price: float = 0.0
        self.ce_open: bool = False
        self.pe_open: bool = False
        self.ce_order: Optional[Order] = None
        self.pe_order: Optional[Order] = None

        self.daily_pnl: float = 0.0
        self.is_active: bool = True
        self._ledger_recorded: bool = False

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
            self._ledger_recorded = False
            self.check_expiry_status(now)

        # Check for trade entry on tick if within window (12:45 to 13:15)
        if self.is_expiry_day and not self.in_trade and not self.trade_closed_today:
            t = now.time()
            if dtime(12, 45) <= t <= dtime(13, 15):
                self.enter_theta_strangle(tick.ltp, now)

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

        step = getattr(settings, f"{self.symbol}_STRIKE_STEP", 50)
        atm = get_atm_strike(spot_price, strike_step=step)
        self.ce_strike = atm + 100
        self.pe_strike = atm - 100

        exp_weekday = 1 if self.symbol == "NIFTY" else 3
        exp_date = get_next_weekly_expiry(current_dt.date(), weekday=exp_weekday)

        self.ce_symbol = format_nifty_symbol(exp_date, self.ce_strike, OptionType.CE)
        self.pe_symbol = format_nifty_symbol(exp_date, self.pe_strike, OptionType.PE)

        ce_quote = get_live_option_quote(symbol=self.symbol.lower(), strike=self.ce_strike, opt_type="CE")
        pe_quote = get_live_option_quote(symbol=self.symbol.lower(), strike=self.pe_strike, opt_type="PE")

        self.ce_entry_price = float(ce_quote.get("ltp") or ce_quote.get("price") or 25.0)
        self.pe_entry_price = float(pe_quote.get("ltp") or pe_quote.get("price") or 25.0)

        # Fallback safeguard against zero or invalid values
        if self.ce_entry_price <= 0:
            self.ce_entry_price = 25.0
        if self.pe_entry_price <= 0:
            self.pe_entry_price = 25.0

        # 25% Stop Loss per leg
        self.ce_sl_price = round(self.ce_entry_price * 1.25, 2)
        self.pe_sl_price = round(self.pe_entry_price * 1.25, 2)

        lot_sz = getattr(settings, f"{self.symbol}_LOT_SIZE", 65)
        contract_qty = self.lots * lot_sz

        now_ms = int(time.time() * 1000)
        # Place CE Sell Order
        ce_ord = Order(
            order_id=f"THETA_CE_{now_ms}",
            instrument=Instrument(symbol=self.ce_symbol, exchange="NFO", lot_size=lot_sz),
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=contract_qty,
            price=self.ce_entry_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag="THETA_0DTE_CE"
        )
        pe_ord = Order(
            order_id=f"THETA_PE_{now_ms + 1}",
            instrument=Instrument(symbol=self.pe_symbol, exchange="NFO", lot_size=lot_sz),
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

        logger.info(
            f"🚀 [0-DTE THETA STRANGLE ENTERED] Sold {self.ce_symbol} @ ₹{self.ce_entry_price:.2f} "
            f"& {self.pe_symbol} @ ₹{self.pe_entry_price:.2f} (Qty: {contract_qty})"
        )

        if self.telegram:
            tot_prem = self.ce_entry_price + self.pe_entry_price
            target_prem = round(tot_prem * 0.50, 2)
            self.telegram.send_notification(
                f"⏳ <b>0-DTE THETA STRANGLE ENTERED</b>\n\n"
                f"• <b>CE Leg:</b> <code>{self.ce_symbol}</code>\n"
                f"  Sold @ ₹{self.ce_entry_price:.2f} | <b>SL (+25%):</b> ₹{self.ce_sl_price:.2f}\n"
                f"• <b>PE Leg:</b> <code>{self.pe_symbol}</code>\n"
                f"  Sold @ ₹{self.pe_entry_price:.2f} | <b>SL (+25%):</b> ₹{self.pe_sl_price:.2f}\n"
                f"• <b>Lots:</b> {self.lots} ({contract_qty} Qty)\n"
                f"• <b>Combined Premium:</b> ₹{tot_prem:.2f}\n"
                f"• <b>50% Decay Target:</b> ₹{target_prem:.2f}\n"
                f"• <b>Hard Time Exit:</b> 14:45 PM IST"
            )

    def manage_active_strangle(self, current_dt: datetime):
        if not self.in_trade:
            return

        # Fetch current quotes
        ce_q = get_live_option_quote(symbol=self.symbol.lower(), strike=self.ce_strike, opt_type="CE")
        pe_q = get_live_option_quote(symbol=self.symbol.lower(), strike=self.pe_strike, opt_type="PE")

        curr_ce = float(ce_q.get("ltp") or ce_q.get("price") or self.ce_entry_price)
        curr_pe = float(pe_q.get("ltp") or pe_q.get("price") or self.pe_entry_price)

        # 1. Stop Loss Checks (25% expansion on individual legs)
        if self.ce_open and curr_ce >= self.ce_sl_price:
            self._close_leg("CE", curr_ce, "SL HIT (25% Expansion)", current_dt)

        if self.pe_open and curr_pe >= self.pe_sl_price:
            self._close_leg("PE", curr_pe, "SL HIT (25% Expansion)", current_dt)

        # If both legs stopped out, return immediately
        if not self.ce_open and not self.pe_open:
            return

        # 2. Profit Target (50% decay)
        if self.ce_open and self.pe_open:
            comb_entry = self.ce_entry_price + self.pe_entry_price
            comb_curr = curr_ce + curr_pe
            if comb_curr <= (comb_entry * 0.50):
                self.close_all("TARGET ACHIEVED (50% Premium Decayed)", current_dt, exit_ce=curr_ce, exit_pe=curr_pe)
                return
        elif self.ce_open:
            if curr_ce <= (self.ce_entry_price * 0.50):
                self._close_leg("CE", curr_ce, "TARGET ACHIEVED (50% CE Decay)", current_dt)
                return
        elif self.pe_open:
            if curr_pe <= (self.pe_entry_price * 0.50):
                self._close_leg("PE", curr_pe, "TARGET ACHIEVED (50% PE Decay)", current_dt)
                return

        # 3. Time-based Exit at 14:45 PM
        if current_dt.time() >= dtime(14, 45):
            self.close_all("EXPIRY CLOSE (14:45 PM Time Exit)", current_dt, exit_ce=curr_ce, exit_pe=curr_pe)
            return

    def _close_leg(self, leg: str, exit_price: float, reason: str, current_dt: datetime):
        is_ce = (leg == "CE")
        ord_obj = self.ce_order if is_ce else self.pe_order
        if not ord_obj:
            return

        now_ms = int(time.time() * 1000)
        close_ord = Order(
            order_id=f"EX_THETA_{leg}_{now_ms}",
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
        self.daily_pnl = round(self.daily_pnl + pnl, 2)

        if is_ce:
            self.ce_open = False
        else:
            self.pe_open = False

        logger.info(
            f"🏁 [0-DTE {leg} LEG CLOSED: {reason}] Exit: ₹{exit_price:.2f} | Leg PnL: ₹{pnl:+,.2f} | Net Strategy PnL: ₹{self.daily_pnl:+,.2f}"
        )

        if self.telegram:
            pnl_icon = "🟢" if pnl >= 0 else "🔴"
            self.telegram.send_notification(
                f"🏁 <b>0-DTE {leg} LEG CLOSED</b>\n\n"
                f"• <b>Reason:</b> {reason}\n"
                f"• <b>Symbol:</b> <code>{ord_obj.instrument.symbol}</code>\n"
                f"• <b>Entry:</b> ₹{entry_p:.2f} | <b>Exit:</b> ₹{exit_price:.2f}\n"
                f"• <b>Leg PnL:</b> {pnl_icon} <b>₹{pnl:+,.2f}</b>\n"
                f"• <b>Total Theta PnL:</b> <b>₹{self.daily_pnl:+,.2f}</b>"
            )

        if not self.ce_open and not self.pe_open:
            self.in_trade = False
            self.trade_closed_today = True
            self._record_completion(reason, current_dt)

    def close_all(self, reason: str, current_dt: datetime, exit_ce: Optional[float] = None, exit_pe: Optional[float] = None):
        if self.ce_open:
            if exit_ce is None:
                ce_q = get_live_option_quote(symbol=self.symbol.lower(), strike=self.ce_strike, opt_type="CE")
                exit_ce = float(ce_q.get("ltp") or ce_q.get("price") or max(1.0, self.ce_entry_price * 0.5))
            self._close_leg("CE", exit_ce, reason, current_dt)

        if self.pe_open:
            if exit_pe is None:
                pe_q = get_live_option_quote(symbol=self.symbol.lower(), strike=self.pe_strike, opt_type="PE")
                exit_pe = float(pe_q.get("ltp") or pe_q.get("price") or max(1.0, self.pe_entry_price * 0.5))
            self._close_leg("PE", exit_pe, reason, current_dt)

        self.in_trade = False
        self.trade_closed_today = True
        self._record_completion(reason, current_dt)

    def _record_completion(self, reason: str, current_dt: datetime):
        if self._ledger_recorded:
            return
        self._ledger_recorded = True

        if self.telegram:
            pnl_icon = "🟢" if self.daily_pnl >= 0 else "🔴"
            self.telegram.send_notification(
                f"🎯 <b>THETA-0DTE SESSION COMPLETE</b>\n\n"
                f"• <b>Reason:</b> {reason}\n"
                f"• <b>Final PnL:</b> {pnl_icon} <b>₹{self.daily_pnl:+,.2f}</b>\n"
                f"• <b>Capital:</b> ₹{self.broker.initial_capital:,.2f}"
            )

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
            "trade_closed_today": self.trade_closed_today,
            "daily_pnl": self.daily_pnl,
            "ce_symbol": self.ce_symbol,
            "pe_symbol": self.pe_symbol,
            "ce_strike": self.ce_strike,
            "pe_strike": self.pe_strike,
            "ce_entry_price": self.ce_entry_price,
            "pe_entry_price": self.pe_entry_price,
            "ce_sl_price": self.ce_sl_price,
            "pe_sl_price": self.pe_sl_price,
            "ce_open": self.ce_open,
            "pe_open": self.pe_open
        }
