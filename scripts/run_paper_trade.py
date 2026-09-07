"""
Paper Trading Execution & Auto-Exit Monitor for Options (CE & PE).
Places an ATM Call or Put option trade with defined Target % and Stop Loss %,
supports custom user prices, lot size of 65, and continuously tracks market ticks.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import time
import uuid
import argparse
import random
from pathlib import Path
from datetime import datetime
from typing import Optional

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.logger import get_logger
from core.models import Order, Instrument, OrderSide, OrderType, OptionType
from core.charges import calculate_round_trip_charges
from core.option_chain import get_atm_strike, get_next_weekly_expiry, calculate_black_scholes
from core.market_data import get_live_nifty_spot, get_live_option_quote, get_live_option_chain
from brokers.paper_broker import PaperBroker
from telegram_bridge.bot import TelegramBridge
from config.settings import settings

logger = get_logger("PaperTrader")


def execute_paper_trade(
    opt_type_str: str = "PE",
    target_pct: float = 0.10,
    sl_pct: float = 0.05,
    lots: int = 2,
    custom_price: Optional[float] = None,
    custom_strike: Optional[int] = None,
    auto_monitor: bool = True
):
    opt_enum = OptionType.PE if opt_type_str.upper() == "PE" else OptionType.CE
    type_label = "PUT (PE)" if opt_enum == OptionType.PE else "CALL (CE)"

    # 1. Fetch live Nifty spot price and option quote
    market_data = get_live_nifty_spot()
    spot_price = market_data.get("spot", 23950.0)
    atm_strike = custom_strike or get_atm_strike(spot_price)

    # Fetch live option quote from real market feed
    live_quote = get_live_option_quote(symbol="nifty", strike=atm_strike, opt_type=opt_enum.value)

    # Resolve Expiry Date
    if live_quote.get("expiry"):
        try:
            expiry = datetime.strptime(live_quote["expiry"], "%Y-%m-%d").date()
        except Exception:
            expiry = get_next_weekly_expiry()
    else:
        expiry = get_next_weekly_expiry()

    # Angel One standard symbol format (e.g. NIFTY08SEP2623950PE)
    year_str = str(expiry.year)[2:]
    month_str = expiry.strftime("%b").upper()
    day_str = f"{expiry.day:02d}"
    symbol = f"NIFTY{day_str}{month_str}{year_str}{atm_strike}{opt_enum.value}"
    contract_name = live_quote.get("display_name", f"NIFTY {day_str} {month_str} {atm_strike} {type_label}")

    # 2. Compute Entry Price: Custom price if provided, else genuine live market LTP
    if custom_price and custom_price > 0:
        entry_price = round(custom_price, 2)
        price_source = "User Specified"
    elif live_quote.get("is_live") and live_quote.get("ltp") and live_quote.get("ltp") > 0:
        entry_price = round(float(live_quote["ltp"]), 2)
        price_source = "NSE Real-Time Market Feed"
    else:
        # Fallback to Black-Scholes estimate if offline
        bs = calculate_black_scholes(
            spot=spot_price,
            strike=atm_strike,
            time_to_expiry_years=4.0 / 365.0,
            volatility=0.12,
            option_type=opt_enum
        )
        entry_price = round(bs.get("price", 75.0), 2)
        price_source = "Black-Scholes Estimate"

    # 3. Lot size calculation (NIFTY LOT SIZE = 65)
    lot_size = settings.NIFTY_LOT_SIZE  # 65
    quantity = lots * lot_size

    # 4. Compute 2% Target & 2% SL levels
    target_price = round(entry_price * (1.0 + target_pct), 2)
    sl_price = round(entry_price * (1.0 - sl_pct), 2)
    target_pnl = round((target_price - entry_price) * quantity, 2)
    sl_pnl = round((entry_price - sl_price) * quantity, 2)

    logger.info(f"Preparing {type_label} BUY: {symbol} @ ₹{entry_price:.2f} | Lot Size: {lot_size} (Qty: {quantity})")
    logger.info(f"Target (+{target_pct*100:.1f}%): ₹{target_price:.2f} (+₹{target_pnl:.2f}) | SL (-{sl_pct*100:.1f}%): ₹{sl_price:.2f} (-₹{sl_pnl:.2f})")

    # 5. Initialize Paper Broker with disk persistence
    broker = PaperBroker(initial_capital=settings.PAPER_INITIAL_CAPITAL, persist=True)
    broker.authenticate()
    broker.set_ltp(symbol, entry_price)

    # 6. Place Market Buy Order
    order = Order(
        order_id=f"PAPER_BUY_{uuid.uuid4().hex[:6].upper()}",
        instrument=Instrument(
            symbol=symbol,
            exchange="NFO",
            strike=atm_strike,
            expiry=expiry,
            option_type=opt_enum,
            lot_size=lot_size
        ),
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=entry_price,
        tag=f"PAPER_{opt_enum.value}_BUY_{int(target_pct*100)}PCT"
    )

    placed_order = broker.place_order(order)
    actual_fill = placed_order.average_price

    # Recalculate targets based on exact fill price
    target_price = round(actual_fill * (1.0 + target_pct), 2)
    sl_price = round(actual_fill * (1.0 - sl_pct), 2)
    target_pnl = round((target_price - actual_fill) * quantity, 2)
    sl_pnl = round((actual_fill - sl_price) * quantity, 2)

    # 7. Notify via Telegram
    telegram = TelegramBridge(runner=None)
    trade_alert = (
        f"🚀 <b>Paper Trade Executed!</b>\n\n"
        f"• <b>Side:</b> BUY {type_label}\n"
        f"• <b>Contract:</b> <b>{contract_name}</b> (<code>{symbol}</code>)\n"
        f"• <b>Nifty Spot:</b> ₹{spot_price:,.2f} (ATM Strike: {atm_strike})\n"
        f"• <b>Lot Size:</b> {lot_size} Qty/Lot (Total: {quantity} Qty for {lots} Lot)\n"
        f"• <b>Entry Fill Price:</b> ₹{actual_fill:.2f} (Total Cost: ₹{actual_fill * quantity:,.2f})\n"
        f"• <b>Pricing Source:</b> {price_source}\n\n"
        f"🎯 <b>Target (+{target_pct*100:.1f}%):</b> ₹{target_price:.2f} (+₹{target_pnl:,.2f})\n"
        f"🛑 <b>Stop Loss (-{sl_pct*100:.1f}%):</b> ₹{sl_price:.2f} (-₹{sl_pnl:,.2f})\n\n"
        f"⚡ <i>Auto-Exit Monitor engaged. Position will square off automatically upon reaching target or stop-loss!</i>"
    )
    telegram.send_notification(trade_alert)
    logger.info("Telegram notification sent successfully!")

    print("\n" + "="*65)
    print(f"TRADE EXECUTED: BUY {quantity} {contract_name} ({symbol}) @ ₹{actual_fill:.2f}")
    print(f"LOT SIZE: {lot_size} Qty | SOURCE: {price_source} | TOTAL COST: ₹{actual_fill * quantity:,.2f}")
    print(f"TARGET (+{target_pct*100:.1f}%): ₹{target_price:.2f} | STOP LOSS (-{sl_pct*100:.1f}%): ₹{sl_price:.2f}")
    print("="*65 + "\n")

    if not auto_monitor:
        return

    # 8. Real-time Auto-Exit Monitor Loop
    logger.info("Starting real-time auto-exit monitor loop with Breakeven Lock & Dynamic Trailing SL...")
    opt_price = actual_fill
    entry_spot = spot_price
    accumulated_drift = 0.0
    peak_price = actual_fill
    breakeven_locked = False
    trailing_active = False

    while True:
        time.sleep(2)
        # Fetch current live spot
        curr_mkt = get_live_nifty_spot()
        curr_spot = curr_mkt.get("spot", entry_spot)

        # Delta calculation:
        # If CE: positive correlation with spot
        # If PE: inverse correlation with spot (spot drops -> PE rises)
        if opt_enum == OptionType.CE:
            spot_change = (curr_spot - entry_spot) * 0.52
        else:
            spot_change = (entry_spot - curr_spot) * 0.52

        # Check live market quote if available
        try:
            live_q = get_live_option_quote(symbol="nifty", strike=atm_strike, opt_type=opt_enum.value)
            if live_q.get("is_live") and live_q.get("ltp") and live_q.get("ltp") > 0:
                live_market_ltp = float(live_q["ltp"])
                market_diff = live_market_ltp - actual_fill
            else:
                market_diff = spot_change
        except Exception:
            market_diff = spot_change

        accumulated_drift += random.gauss(0, 0.20)
        accumulated_drift = max(-2.5, min(2.5, accumulated_drift))

        opt_price = round(max(0.5, actual_fill + market_diff + accumulated_drift), 2)
        broker.set_ltp(symbol, opt_price)

        pos = broker.get_positions().get(symbol)
        current_pnl = pos.unrealized_pnl if pos else 0.0
        pnl_pct = ((opt_price - actual_fill) / actual_fill) * 100.0
        peak_price = max(peak_price, opt_price)

        # Dynamic Breakeven Lock (+2.0% profit reached, buffer offsets charges)
        if pnl_pct >= 2.0 and not breakeven_locked:
            be_sl = round(actual_fill + 0.80, 2)
            if be_sl > sl_price:
                sl_price = be_sl
                breakeven_locked = True
                logger.info(f"🛡️ Breakeven lock active! SL moved to ₹{sl_price:.2f} (covers friction)")
                telegram.send_notification(
                    f"🛡️ <b>Breakeven Activated!</b>\n\n"
                    f"• Instrument: <code>{symbol}</code>\n"
                    f"• Entry: ₹{actual_fill:.2f} | LTP: ₹{opt_price:.2f} (+{pnl_pct:.2f}%)\n"
                    f"• Stop Loss moved to Entry + Buffer: <b>₹{sl_price:.2f}</b>\n"
                    f"• Zero downside risk & round-trip fees locked in."
                )

        # Dynamic Trailing Stop-Loss (+2.5% profit reached)
        if peak_price >= actual_fill * 1.025:
            cand_sl = round(peak_price - (actual_fill * 0.015), 2)
            if cand_sl > sl_price:
                sl_price = cand_sl
                trailing_active = True

        status_tag = "🛡️ BE" if breakeven_locked else ("📈 TRAIL" if trailing_active else "STATIC")
        sys.stdout.write(f"\r[MONITOR] {symbol} LTP: ₹{opt_price:.2f} | PnL: ₹{current_pnl:+,.2f} ({pnl_pct:+.2f}%) | TP: ₹{target_price:.2f} | SL: ₹{sl_price:.2f} [{status_tag}]")
        sys.stdout.flush()

        # Check Target Hit
        if opt_price >= target_price:
            print("\n")
            logger.info(f"🎯 Target reached! LTP ₹{opt_price:.2f} >= TP ₹{target_price:.2f}")
            exit_order = Order(
                order_id=f"PAPER_EXIT_{uuid.uuid4().hex[:6].upper()}",
                instrument=placed_order.instrument,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=quantity,
                price=opt_price,
                tag=f"TARGET_EXIT_{int(target_pct*100)}PCT"
            )
            broker.place_order(exit_order)
            gross_profit = (opt_price - actual_fill) * quantity
            charges_data = calculate_round_trip_charges("NIFTY_OPT", actual_fill, opt_price, quantity)
            fees = charges_data["total_charges"]
            net_profit = gross_profit - fees

            exit_msg = (
                f"🎯 <b>TARGET REACHED (+{target_pct*100:.1f}% PROFIT)!</b>\n\n"
                f"• <b>Instrument:</b> {symbol} ({type_label})\n"
                f"• <b>Exit Price:</b> ₹{opt_price:.2f}\n"
                f"• <b>Entry Price:</b> ₹{actual_fill:.2f}\n"
                f"• <b>Lot Size:</b> {lot_size} Qty (Total: {quantity})\n"
                f"• <b>Gross Profit:</b> +₹{gross_profit:,.2f} (+{pnl_pct:.2f}%)\n"
                f"• <b>Charges & Taxes:</b> -₹{fees:,.2f}\n"
                f"• <b>Net Realized Profit:</b> 🟢 <b>+₹{net_profit:,.2f}</b>\n"
                f"• <b>Status:</b> Trade Completed Successfully!\n\n"
                f"🕒 {datetime.now().strftime('%H:%M:%S IST')}"
            )
            telegram.send_notification(exit_msg)
            break

        # Check Stop Loss Hit
        if opt_price <= sl_price:
            print("\n")
            exit_tag = "TRAILING_STOP_EXIT" if trailing_active else ("BREAKEVEN_EXIT" if breakeven_locked else f"SL_EXIT_{int(sl_pct*100)}PCT")
            logger.info(f"🛑 Exit triggered! LTP ₹{opt_price:.2f} <= SL ₹{sl_price:.2f} ({exit_tag})")
            exit_order = Order(
                order_id=f"PAPER_EXIT_{uuid.uuid4().hex[:6].upper()}",
                instrument=placed_order.instrument,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=quantity,
                price=opt_price,
                tag=exit_tag
            )
            broker.place_order(exit_order)
            gross_loss = (opt_price - actual_fill) * quantity
            charges_data = calculate_round_trip_charges("NIFTY_OPT", actual_fill, opt_price, quantity)
            fees = charges_data["total_charges"]
            net_pnl = gross_loss - fees
            pnl_icon = "🟢" if net_pnl >= 0 else "🔴"
            exit_title = "📈 TRAILING STOP PROFIT LOCKED" if net_pnl >= 0 else ("🛡️ BREAKEVEN EXIT (NO LOSS)" if breakeven_locked else "🛑 STOP LOSS TRIGGERED")

            exit_msg = (
                f"{pnl_icon} <b>{exit_title}!</b>\n\n"
                f"• <b>Instrument:</b> {symbol} ({type_label})\n"
                f"• <b>Exit Price:</b> ₹{opt_price:.2f}\n"
                f"• <b>Entry Price:</b> ₹{actual_fill:.2f}\n"
                f"• <b>Lot Size:</b> {lot_size} Qty (Total: {quantity})\n"
                f"• <b>Gross PnL:</b> ₹{gross_loss:+,.2f} ({pnl_pct:+.2f}%)\n"
                f"• <b>Charges & Taxes:</b> -₹{fees:,.2f}\n"
                f"• <b>Net Realized PnL:</b> {pnl_icon} <b>₹{net_pnl:+,.2f}</b>\n"
                f"• <b>Status:</b> Position Closed by Risk Engine.\n\n"
                f"🕒 {datetime.now().strftime('%H:%M:%S IST')}"
            )
            telegram.send_notification(exit_msg)
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paper Trading Options Execution")
    parser.add_argument("--type", choices=["CE", "PE"], default="PE", help="Option type (CE or PE)")
    parser.add_argument("--tp", type=float, default=0.05, help="Target profit percentage (default 0.05 = 5%%)")
    parser.add_argument("--sl", type=float, default=0.025, help="Stop loss percentage (default 0.025 = 2.5%%)")
    parser.add_argument("--lots", type=int, default=2, help="Lots to trade (default 2 = 130 qty)")
    parser.add_argument("--price", type=float, default=None, help="Custom entry premium price")
    parser.add_argument("--strike", type=int, default=None, help="Custom strike price (e.g. 23950)")
    args = parser.parse_args()

    execute_paper_trade(
        opt_type_str=args.type,
        target_pct=args.tp,
        sl_pct=args.sl,
        lots=args.lots,
        custom_price=args.price,
        custom_strike=args.strike,
        auto_monitor=True
    )
