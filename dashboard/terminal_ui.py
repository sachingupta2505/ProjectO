"""
Terminal Dashboard using Rich.
Renders real-time tables for positions, orders, strategy metrics, and RMS alerts.
"""

from datetime import datetime
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.console import Group
from brokers.base_broker import BaseBroker
from core.risk_manager import RiskManager
from strategies.base_strategy import BaseStrategy
from config.settings import settings


def render_dashboard(
    broker: BaseBroker,
    strategy: BaseStrategy,
    risk_manager: RiskManager,
    spot_price: float
) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="metrics", size=4),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3)
    )
    layout["main"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1)
    )

    # 1. Header
    mode_str = "[bold green]PAPER TRADING[/]" if settings.PAPER_TRADING else "[bold red]LIVE TRADING[/]"
    header_text = Text.from_markup(
        f"⚡ [bold cyan]NIFTY OPTIONS TRADING BOT[/] | Mode: {mode_str} | Broker: [bold yellow]{settings.BROKER.upper()}[/] | "
        f"Nifty Spot: [bold magenta]₹{spot_price:,.2f}[/] | Time: [cyan]{datetime.now().strftime('%H:%M:%S')}[/]"
    )
    layout["header"].update(Panel(header_text, style="blue"))

    # 2. Financial Metrics Bar
    margins = broker.get_margins()
    tot_pnl = margins.get("total_pnl", 0.0)
    real_pnl = margins.get("realized_pnl", 0.0)
    unreal_pnl = margins.get("unrealized_pnl", 0.0)

    pnl_color = "green" if tot_pnl >= 0 else "red"
    metrics_table = Table.grid(expand=True)
    metrics_table.add_column(justify="center", ratio=1)
    metrics_table.add_column(justify="center", ratio=1)
    metrics_table.add_column(justify="center", ratio=1)
    metrics_table.add_column(justify="center", ratio=1)

    metrics_table.add_row(
        f"[bold]Total PnL:[/] [{pnl_color}]₹{tot_pnl:+,.2f}[/]",
        f"[bold]Realized PnL:[/] ₹{real_pnl:+,.2f}",
        f"[bold]Unrealized PnL:[/] ₹{unreal_pnl:+,.2f}",
        f"[bold]Available Cash:[/] ₹{margins.get('available_cash', 0.0):,.2f}",
    )
    layout["metrics"].update(Panel(metrics_table, title="Portfolio Overview", style="cyan"))

    # 3. Active Positions Table (Left Column)
    pos_table = Table(title="Active Option Positions", expand=True)
    pos_table.add_column("Symbol", style="cyan", no_wrap=True)
    pos_table.add_column("Qty", justify="right")
    pos_table.add_column("Avg Price", justify="right")
    pos_table.add_column("LTP", justify="right")
    pos_table.add_column("PnL", justify="right")

    positions = broker.get_positions()
    active_count = 0
    for sym, p in positions.items():
        if p.quantity != 0 or p.realized_pnl != 0:
            active_count += 1
            pos_pnl_color = "green" if p.total_pnl >= 0 else "red"
            avg_p = p.average_sell_price if p.quantity < 0 else p.average_buy_price
            pos_table.add_row(
                sym,
                str(p.quantity),
                f"₹{avg_p:.2f}",
                f"₹{p.ltp:.2f}",
                f"[{pos_pnl_color}]₹{p.total_pnl:+,.2f}[/]"
            )

    if active_count == 0:
        pos_table.add_row("-", "0", "₹0.00", "₹0.00", "₹0.00")

    layout["left"].update(Panel(pos_table, style="white"))

    # 4. Strategy & RMS Status (Right Column)
    strat_status = strategy.get_status()
    strat_text = f"[bold yellow]Strategy:[/] {strat_status.get('name', 'N/A')}\n"

    if "ce_leg" in strat_status:
        ce = strat_status["ce_leg"]
        pe = strat_status["pe_leg"]
        strat_text += (
            f"\n[bold]CE Leg:[/] {ce['symbol']} | Entry: ₹{ce['entry_price']:.2f} | SL: ₹{ce['sl_price']:.2f}\n"
            f"       Status: {'CLOSED (' + ce['exit_reason'] + ')' if ce['exited'] else 'ACTIVE'}\n"
            f"\n[bold]PE Leg:[/] {pe['symbol']} | Entry: ₹{pe['entry_price']:.2f} | SL: ₹{pe['sl_price']:.2f}\n"
            f"       Status: {'CLOSED (' + pe['exit_reason'] + ')' if pe['exited'] else 'ACTIVE'}\n"
        )
    elif "active_position" in strat_status:
        strat_text += (
            f"\n[bold]Position:[/] {strat_status.get('active_position')}\n"
            f"[bold]Symbol:[/] {strat_status.get('symbol')}\n"
            f"[bold]Entry:[/] ₹{strat_status.get('entry_price', 0.0):.2f} | "
            f"[bold]SL:[/] ₹{strat_status.get('current_sl', 0.0):.2f} | "
            f"[bold]Target:[/] ₹{strat_status.get('target_price', 0.0):.2f}\n"
            f"[bold]Trades Today:[/] {strat_status.get('trades_today', 0)}\n"
        )

    rms_status = (
        "[bold red]TRIGGERED - TRADING BLOCKED[/]"
        if risk_manager.kill_switch_active
        else "[bold green]NORMAL (Guards Active)[/]"
    )
    strat_text += f"\n[bold]RMS Kill Switch:[/] {rms_status}\n"
    strat_text += f"[bold]Daily Loss Limit:[/] -₹{risk_manager.max_daily_loss:,.2f}\n"
    strat_text += f"[bold]Auto Square-Off:[/] {risk_manager.auto_square_off_time_str} IST"

    layout["right"].update(Panel(strat_text, title="Strategy & Risk Engine", style="white"))

    # 5. Footer Instructions
    footer_text = Text.from_markup(
        "[dim]Press Ctrl+C to stop the bot and trigger emergency square-off. Logs are written to logs/trading_bot.log[/dim]"
    )
    layout["footer"].update(Panel(footer_text, style="dim"))

    return layout
