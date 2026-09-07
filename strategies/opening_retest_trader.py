"""
15-Minute Opening Candle Retest Strategy (NIFTY 50 / FINNIFTY / SENSEX).
Strictly disciplined execution:
1. Analyzes the first 15-minute opening candle (09:15 - 09:30 AM).
2. Verifies minimum body conviction (>= 30 pts) and rejection wick guard (< 85% wick).
3. Establishes the 50% retest zone, structural invalidation SL, and Targets 1 & 2.
4. Waits between 09:30 AM and 11:30 AM for a confirmed bounce/rejection candle in the retest zone.
5. Buys 1 lot ATM option with disciplined SL and Target.
6. When Target 1 is reached, ratchets Stop Loss to Breakeven immediately (neutralizing theta decay).
7. Exits full position at Target 2 or Invalidation SL, or squares off at 15:15 PM EOD.
"""

import time
import uuid
from datetime import datetime, time as dtime
from typing import Dict, Any, List, Optional
from pathlib import Path
import pandas as pd

from core.logger import get_logger
from core.models import (
    Instrument, Order, OrderSide, OrderType, OrderStatus, OptionType, Tick
)
from core.option_chain import get_atm_strike, get_next_weekly_expiry, get_next_monthly_expiry
from core.market_data import get_live_option_quote, get_live_nifty_spot
from brokers.base_broker import BaseBroker
from core.risk_manager import RiskManager
from strategies.base_strategy import BaseStrategy
from config.settings import settings
from telegram_bridge.bot import TelegramBridge

logger = get_logger("OpeningRetestTrader")


