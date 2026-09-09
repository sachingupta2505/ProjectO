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

import math
import time
import uuid
from datetime import datetime, timedelta, time as dtime
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
        retrace_low: float = 0.50,
        retrace_high: float = 1.00,
        confirmation_body_ratio: float = 0.0,
        entry_cutoff: str = "11:30",
        version: str = "ORION-15",
        risk_per_trade: Optional[float] = None,
        max_lots: Optional[int] = None,
        telegram: Optional[TelegramBridge] = None
    ):
        if not 0.0 <= retrace_low <= retrace_high <= 1.0:
            raise ValueError("Retracement bounds must satisfy 0 <= low <= high <= 1")
        if confirmation_body_ratio < 0.0:
            raise ValueError("Confirmation body ratio cannot be negative")
        try:
            self.entry_cutoff = datetime.strptime(entry_cutoff, "%H:%M").time()
        except ValueError as exc:
            raise ValueError("Entry cutoff must use HH:MM format") from exc

        super().__init__(f"{version} ({symbol})", broker, risk_manager)
        self.symbol = symbol.upper()
        self.lots = lots
        self.min_body_points = min_body_points
        self.retest_leeway = retest_leeway
        self.retrace_low = retrace_low
        self.retrace_high = retrace_high
        self.confirmation_body_ratio = confirmation_body_ratio
        self.version = version
        self.risk_per_trade = risk_per_trade
        self.max_lots = max_lots if max_lots is not None else lots
        if self.max_lots < 1:
            raise ValueError("max_lots must be at least one")
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
        self.opening_decision_reason: str = "Opening candle has not been evaluated yet."
        self.last_exit_reason: str = ""
        self.sized_lots: int = 0
        self.estimated_loss_per_lot: float = 0.0
        self.opening_fetch_attempts: int = 0
        self.next_opening_fetch_retry_at: Optional[datetime] = None
        self.opening_fetch_final_failure: bool = False
        self.opening_data_pending: bool = False

    def initialize(self):
        """Set active state and prepare daily session."""
        self.is_active = True
        self.reset_daily_state()
        logger.info(f"🎯 [{self.name}] Initialized and ready for 09:15 AM market open.")

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
        if (
            not self.candle_15m
            and not self.opening_fetch_final_failure
            and (self.next_opening_fetch_retry_at is None or ts >= self.next_opening_fetch_retry_at)
        ):
            self.evaluate_opening_15m_candle(ts)

        # The 09:30 timestamp represents the opening range decision point, not
        # a completed post-opening confirmation candle.  Start retest entries
        # with the next completed five-minute candle so the opening bar cannot
        # be accidentally reused as its own confirmation.
        if t <= dtime(9, 30):
            return

        # Between 09:30 AM and 11:30 AM: Check for retest entry if setup is valid and not already in trade
        if self.setup_valid and not self.in_trade and not self.trade_closed_today:
            if t <= self.entry_cutoff:
                self.evaluate_retest_entry(bar, ts)
            else:
                if not self.retest_window_expired:
                    self.retest_window_expired = True
                    cutoff_label = self.entry_cutoff.strftime("%H:%M")
                    logger.info(f"⏳ {cutoff_label} AM Retest window passed without confirmation. No trade today (Discipline preserved).")

    def evaluate_opening_15m_candle(self, current_dt: datetime):
        """Constructs and validates the 09:15 - 09:30 AM opening candle."""
        # Runtime bars are formed from tick data and may have started before a
        # verified feed was available. At the decision point, replace them with
        # completed exchange candles. Unit/backtest bars intentionally omit this
        # marker and remain fully deterministic.
        runtime_bars = any("data_source" in bar or "is_live" in bar for bar in self.bars_5m)
        should_fetch_exchange_bars = runtime_bars or len(self.bars_5m) < 3
        opening_ohlc = None
        if should_fetch_exchange_bars:
            try:
                self.opening_fetch_attempts += 1
                from scripts.download_candles import fetch_and_save_candles
                # Angel publishes a native 15-minute interval. It is the primary
                # source because ORION's setup is defined from one 09:15–09:30
                # candle. A verified 5-minute reconstruction remains a fallback
                # for delayed/missing 15-minute publication.
                df_15m = fetch_and_save_candles(self.symbol, "15m", days_back=2, save_csv=False)
                if not df_15m.empty:
                    df_15m["timestamp"] = pd.to_datetime(df_15m["timestamp"])
                    today_15m = df_15m[df_15m["timestamp"].dt.date == current_dt.date()].sort_values("timestamp")
                    if not today_15m.empty:
                        row = today_15m.iloc[0]
                        opening_ohlc = {
                            "open": float(row["open"]), "high": float(row["high"]),
                            "low": float(row["low"]), "close": float(row["close"]),
                            "source": "Angel 15-minute",
                        }

                if opening_ohlc is None:
                    df_5m = fetch_and_save_candles(self.symbol, "5m", days_back=2, save_csv=False)
                    if not df_5m.empty:
                        df_5m["timestamp"] = pd.to_datetime(df_5m["timestamp"])
                        today_5m = df_5m[df_5m["timestamp"].dt.date == current_dt.date()].sort_values("timestamp")
                        if len(today_5m) >= 3:
                            self.bars_5m = today_5m.iloc[:3].to_dict("records")
                            opening_ohlc = {
                                "open": float(self.bars_5m[0]["open"]),
                                "high": max(float(b["high"]) for b in self.bars_5m[:3]),
                                "low": min(float(b["low"]) for b in self.bars_5m[:3]),
                                "close": float(self.bars_5m[2]["close"]),
                                "source": "Angel 5-minute reconstruction",
                            }
                if opening_ohlc is None:
                    raise RuntimeError("Angel has not published a completed 09:15–09:30 opening candle yet")
            except Exception as e:
                self.opening_data_pending = self.opening_fetch_attempts < 3
                if self.opening_data_pending:
                    self.next_opening_fetch_retry_at = current_dt + timedelta(minutes=5)
                    self.opening_decision_reason = (
                        "Waiting for Angel's verified opening candle; "
                        f"retry {self.opening_fetch_attempts + 1}/3 at "
                        f"{self.next_opening_fetch_retry_at.strftime('%H:%M')} IST."
                    )
                    logger.warning("ORION opening candle pending; %s (%s)", self.opening_decision_reason, e)
                else:
                    self.opening_fetch_final_failure = True
                    self.opening_decision_reason = "Verified Angel opening data was unavailable after three attempts; no trade for data safety."
                    logger.error("ORION opening setup withheld after three authoritative Angel-candle attempts (%s)", e)
                return

        if opening_ohlc is None:
            if len(self.bars_5m) < 3:
                self.opening_decision_reason = "Fewer than three completed opening candles were available."
                logger.warning("Insufficient bars to construct 15m opening candle (found %s)", len(self.bars_5m))
                return
            opening_ohlc = {
                "open": float(self.bars_5m[0]["open"]),
                "high": max(float(b["high"]) for b in self.bars_5m[:3]),
                "low": min(float(b["low"]) for b in self.bars_5m[:3]),
                "close": float(self.bars_5m[2]["close"]),
                "source": "supplied bars",
            }

        self.opening_data_pending = False
        self.next_opening_fetch_retry_at = None
        o15 = opening_ohlc["open"]
        c15 = opening_ohlc["close"]
        h15 = opening_ohlc["high"]
        l15 = opening_ohlc["low"]
        body15 = abs(c15 - o15)
        range15 = h15 - l15
        is_green = c15 >= o15

        self.candle_15m = {
            "open": o15, "high": h15, "low": l15, "close": c15,
            "body": body15, "range": range15, "is_green": is_green,
            "source": opening_ohlc["source"],
        }

        logger.info(
            "ORION opening candle [%s]: O=%.2f H=%.2f L=%.2f C=%.2f | body=%.2f",
            self.candle_15m["source"], o15, h15, l15, c15, body15,
        )

        # Filter 1: Minimum body conviction
        if body15 < self.min_body_points:
            self.opening_decision_reason = (
                f"Opening body was {body15:.1f} points, below the {self.min_body_points:.1f}-point minimum."
            )
            logger.info(f"⏭️ 15m candle body ({body15:.1f} pts) < min threshold ({self.min_body_points} pts). Skipping choppy session.")
            return

        # Filter 2: Rejection wick guard
        upper_wick = h15 - max(o15, c15)
        lower_wick = min(o15, c15) - l15
        if is_green and upper_wick > (body15 * 0.85):
            self.opening_decision_reason = "Bullish opening candle had an excessive upper rejection wick."
            logger.info(f"⏭️ 15m Green candle rejected at top (upper wick {upper_wick:.1f} > 85% of body). Setup invalid.")
            return
        if not is_green and lower_wick > (body15 * 0.85):
            self.opening_decision_reason = "Bearish opening candle had an excessive lower rejection wick."
            logger.info(f"⏭️ 15m Red candle rejected at bottom (lower wick {lower_wick:.1f} > 85% of body). Setup invalid.")
            return

        # Valid setup formed! Calculate structural levels
        self.setup_valid = True
        self.opening_decision_reason = "Opening structure qualifies; awaiting the retest and confirmation."
        self.setup_side = "CALL" if is_green else "PUT"
        self.body_15m = body15

        # Retest zone (40% to 65% retracement of the opening 15m move)
        # The defaults preserve the original deep 50%-100% retracement. ORION
        # 2.0 uses the more selective 35%-65% continuation retracement.
        if is_green:
            self.retest_min = c15 - (self.retrace_high * body15)
            self.retest_max = c15 - (self.retrace_low * body15)
        else:
            self.retest_min = c15 + (self.retrace_low * body15)
            self.retest_max = c15 + (self.retrace_high * body15)

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


    def evaluate_retest_entry(self, bar: Dict[str, Any], current_dt: datetime):
        """Checks if a 5m bar pulled into the retest zone and bounced."""
        b_low = bar["low"]
        b_high = bar["high"]
        b_close = bar["close"]
        b_open = bar["open"]

        triggered = False
        confirmation_body = abs(b_close - b_open)
        body_confirmed = confirmation_body >= (self.body_15m * self.confirmation_body_ratio)

        if self.setup_side == "CALL":
            # Pulled back into retest zone and printed a green bounce candle
            if b_low <= (self.retest_max + self.retest_leeway) and b_high >= self.retest_min:
                if b_close >= b_open and body_confirmed:
                    triggered = True
        else:
            # Rallied into retest zone and printed a red rejection candle
            if b_high >= (self.retest_min - self.retest_leeway) and b_low <= self.retest_max:
                if b_close <= b_open and body_confirmed:
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

        # Calculate a stop-based quantity.  The live feed does not expose a
        # reliable option-at-stop quote, so use a deliberately conservative
        # delta estimate and a 10-point minimum premium stop.  If even one lot
        # exceeds the risk budget, skip the trade rather than over-size it.
        lot_sizes = {"NIFTY": 65, "FINNIFTY": 40, "BANKNIFTY": 15, "SENSEX": 10}
        lot_size = lot_sizes.get(self.symbol, 65)
        structural_stop_distance = abs(current_spot - self.invalidation_spot)
        estimated_option_stop_points = max(10.0, structural_stop_distance * 0.50)
        self.estimated_loss_per_lot = round(estimated_option_stop_points * lot_size + 55.0, 2)
        if self.risk_per_trade is None:
            self.sized_lots = self.max_lots
        else:
            self.sized_lots = min(self.max_lots, math.floor(self.risk_per_trade / self.estimated_loss_per_lot))
        if self.sized_lots < 1:
            self.opening_decision_reason = (
                f"Skipped: estimated one-lot loss of ₹{self.estimated_loss_per_lot:,.0f} exceeds "
                f"the ₹{self.risk_per_trade:,.0f} risk budget."
            )
            logger.warning(self.opening_decision_reason)
            return
        contract_qty = lot_size * self.sized_lots

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
            logger.info(
                f"🚀 [ORDER FILLED] Bought {self.opt_symbol} @ ₹{self.entry_opt_price:.2f} "
                f"(Qty: {contract_qty}; {self.sized_lots} lot(s); estimated risk ₹{self.estimated_loss_per_lot * self.sized_lots:,.0f})"
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

        # Record trade into ORION Strategy Ledger
        try:
            dur_mins = round((current_dt - self.trade_order.placed_at).total_seconds() / 60.0, 1) if hasattr(self.trade_order, "placed_at") else 0.0
            from core.strategy_ledger import strategy_ledger
            strategy_ledger.record_trade("orion", {
                "trade_id": f"ORION_{int(time.time())}",
                "strategy": "ORION-15",
                "index": self.symbol,
                "date": current_dt.strftime("%Y-%m-%d"),
                "side": self.setup_side,
                "opening_body_pts": round(self.body_15m, 1),
                "retest_zone": f"{self.retest_min:.1f} - {self.retest_max:.1f}",
                "invalidation_sl": round(self.invalidation_spot, 1),
                "target1": round(self.target1_spot, 1),
                "target2": round(self.target2_spot, 1),
                "entry_time": self.trade_order.placed_at.strftime("%H:%M:%S") if hasattr(self.trade_order, "placed_at") else "",
                "entry_spot": round(self.entry_spot, 1),
                "entry_premium": round(self.entry_opt_price, 2),
                "exit_time": current_dt.strftime("%H:%M:%S"),
                "exit_spot": round(exit_spot, 1),
                "exit_premium": round(opt_exit_price, 2),
                "reason": reason,
                "breakeven_triggered": self.breakeven_ratchet_done,
                "quantity": self.trade_order.quantity,
                "net_pnl": round(net_pnl, 2),
                "duration_mins": dur_mins,
                "mode": "PAPER",
                "notes": f"Paper trial. Closed via {reason}."
            })
        except Exception as e:
            logger.warning(f"Failed to record trade to strategy ledger: {e}")

        logger.info(f"🏁 [TRADE CLOSED: {reason}] PnL: ₹{net_pnl:+,.2f} (Entry: ₹{self.entry_opt_price:.2f} -> Exit: ₹{opt_exit_price:.2f})")
        self.last_exit_reason = reason

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
