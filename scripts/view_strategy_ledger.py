"""
Strategy Ledger Viewer & Analytics CLI.
Displays full historical trades and performance metrics for any strategy (e.g. ORION-15).
Usage:
    python scripts/view_strategy_ledger.py --strategy orion
"""

import sys
import argparse
from pathlib import Path
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from core.strategy_ledger import strategy_ledger

console = Console()


def display_ledger(strategy_name: str = "orion"):
    summary = strategy_ledger.get_summary(strategy_name)
    trades = strategy_ledger.load_trades(strategy_name)

    console.print("\n")
    console.print(Panel(
        f"[bold cyan]STRATEGY TRADE LEDGER: {strategy_name.upper()}[/bold cyan]\n"
        f"• Total Trades: [bold]{summary['total_trades']}[/bold] | Win Rate: [bold]{summary['win_rate_pct']}%[/bold]\n"
        f"• Total Net P&L: [{'bold green' if summary['net_pnl'] >= 0 else 'bold red'}]₹{summary['net_pnl']:,.2f}[/]\n"
        f"• Profit Factor: [bold]{summary['profit_factor']}[/bold] | Max DD: [bold red]₹{summary['max_drawdown']:,.2f}[/]",
        title=f"🏛️ Strategy Performance: {strategy_name.upper()}",
        border_style="cyan"
    ))

    if not trades:
        console.print(f"[yellow]No trades recorded yet in {strategy_name}_ledger.json. Ready for live session.[/yellow]\n")
        return

    table = Table(title=f"📜 {strategy_name.upper()} Execution History", show_header=True, header_style="bold magenta")
    table.add_column("Trade ID", style="dim", width=12)
    table.add_column("Date", width=10)
    table.add_column("Index", width=8)
    table.add_column("Side", width=6)
    table.add_column("Entry Time", width=8)
    table.add_column("Exit Time", width=8)
    table.add_column("Entry Spot", justify="right")
    table.add_column("Exit Spot", justify="right")
    table.add_column("Reason", width=18)
    table.add_column("Breakeven?", width=10)
    table.add_column("Net P&L (₹)", justify="right")

    for t in trades:
        pnl = t.get("net_pnl", 0.0)
        pnl_str = f"[green]+₹{pnl:,.2f}[/green]" if pnl > 0 else (f"[red]-₹{abs(pnl):,.2f}[/red]" if pnl < 0 else "₹0.00")
        be_str = "[cyan]Yes 🔒[/cyan]" if t.get("breakeven_triggered") else "[dim]No[/dim]"
        side_str = "[green]CALL[/green]" if t.get("side") == "CALL" else "[red]PUT[/red]"

        table.add_row(
            str(t.get("trade_id", "")),
            str(t.get("date", "")),
            str(t.get("index", "")),
            side_str,
            str(t.get("entry_time", "")),
            str(t.get("exit_time", "")),
            f"{t.get('entry_spot', 0.0):.1f}",
            f"{t.get('exit_spot', 0.0):.1f}",
            str(t.get("reason", "")),
            be_str,
            pnl_str
        )

    console.print(table)
    console.print("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View Strategy Trade Ledger")
    parser.add_argument("--strategy", type=str, default="orion", help="Strategy name (default: orion)")
    args = parser.parse_args()
    display_ledger(args.strategy)