class OpeningRetestStrategy(BaseStrategy):
    def __init__(
        self,
        broker: BaseBroker,
        risk_manager: RiskManager,
        symbol: str = "NIFTY",
        lots: int = 1,
        min_body_points: float = 30.0,
        retest_leeway: float = 5.0,
        telegram: Optional[TelegramBridge] = None
    ):
        super().__init__(f"Opening 15m Retest ({symbol})", broker, risk_manager)
        self.symbol = symbol.upper()
        self.lots = lots
        self.min_body_points = min_body_points
        self.retest_leeway = retest_leeway
        self.telegram = telegram

        # State machine
        self.reset_daily_state()

    def reset_daily_state(self):
        """Reset internal states for a fresh trading day."""
        self.trade_date: Optional[datetime.date] = None
        self.bars_5m: List[Dict[str, Any]] = []
        self.candle_15m: Optional[Dict[str, Any]] = None
        self.setup_valid: bool = False
        self.setup_side: Optional[str] = None  # "CALL" or "PUT"
        self.retest_min: float = 0.0
        self.retest_max: float = 0.0
        self.invalidation_spot: float = 0.0
        self.target1_spot: float = 0.0
        self.target2_spot: float = 0.0
        self.body_15m: float = 0.0

        # Active trade tracking
        self.in_trade: bool = False
        self.trade_order: Optional[Order] = None
        self.entry_spot: float = 0.0
        self.entry_opt_price: float = 0.0
        self.opt_symbol: str = ""
        self.opt_token: str = ""
        self.opt_strike: int = 0
        self.opt_type: Optional[OptionType] = None
        self.breakeven_ratchet_done: bool = False
        self.trade_closed_today: bool = False
        self.retest_window_expired: bool = False
        self.daily_pnl: float = 0.0

    def initialize(self):
        """Set active state and prepare daily session."""
        self.is_active = True
        self.reset_daily_state()
        logger.info(f"🎯 [{self.name}] Initialized and ready for 09:15 AM market open.")
        if self.telegram:
            self.telegram.send_notification(
                f"🎯 <b>15-Minute Opening Retest Strategy Initialized</b>\n"
                f"• Asset: <b>{self.symbol}</b>\n"
                f"• Lots: <b>{self.lots}</b>\n"
                f"• Min Body Filter: <b>{self.min_body_points} pts</b>\n"
                f"• Rules: Strict 09:30 AM confirmation + 50% retest + Breakeven ratchet"
            )

    def on_tick(self, tick: Tick):
        """Process real-time price updates."""
        if not self.is_active:
            return

        now = tick.timestamp
        current_time = now.time()

        # Check new trading day
        if self.trade_date != now.date():
            self.trade_date = now.date()
            self.reset_daily_state()
            self.trade_date = now.date()

        # If in active trade, monitor stop-loss and targets dynamically
        if self.in_trade:
            self.manage_active_trade(tick.price, now)

    def on_bar(self, bar: Dict[str, Any]):
        """
        Process completed 5-minute bar.
        bar dict contains: {"timestamp", "open", "high", "low", "close", "volume"}
        """
        if not self.is_active:
            return

        ts = bar["timestamp"]
        if isinstance(ts, str):
            ts = pd.to_datetime(ts)
        t = ts.time()

        # Check day reset
        if self.trade_date != ts.date():
            self.trade_date = ts.date()
            self.reset_daily_state()
            self.trade_date = ts.date()

        # Collect first 3 5-minute bars (09:15, 09:20, 09:25)
        if t < dtime(9, 30):
            self.bars_5m.append(bar)
            return

        # At or right after 09:30 AM: Evaluate first 15-minute candle if not evaluated yet
        if not self.candle_15m:
            self.evaluate_opening_15m_candle(ts)

        # Between 09:30 AM and 11:30 AM: Check for retest entry if setup is valid and not already in trade
        if self.setup_valid and not self.in_trade and not self.trade_closed_today:
            if t <= dtime(11, 30):
                self.evaluate_retest_entry(bar, ts)
            else:
                if not self.retest_window_expired:
                    self.retest_window_expired = True
                    logger.info("⏳ 11:30 AM Retest window passed without confirmation. No trade today (Discipline preserved).")
                    if self.telegram:
                        self.telegram.send_notification("⏳ <b>Retest Window Expired (11:30 AM)</b>\nNo confirmed retest pullback occurred. No trade taken today. Capital strictly preserved.")

    def evaluate_opening_15m_candle(self, current_dt: datetime):
        """Constructs and validates the 09:15 - 09:30 AM opening candle."""
        # Fallback if live bars were missing: fetch directly from Angel One historical API
        if len(self.bars_5m) < 3:
            try:
                from scripts.download_candles import fetch_and_save_candles
                df_hist = fetch_and_save_candles(self.symbol, "5m", days_back=2, save_csv=False)
                df_hist["timestamp"] = pd.to_datetime(df_hist["timestamp"])
                today_df = df_hist[df_hist["timestamp"].dt.date == current_dt.date()].sort_values("timestamp")
                if len(today_df) >= 3:
                    self.bars_5m = today_df.iloc[:3].to_dict("records")
            except Exception as e:
                logger.warning(f"Could not fetch today's opening bars via API: {e}")

        if len(self.bars_5m) < 3:
            logger.warning(f"Insufficient bars to construct 15m opening candle (found {len(self.bars_5m)})")
            return

        o15 = self.bars_5m[0]["open"]
        c15 = self.bars_5m[2]["close"]
        h15 = max(b["high"] for b in self.bars_5m[:3])
        l15 = min(b["low"] for b in self.bars_5m[:3])
        body15 = abs(c15 - o15)
        range15 = h15 - l15
        is_green = c15 >= o15

        self.candle_15m = {
            "open": o15, "high": h15, "low": l15, "close": c15,
            "body": body15, "range": range15, "is_green": is_green
        }

        # Filter 1: Minimum body conviction
        if body15 < self.min_body_points:
            logger.info(f"⏭️ 15m candle body ({body15:.1f} pts) < min threshold ({self.min_body_points} pts). Skipping choppy session.")
            if self.telegram:
                self.telegram.send_notification(f"⚠️ <b>15m Opening Candle Choppy</b>\nBody: {body15:.1f} pts (< {self.min_body_points} pts min threshold). No clear directional conviction. Standing aside today.")
            return

        # Filter 2: Rejection wick guard
        upper_wick = h15 - max(o15, c15)
        lower_wick = min(o15, c15) - l15
        if is_green and upper_wick > (body15 * 0.85):
            logger.info(f"⏭️ 15m Green candle rejected at top (upper wick {upper_wick:.1f} > 85% of body). Setup invalid.")
            return
        if not is_green and lower_wick > (body15 * 0.85):
            logger.info(f"⏭️ 15m Red candle rejected at bottom (lower wick {lower_wick:.1f} > 85% of body). Setup invalid.")
            return

        # Valid setup formed! Calculate structural levels
        self.setup_valid = True
        self.setup_side = "CALL" if is_green else "PUT"
        self.body_15m = body15

        # Retest zone (40% to 65% retracement of the opening 15m move)
        retest_mid = o15 + (c15 - o15) * 0.50
        self.retest_min = min(o15, retest_mid)
        self.retest_max = max(o15, retest_mid)

        if is_green:
            self.invalidation_spot = l15 - self.retest_leeway
            self.target1_spot = h15
            self.target2_spot = h15 + (body15 * 0.80)
        else:
            self.invalidation_spot = h15 + self.retest_leeway
            self.target1_spot = l15
            self.target2_spot = l15 - (body15 * 0.80)

        logger.info(
            f"✅ [15m Setup Confirmed] Direction: {self.setup_side} | "
            f"Retest Zone: {self.retest_min:.1f} - {self.retest_max:.1f} | "
            f"SL: {self.invalidation_spot:.1f} | T1: {self.target1_spot:.1f} | T2: {self.target2_spot:.1f}"
        )

        if self.telegram:
            direction_emoji = "🟢 BULLISH CALL" if is_green else "🔴 BEARISH PUT"
            self.telegram.send_notification(
                f"🎯 <b>15-Minute Opening Setup Formed!</b>\n"
                f"• Direction: <b>{direction_emoji}</b>\n"
                f"• 15m Range: <b>{l15:.1f} - {h15:.1f}</b> (Body: {body15:.1f} pts)\n"
                f"• <b>Retest Buy Zone</b>: <code>{self.retest_min:.1f} - {self.retest_max:.1f}</code>\n"
                f"• Target 1: <code>{self.target1_spot:.1f}</code> (Breakeven Lock)\n"
                f"• Target 2: <code>{self.target2_spot:.1f}</code> (Final Take-Profit)\n"
                f"• Invalidation SL: <code>{self.invalidation_spot:.1f}</code>\n\n"
                f"<i>Monitoring for retest bounce between 09:30 AM and 11:30 AM...</i>"
            )

    def evaluate_retest_entry(self, bar: Dict[str, Any], current_dt: datetime):
        """Checks if a 5m bar pulled into the retest zone and bounced."""
        b_low = bar["low"]
        b_high = bar["high"]
        b_close = bar["close"]
        b_open = bar["open"]

        triggered = False

        if self.setup_side == "CALL":
            # Pulled back into retest zone and printed a green bounce candle
            if b_low <= (self.retest_max + self.retest_leeway) and b_high >= self.retest_min:
                if b_close >= b_open:
                    triggered = True
        else:
            # Rallied into retest zone and printed a red rejection candle
            if b_high >= (self.retest_min - self.retest_leeway) and b_low <= self.retest_max:
                if b_close <= b_open:
                    triggered = True

        if triggered:
            self.execute_entry(b_close, current_dt)

    def execute_entry(self, current_spot: float, current_dt: datetime):
        """Executes ATM option buying order with predefined SL and Target."""
        # Risk check
        if self.risk_manager.kill_switch_active:
            logger.warning(f"RMS blocked trade: {self.risk_manager.kill_switch_reason}")
            return

        self.entry_spot = current_spot
        strike_step = 50 if self.symbol in ["NIFTY", "FINNIFTY"] else 100
        self.opt_strike = get_atm_strike(current_spot, strike_step)
        self.opt_type = OptionType.CE if self.setup_side == "CALL" else OptionType.PE

        # Determine expiry (Weekly Tuesday for Nifty, Weekly Thursday for Sensex, Monthly for FinNifty/BankNifty)
        if self.symbol == "NIFTY":
            expiry_date = get_next_weekly_expiry(current_dt.date(), weekday=1)
        elif self.symbol == "SENSEX":
            expiry_date = get_next_weekly_expiry(current_dt.date(), weekday=3)
        else:
            expiry_date = get_next_monthly_expiry(current_dt.date(), weekday=1)

        # Fetch live quote or simulate option price
        opt_type_str = self.opt_type.value if hasattr(self.opt_type, "value") else str(self.opt_type)
        quote = get_live_option_quote(symbol=self.symbol.lower(), strike=self.opt_strike, opt_type=opt_type_str)
        if quote and quote.get("price", 0) > 0:
            self.entry_opt_price = quote["price"]
            self.opt_symbol = quote.get("symbol", f"{self.symbol}_{self.opt_strike}_{self.opt_type.value}")
            self.opt_token = str(quote.get("token", ""))
        else:
            # Theoretical fallback
            self.entry_opt_price = 100.0
            self.opt_symbol = f"{self.symbol}{expiry_date.strftime('%y%m%d')}{self.opt_strike}{self.opt_type.value}"
            self.opt_token = "12345"

        # Calculate lot quantity
        lot_sizes = {"NIFTY": 65, "FINNIFTY": 40, "BANKNIFTY": 15, "SENSEX": 10}
        contract_qty = lot_sizes.get(self.symbol, 65) * self.lots

        # Place Order via Broker
        inst = Instrument(
            symbol=self.opt_symbol,
            exchange="BFO" if self.symbol == "SENSEX" else "NFO",
            strike=float(self.opt_strike),
            expiry=expiry_date,
            option_type=self.opt_type,
            lot_size=contract_qty
        )
        order = Order(
            order_id=f"OR_{int(time.time())}",
            instrument=inst,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=contract_qty,
            price=self.entry_opt_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag="OPENING_RETEST"
        )

        placed = self.broker.place_order(order)
        if placed and placed.status in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.FILLED]:
            self.in_trade = True
            self.trade_order = placed
            logger.info(f"🚀 [ORDER FILLED] Bought {self.opt_symbol} @ ₹{self.entry_opt_price:.2f} (Qty: {contract_qty})")

            if self.telegram:
                self.telegram.send_notification(
                    f"🚀 <b>TRADE ENTERED! (Opening Retest)</b>\n"
                    f"• Instrument: <b>{self.opt_symbol}</b>\n"
                    f"• Entry Premium: <b>₹{self.entry_opt_price:.2f}</b> (Spot: {current_spot:.1f})\n"
                    f"• Quantity: <b>{contract_qty} ({self.lots} Lot)</b>\n"
                    f"• Invalidation SL: <b>{self.invalidation_spot:.1f}</b>\n"
                    f"• Target 1 (Breakeven Trigger): <b>{self.target1_spot:.1f}</b>\n"
                    f"• Target 2 (Take Profit): <b>{self.target2_spot:.1f}</b>"
                )

    def manage_active_trade(self, current_spot: float, current_dt: datetime):
        """Manages active trade: Breakeven Ratchet at Target 1, Exit at Target 2 or Invalidation SL."""
        if not self.in_trade or not self.trade_order:
            return

        is_call = self.setup_side == "CALL"
        spot_move = (current_spot - self.entry_spot) if is_call else (self.entry_spot - current_spot)
        spot_adverse = (self.entry_spot - current_spot) if is_call else (current_spot - self.entry_spot)

        target1_dist = abs(self.target1_spot - self.entry_spot)
        target2_dist = abs(self.target2_spot - self.entry_spot)
        sl_dist = abs(self.invalidation_spot - self.entry_spot)

        # 1. Check Breakeven Ratchet (Target 1 Reached)
        if spot_move >= target1_dist and not self.breakeven_ratchet_done:
            self.breakeven_ratchet_done = True
            logger.info(f"🔒 [Target 1 Touched ({self.target1_spot:.1f})] Ratcheting Stop Loss to Breakeven @ Spot {self.entry_spot:.1f}!")
            if self.telegram:
                self.telegram.send_notification(
                    f"🔒 <b>TARGET 1 REACHED! ({self.target1_spot:.1f})</b>\n"
                    f"Stop Loss has been ratcheted to <b>BREAKEVEN (₹{self.entry_opt_price:.2f})</b>.\n"
                    f"This trade is now 100% RISK-FREE. Riding towards Target 2 (<code>{self.target2_spot:.1f}</code>)."
                )

        # 2. Check Target 2 Achieved (Take Profit)
        if spot_move >= target2_dist:
            self.close_trade("TARGET 2 (Take Profit)", current_spot, current_dt)
            return

        # 3. Check Stop Loss Hit
        if self.breakeven_ratchet_done:
            # Trailed at entry spot
            if (is_call and current_spot <= self.entry_spot) or (not is_call and current_spot >= self.entry_spot):
                self.close_trade("BREAKEVEN EXIT (Risk Neutralized)", current_spot, current_dt)
                return
        else:
            # Initial structural invalidation SL
            if (is_call and current_spot <= self.invalidation_spot) or (not is_call and current_spot >= self.invalidation_spot):
                self.close_trade("STOP LOSS HIT", current_spot, current_dt)
                return

        # 4. EOD Square-Off (15:15 PM)
        if current_dt.time() >= dtime(15, 15):
            self.close_trade("EOD SQUARE-OFF (15:15 PM)", current_spot, current_dt)
            return

    def close_trade(self, reason: str, exit_spot: float, current_dt: datetime):
        """Closes active trade position."""
        if not self.in_trade or not self.trade_order:
            return

        # Fetch exit option price
        opt_exit_price = self.entry_opt_price
        if self.opt_token:
            opt_type_str = self.opt_type.value if hasattr(self.opt_type, "value") else str(self.opt_type)
            quote = get_live_option_quote(symbol=self.symbol.lower(), strike=self.opt_strike, opt_type=opt_type_str)
            if quote and quote.get("price", 0) > 0:
                opt_exit_price = quote["price"]
            else:
                # Approximate from spot delta
                delta = 0.65 if "TARGET" in reason else 0.45
                spot_pts = (exit_spot - self.entry_spot) if self.setup_side == "CALL" else (self.entry_spot - exit_spot)
                opt_exit_price = max(1.0, self.entry_opt_price + (spot_pts * delta))

        # Close position via broker
        exit_order = Order(
            order_id=f"EX_{int(time.time())}",
            instrument=self.trade_order.instrument,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=self.trade_order.quantity,
            price=opt_exit_price,
            status=OrderStatus.PENDING,
            placed_at=current_dt,
            tag="OPENING_RETEST_EXIT"
        )
        self.broker.place_order(exit_order)

        net_pnl = (opt_exit_price - self.entry_opt_price) * self.trade_order.quantity
        self.daily_pnl += net_pnl
        self.risk_manager.evaluate_daily_pnl(self.daily_pnl)

        logger.info(f"🏁 [TRADE CLOSED: {reason}] PnL: ₹{net_pnl:+,.2f} (Entry: ₹{self.entry_opt_price:.2f} -> Exit: ₹{opt_exit_price:.2f})")

        if self.telegram:
            emoji = "🏆" if net_pnl > 0 else "🛑"
            self.telegram.send_notification(
                f"{emoji} <b>TRADE CLOSED ({reason})</b>\n"
                f"• Instrument: <b>{self.opt_symbol}</b>\n"
                f"• Entry: <b>₹{self.entry_opt_price:.2f}</b> | Exit: <b>₹{opt_exit_price:.2f}</b>\n"
                f"• Net P&L: <b>₹{net_pnl:+,.2f}</b>\n"
                f"• Today's Account P&L: <b>₹{self.daily_pnl:+,.2f}</b>"
            )

        self.in_trade = False
        self.trade_order = None
        self.trade_closed_today = True

    def check_entry_conditions(self, current_dt: datetime, spot_price: float):
        pass

    def check_exit_conditions(self, current_dt: datetime):
        pass

    def get_status(self) -> Dict[str, Any]:
        """Status for terminal UI / Streamlit dashboard."""
        return {
            "strategy": self.name,
            "setup_valid": self.setup_valid,
            "setup_side": self.setup_side,
            "retest_zone": f"{self.retest_min:.1f} - {self.retest_max:.1f}" if self.setup_valid else "Waiting 09:30 AM",
            "in_trade": self.in_trade,
            "breakeven_active": self.breakeven_ratchet_done,
            "daily_pnl": self.daily_pnl
        }
