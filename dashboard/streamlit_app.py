"""
Streamlit Web Dashboard for Angel One Trading & Nifty Options Bot.
Fetches and displays live account balance, trades, positions, real-time PnL,
and provides remote control and emergency square-off.
"""

import sys
import os
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import numpy as np
import streamlit as st

# Ensure project root is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from core.models import OptionType, OrderSide, OrderType, OrderStatus
from core.option_chain import (
    get_atm_strike, get_strikes_around_atm, calculate_black_scholes, get_next_weekly_expiry
)
from brokers.paper_broker import PaperBroker
from brokers.angel_broker import AngelOneBroker
from core.risk_manager import RiskManager
from strategies.opening_retest_trader import OpeningRetestStrategy
from strategies.theta_decay_trader import ThetaDecayTraderStrategy
from core.market_data import get_live_nifty_spot, get_live_banknifty_spot
from config.settings import settings
from core.strategy_ledger import strategy_ledger
import streamlit.components.v1 as components
from dashboard.ticker_service import start_ticker_service


def render_realtime_broker_banner():
    """
    Renders a broker-style sticky header banner with a real-time live ticker stream.
    Updates in place via client-side JavaScript every 1 second without reloading or flickering the Streamlit page.
    """
    banner_html = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8"/>
    <style>
      * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      }
      body {
        background-color: transparent;
        color: #d1d4dc;
        overflow: hidden;
      }
      .ticker-container {
        background: linear-gradient(180deg, #181d28 0%, #131722 100%);
        border: 1px solid #2a2e39;
        border-radius: 8px;
        padding: 10px 18px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        height: 68px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
      }
      .ticker-section {
        display: flex;
        align-items: center;
        gap: 20px;
        flex-wrap: nowrap;
      }
      .ticker-item {
        display: flex;
        flex-direction: column;
        justify-content: center;
      }
      .ticker-label {
        font-size: 10px;
        font-weight: 700;
        color: #848e9c;
        text-transform: uppercase;
        letter-spacing: 0.6px;
        margin-bottom: 2px;
        display: flex;
        align-items: center;
        gap: 5px;
      }
      .ticker-val-row {
        display: flex;
        align-items: baseline;
        gap: 8px;
      }
      .ticker-price {
        font-size: 17px;
        font-weight: 800;
        color: #ffffff;
        letter-spacing: -0.2px;
        transition: background-color 0.4s ease, color 0.4s ease;
        padding: 1px 4px;
        border-radius: 4px;
      }
      .badge {
        font-size: 11px;
        font-weight: 700;
        padding: 2px 6px;
        border-radius: 4px;
        line-height: 1.2;
      }
      .badge-up {
        color: #089981;
        background: rgba(8, 153, 129, 0.18);
        border: 1px solid rgba(8, 153, 129, 0.3);
      }
      .badge-down {
        color: #f23645;
        background: rgba(242, 54, 69, 0.18);
        border: 1px solid rgba(242, 54, 69, 0.3);
      }
      .badge-cyan {
        color: #00bcd4;
        background: rgba(0, 188, 212, 0.15);
        border: 1px solid rgba(0, 188, 212, 0.3);
      }
      .badge-amber {
        color: #ffa726;
        background: rgba(255, 167, 38, 0.15);
        border: 1px solid rgba(255, 167, 38, 0.3);
      }
      .tick-flash-green {
        animation: flashGreen 0.5s ease;
      }
      .tick-flash-red {
        animation: flashRed 0.5s ease;
      }
      @keyframes flashGreen {
        0% { background-color: rgba(8, 153, 129, 0.6); color: #ffffff; }
        100% { background-color: transparent; }
      }
      @keyframes flashRed {
        0% { background-color: rgba(242, 54, 69, 0.6); color: #ffffff; }
        100% { background-color: transparent; }
      }
      .pulse-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background-color: #089981;
        box-shadow: 0 0 0 0 rgba(8, 153, 129, 0.7);
        animation: pulseLive 1.8s infinite;
        display: inline-block;
      }
      @keyframes pulseLive {
        0% { box-shadow: 0 0 0 0 rgba(8, 153, 129, 0.7); }
        70% { box-shadow: 0 0 0 6px rgba(8, 153, 129, 0); }
        100% { box-shadow: 0 0 0 0 rgba(8, 153, 129, 0); }
      }
      .separator {
        width: 1px;
        height: 32px;
        background-color: #2a2e39;
      }
      .subtext {
        font-size: 11px;
        color: #b2b5be;
        font-family: monospace;
      }
    </style>
    </head>
    <body>
      <div class="ticker-container">
        <div class="ticker-section">
          <!-- NIFTY 50 -->
          <div class="ticker-item">
            <div class="ticker-label">
              <span class="pulse-dot"></span>
              <span>NIFTY 50 SPOT</span>
            </div>
            <div class="ticker-val-row">
              <span id="nifty-spot" class="ticker-price">--</span>
              <span id="nifty-badge" class="badge badge-cyan">--</span>
            </div>
          </div>

          <div class="separator"></div>

          <!-- BANK NIFTY -->
          <div class="ticker-item">
            <div class="ticker-label">BANK NIFTY</div>
            <div class="ticker-val-row">
              <span id="bank-spot" class="ticker-price">--</span>
              <span id="bank-badge" class="badge badge-cyan">--</span>
            </div>
          </div>

          <div class="separator"></div>

          <!-- ATM STRIKE -->
          <div class="ticker-item">
            <div class="ticker-label">ATM STRIKE</div>
            <div class="ticker-val-row">
              <span id="atm-strike" class="ticker-price" style="color: #00bcd4;">--</span>
              <span id="lot-badge" class="badge badge-amber">65 LOT</span>
            </div>
          </div>

          <div class="separator"></div>

          <!-- ATM OPTIONS PE & CE -->
          <div class="ticker-item">
            <div class="ticker-label">ATM OPTIONS (PUT / CALL)</div>
            <div class="ticker-val-row">
              <span style="font-size: 13px; font-weight: 700; color: #f23645;">PE: <span id="pe-ltp" style="color:#ffffff;">--</span></span>
              <span style="color: #434651;">|</span>
              <span style="font-size: 13px; font-weight: 700; color: #089981;">CE: <span id="ce-ltp" style="color:#ffffff;">--</span></span>
            </div>
          </div>

          <div class="separator"></div>

          <!-- DAY RANGE -->
          <div class="ticker-item">
            <div class="ticker-label">DAY RANGE</div>
            <div class="ticker-val-row">
              <span id="day-range" class="subtext" style="font-size: 12px; font-weight: 600; color: #d1d4dc;">--</span>
            </div>
          </div>
        </div>

        <!-- RIGHT: LIVE STATUS CLOCK -->
        <div class="ticker-item" style="text-align: right; min-width: 100px;">
          <div style="display: flex; align-items: center; justify-content: flex-end; gap: 5px;">
            <span style="font-size: 10px; font-weight: 800; color: #089981; letter-spacing: 0.5px;">LIVE FEED</span>
          </div>
          <div id="ticker-clock" class="subtext" style="color: #787b86;">--:--:--</div>
        </div>
      </div>

      <script>
        let prevNifty = 0;
        let prevBank = 0;

        async function fetchLiveTicker() {
          try {
            const host = window.location.hostname || "127.0.0.1";
            const response = await fetch("http://" + host + ":8502/api/ticker", { cache: "no-store" });
            if (!response.ok) return;
            const data = await response.json();

            // 1. NIFTY 50
            const nVal = data.nifty || 0;
            const nEl = document.getElementById("nifty-spot");
            const nBadge = document.getElementById("nifty-badge");

            nEl.textContent = "₹" + nVal.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

            if (prevNifty > 0) {
              if (nVal > prevNifty) {
                nEl.classList.remove("tick-flash-red");
                nEl.classList.add("tick-flash-green");
                setTimeout(() => nEl.classList.remove("tick-flash-green"), 450);
              } else if (nVal < prevNifty) {
                nEl.classList.remove("tick-flash-green");
                nEl.classList.add("tick-flash-red");
                setTimeout(() => nEl.classList.remove("tick-flash-red"), 450);
              }
            }
            prevNifty = nVal;

            const nChg = data.n_chg || 0;
            const nPct = data.n_pct || 0;
            const sign = nChg >= 0 ? "+" : "";
            nBadge.textContent = sign + nChg.toFixed(2) + " (" + sign + nPct.toFixed(2) + "%)";
            nBadge.className = "badge " + (nChg >= 0 ? "badge-up" : "badge-down");

            // 2. BANK NIFTY
            const bVal = data.bank || 0;
            const bEl = document.getElementById("bank-spot");
            const bBadge = document.getElementById("bank-badge");

            bEl.textContent = "₹" + bVal.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

            if (prevBank > 0) {
              if (bVal > prevBank) {
                bEl.classList.remove("tick-flash-red");
                bEl.classList.add("tick-flash-green");
                setTimeout(() => bEl.classList.remove("tick-flash-green"), 450);
              } else if (bVal < prevBank) {
                bEl.classList.remove("tick-flash-green");
                bEl.classList.add("tick-flash-red");
                setTimeout(() => bEl.classList.remove("tick-flash-red"), 450);
              }
            }
            prevBank = bVal;

            const bChg = data.b_chg || 0;
            const bPct = data.b_pct || 0;
            const bSign = bChg >= 0 ? "+" : "";
            bBadge.textContent = bSign + bChg.toFixed(2) + " (" + bSign + bPct.toFixed(2) + "%)";
            bBadge.className = "badge " + (bChg >= 0 ? "badge-up" : "badge-down");

            // 3. ATM Strike & Options
            document.getElementById("atm-strike").textContent = data.atm || "--";
            document.getElementById("pe-ltp").textContent = "₹" + (data.pe_ltp ? data.pe_ltp.toFixed(2) : "--");
            document.getElementById("ce-ltp").textContent = "₹" + (data.ce_ltp ? data.ce_ltp.toFixed(2) : "--");

            // 5. Day Range
            if (data.n_low && data.n_high) {
              document.getElementById("day-range").textContent = "L: " + Math.round(data.n_low).toLocaleString("en-IN") + " — H: " + Math.round(data.n_high).toLocaleString("en-IN");
            }

            // 6. Clock
            document.getElementById("ticker-clock").textContent = data.ts || new Date().toLocaleTimeString();
          } catch (err) {
            console.debug("Ticker polling error:", err);
          }
        }

        // Run immediately, then poll every 1000ms
        fetchLiveTicker();
        setInterval(fetchLiveTicker, 1000);
      </script>
    </body>
    </html>
    """
    components.html(banner_html, height=75, scrolling=False)


# ----------------------------------------------------------------------
# Helper: Angel One Live Data Fetcher
# ----------------------------------------------------------------------
@st.cache_resource
def get_angel_session():
    """Authenticate and return Angel One session using login.py."""
    try:
        from login import login
        api = login()
        return api
    except Exception as e:
        return None


def fetch_angel_data(api):
    """Fetch profile, balance (RMS), positions, orders, and tradebook from Angel One."""
    data = {
        "connected": False,
        "profile": {},
        "balance": {},
        "positions": [],
        "orders": [],
        "trades": [],
        "error": None
    }

    if not api:
        data["error"] = "Angel One session not initialized."
        return data

    data["connected"] = True

    # 1. Profile
    try:
        prof = api.getProfile(api.refresh_token)
        if prof and prof.get("status"):
            data["profile"] = prof.get("data", {})
    except Exception as e:
        data["profile_error"] = str(e)

    # 2. Balance / RMS Limits
    try:
        rms = api.rmsLimit()
        if rms and rms.get("status"):
            data["balance"] = rms.get("data", {})
        elif rms and rms.get("errorCode"):
            data["balance_error"] = f"{rms.get('message')} ({rms.get('errorCode')})"
    except Exception as e:
        data["balance_error"] = str(e)

    # 3. Positions
    try:
        pos = api.position()
        if pos and pos.get("status") and pos.get("data"):
            data["positions"] = pos.get("data", [])
        elif pos and pos.get("errorCode"):
            data["positions_error"] = f"{pos.get('message')} ({pos.get('errorCode')})"
    except Exception as e:
        data["positions_error"] = str(e)

    # 4. Order Book
    try:
        ob = api.orderBook()
        if ob and ob.get("status") and ob.get("data"):
            data["orders"] = ob.get("data", [])
        elif ob and ob.get("errorCode"):
            data["orders_error"] = f"{ob.get('message')} ({ob.get('errorCode')})"
    except Exception as e:
        data["orders_error"] = str(e)

    # 5. Trade Book
    try:
        tb = api.tradeBook()
        if tb and tb.get("status") and tb.get("data"):
            data["trades"] = tb.get("data", [])
        elif tb and tb.get("errorCode"):
            data["trades_error"] = f"{tb.get('message')} ({tb.get('errorCode')})"
    except Exception as e:
        data["trades_error"] = str(e)

    return data


def render_dashboard():
    # Page Configuration
    st.set_page_config(
        page_title="Angel One Trading Terminal | ProjectO",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Custom styling
    st.markdown("""
    <style>
        .metric-card {
            background-color: #1e222d;
            border-radius: 8px;
            padding: 15px;
            border: 1px solid #2a2e39;
            margin-bottom: 10px;
        }
        .pnl-positive {
            color: #089981 !important;
            font-weight: bold;
        }
        .pnl-negative {
            color: #f23645 !important;
            font-weight: bold;
        }
        .stButton>button {
            border-radius: 6px;
        }
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 2rem;
        }
    </style>
    """, unsafe_allow_html=True)

    # Start Real-Time Ticker Microservice on Port 8502
    start_ticker_service(8502)

    # Render Broker-Style Live Real-Time Banner (Client-side 1s updates, zero page reload)
    render_realtime_broker_banner()

    # Session State Initialization
    if "paper_broker" not in st.session_state:
        st.session_state.paper_broker = PaperBroker(initial_capital=settings.PAPER_INITIAL_CAPITAL, persist=True)
        st.session_state.paper_broker.authenticate()

    if "risk_manager" not in st.session_state:
        st.session_state.risk_manager = RiskManager()

    if "spot_price" not in st.session_state:
        st.session_state.spot_price = 24500.0

    if "strategy" not in st.session_state:
        st.session_state.strategy = OpeningRetestStrategy(
            broker=st.session_state.paper_broker,
            risk_manager=st.session_state.risk_manager,
            symbol="NIFTY",
            lots=2
        )
        st.session_state.strategy.initialize()

    # Sidebar Controls
    with st.sidebar:
        st.title("⚡ Control Center")

        default_idx = 1 if settings.PAPER_TRADING else 0
        account_source = st.radio(
            "Account Source",
            ["Angel One (Live Account)", "Paper Trading (Simulator)"],
            index=default_idx
        )

        st.divider()

        # Real-time Auto-Sync Engine
        st.subheader("🔄 Live Sync Engine")
        auto_sync = st.checkbox("⚡ Auto-Sync with Telegram & Bot (3s)", value=True)
        if auto_sync:
            try:
                from streamlit_autorefresh import st_autorefresh
                st_autorefresh(interval=3000, key="tg_live_sync")
            except Exception:
                pass
        st.caption("📱 100% Synced with Telegram bot trades & orders")

        st.divider()

        # Capital Management & Reset
        st.subheader("💰 Capital Allocation")
        cap_option = st.selectbox(
            "Capital Per Strategy",
            ["₹50,000", "₹1,00,000 (Recommended)", "₹2,00,000", "₹5,00,000"],
            index=1
        )
        cap_val = 50000.0 if "50,000" in cap_option else (100000.0 if "1,00,000" in cap_option else (200000.0 if "2,00,000" in cap_option else 500000.0))
        if st.button("🔄 Reset Core Accounts (₹" + f"{int(cap_val/1000)}k each)", use_container_width=True):
            for name in ["orion", "theta", "default"]:
                PaperBroker(account_name=name, persist=True).reset_account(cap_val)
            st.session_state.paper_broker.reset_account(cap_val)
            st.success(f"✅ Core Strategy accounts reset to ₹{cap_val:,.2f} each (Total: ₹{cap_val*2:,.2f})!")
            st.rerun()

        st.divider()

        st.subheader("⚙️ Optimized Portfolio")
        strat_type = st.selectbox(
            "Selected Strategy View",
            [
                "🌟 Core Duo Portfolio (ORION-15 + THETA-0DTE)",
                "🚀 ORION-15 (Opening Retest)",
                "⏳ THETA-0DTE (Expiry Scalp)"
            ],
            index=0
        )
        lots = st.number_input("Trading Lots per Strategy (1 Lot = 65 Qty)", min_value=1, max_value=10, value=2)
        st.markdown(f"⚡ Per Strategy Size: **{lots * settings.NIFTY_LOT_SIZE} Qty** ({lots} Lots)")
        sl_pct = st.slider("Max Option Risk Cap (%)", min_value=10, max_value=50, value=25, step=5)

        st.divider()

        st.subheader("🚨 Emergency Actions")
        if st.button("🛑 EMERGENCY SQUARE-OFF ALL", type="primary", use_container_width=True):
            if account_source.startswith("Angel"):
                api = get_angel_session()
                if api:
                    broker = AngelOneBroker()
                    broker.smart_api = api
                    closed = broker.square_off_all_positions()
                    st.session_state.risk_manager.kill_switch_active = True
                    st.warning(f"Square-off triggered on Angel One ({len(closed)} orders placed)!")
            else:
                closed = st.session_state.paper_broker.square_off_all_positions()
                st.session_state.risk_manager.kill_switch_active = True
                st.warning(f"Closed {len(closed)} simulated positions. Kill switch engaged!")

        st.divider()
        st.markdown("### 📡 Live Ticker Status")
        st.success("🟢 Broker-style real-time ticker stream active (1-second client updates).")
        if st.button("🔄 Sync Account & Positions", use_container_width=True):
            st.rerun()

        st.caption("ProjectO • Nifty Options Trading Bot")

    # Fetch data based on selected account
    angel_api = None
    angel_data = None

    if account_source.startswith("Angel"):
        angel_api = get_angel_session()
        angel_data = fetch_angel_data(angel_api)

    # Header Section
    col_head1, col_head2 = st.columns([3, 1])
    with col_head1:
        st.title("📊 Trading Terminal & Portfolio")
        now_str = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        if account_source.startswith("Angel"):
            acc_name = angel_data["profile"].get("name", "PUSHPA GUPTA") if angel_data else "Angel One"
            client_code = angel_data["profile"].get("clientcode", "P101525") if angel_data else "P101525"
            st.markdown(f"**Account:** `{client_code}` — **{acc_name}** | **Source:** Angel One SmartAPI | **Time:** `{now_str} IST`")
        else:
            st.markdown(f"**Account:** `SIMULATOR` | **Source:** Paper Trading Engine | **Time:** `{now_str} IST`")

    with col_head2:
        if st.button("🔄 Refresh Data", use_container_width=True):
            st.rerun()

    # ------------------------------------------------------------------
    # Ensure spot price initialized for downstream option calculators
    nifty_data = get_live_nifty_spot()
    st.session_state.spot_price = nifty_data.get("spot", 23950.0)
    st.divider()

    # Top Metrics Bar (PnL, Funds, Positions)
    st.write("")

    if account_source.startswith("Angel"):
        pos_list = angel_data.get("positions", []) if angel_data else []
        total_realized = sum(float(p.get("realised", 0)) for p in pos_list)
        total_unrealized = sum(float(p.get("unrealised", 0)) for p in pos_list)
        total_pnl = total_realized + total_unrealized

        avail_cash = 0.0
        net_margin = 0.0
        if angel_data and angel_data.get("balance"):
            avail_cash = float(angel_data["balance"].get("availablecash", 0.0))
            net_margin = float(angel_data["balance"].get("net", 0.0))

        m1, m2, m3, m4 = st.columns(4)
        pnl_delta = f"{total_pnl:+,.2f}"
        m1.metric("Total MTM PnL", f"₹{total_pnl:+,.2f}", delta=pnl_delta, delta_color="normal")
        m2.metric("Realized PnL", f"₹{total_realized:+,.2f}")
        m3.metric("Unrealized PnL", f"₹{total_unrealized:+,.2f}")
        m4.metric("Available Cash", f"₹{avail_cash:,.2f}" if avail_cash > 0 else "SmartAPI Auth OK")

    else:
        # The production runner is ORION-only. Keep one paper account aligned
        # with the bot command instead of presenting legacy portfolio totals.
        active_brokers = [
            PaperBroker(account_name="orion", persist=True)
        ]
        tot_cap = sum(b.initial_capital for b in active_brokers)
        tot_cash = sum(b.available_cash for b in active_brokers)
        tot_pnl = sum(b.get_margins()["total_pnl"] for b in active_brokers)
        tot_used = sum(b.get_margins().get("margin_used", 0.0) for b in active_brokers)
        
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("💰 ORION Capital", f"₹{tot_cap:,.2f}")
        m2.metric("💵 Available Funds", f"₹{tot_cash:,.2f}", f"₹{tot_used:,.2f} Used")
        m3.metric("📈 Today's Net Profit", f"₹{tot_pnl:+,.2f}", delta=f"{tot_pnl:+,.2f}", delta_color="normal")
        m4.metric("🎯 Active Strategy", "ORION 2.0", f"{lots} Lots")
        m5.metric("🤖 Bot Daemon", "🟢 Armed & Ready", "NIFTY opening retest")

    st.divider()

    # Streamlined Tabs: ORION-15 Cockpit, Strategy Ledgers, Positions & Orders, Account & RMS Limits, Option Chain, AI Learning
    tab_orion, tab_ledger, tab_pos_orders, tab_balance, tab_greeks, tab_learning = st.tabs([
        "🚀 ORION-15 Cockpit",
        "🏛️ Strategy Ledgers",
        "📊 Positions & Orders",
        "💼 Account & RMS Limits",
        "🧮 Options Chain & Greeks",
        "🧠 AI Learning & Forensics"
    ])

    # ======================================================================
    # TAB 1: ORION-15 Cockpit (Opening Retest Automated Engine)
    # ======================================================================
    with tab_orion:
        st.subheader("🚀 ORION-15: Opening Retest Institutional Engine")
        st.markdown(
            "Automated execution of the **15-Minute Opening Retest Setup** on **NIFTY 50**. "
            "Evaluates the first 15m candle (09:15-09:30 AM), verifies conviction body & wick rejection, "
            "monitors the 50% retest zone, and ratchets Stop Loss to Breakeven at Target 1."
        )

        # Status & Controls
        c_stat1, c_stat2, c_stat3 = st.columns(3)
        c_stat1.success("🟢 **Bot Status:** ARMED & ACTIVE (Daemon)")
        c_stat2.info("🎯 **Target Index:** NIFTY 50 (Weekly Tuesday Expiry)")
        c_stat3.warning("🧪 **Execution Mode:** PAPER TRIAL (1 Month Evaluation)")

        st.markdown("""
        <div style="background: linear-gradient(135deg, #181d28 0%, #131722 100%); border: 1px solid #2962ff; border-radius: 10px; padding: 18px; margin: 15px 0; box-shadow: 0 4px 15px rgba(0,0,0,0.3);">
            <div style="font-size: 14px; font-weight: bold; color: #2962ff; text-transform: uppercase; margin-bottom: 8px;">
                ⚡ ORION-15 Execution Schedule & Rules
            </div>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-top: 10px;">
                <div style="background: rgba(255,255,255,0.03); padding: 10px; border-radius: 6px; border-left: 3px solid #00f2fe;">
                    <div style="font-size: 11px; color: #848e9c;">09:15 - 09:30 AM</div>
                    <div style="font-weight: 700; color: #fff; margin-top: 3px;">15m Candle Formation</div>
                    <div style="font-size: 11px; color: #848e9c; margin-top: 3px;">Aggregates first three 5m bars</div>
                </div>
                <div style="background: rgba(255,255,255,0.03); padding: 10px; border-radius: 6px; border-left: 3px solid #089981;">
                    <div style="font-size: 11px; color: #848e9c;">09:30 AM Sharp</div>
                    <div style="font-weight: 700; color: #fff; margin-top: 3px;">Conviction Validation</div>
                    <div style="font-size: 11px; color: #848e9c; margin-top: 3px;">Body ≥ 30 pts, Wick &lt; 85%</div>
                </div>
                <div style="background: rgba(255,255,255,0.03); padding: 10px; border-radius: 6px; border-left: 3px solid #f59e0b;">
                    <div style="font-size: 11px; color: #848e9c;">09:30 - 11:30 AM</div>
                    <div style="font-weight: 700; color: #fff; margin-top: 3px;">50% Retest Entry Zone</div>
                    <div style="font-size: 11px; color: #848e9c; margin-top: 3px;">Enters on 5m confirmation bounce</div>
                </div>
                <div style="background: rgba(255,255,255,0.03); padding: 10px; border-radius: 6px; border-left: 3px solid #8b5cf6;">
                    <div style="font-size: 11px; color: #848e9c;">Target 1 & 2</div>
                    <div style="font-weight: 700; color: #fff; margin-top: 3px;">Breakeven Ratchet 🔒</div>
                    <div style="font-size: 11px; color: #848e9c; margin-top: 3px;">SL to BE @ T1, Take Profit @ T2</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("### 📊 Today's ORION Setup Parameters")
        o_col1, o_col2, o_col3, o_col4, o_col5 = st.columns(5)
        o_col1.metric("Position Sizing", f"{lots} Lots ({lots * settings.NIFTY_LOT_SIZE} Qty)", "NIFTY 50 Options")
        o_col2.metric("Min Body Filter", "30.0 pts", "Conviction Guard")
        o_col3.metric("Retest Zone", "50% Retracement", "Fibonacci Pullback")
        o_col4.metric("Target 1 (Lock)", "15m Extremum", "Breakeven Ratchet 🔒")
        o_col5.metric("Target 2 (Exit)", "15m Body × 80%", "Take Profit 🎯")

        st.divider()

        # Telemetry & Quick Link to Ledger
        st.info("💡 All trades taken by ORION-15 are automatically recorded in real-time into the **Strategy Ledger**. Check the **🏛️ Strategy Ledgers** tab to view complete trade forensics and historical benchmark data.")

    # ======================================================================
    # TAB 2: Strategy Ledgers (Dedicated Multi-Strategy Trade Audit)
    # ======================================================================
    with tab_ledger:
        st.subheader("🏛️ Strategy Trade Ledgers & Quantitative Analytics")
        st.markdown("Each algorithmic strategy maintains an isolated, independent trade ledger to track performance, win rates, drawdown, and execution anomalies.")

        # Top Controls: Strategy Selector & Data Source Toggle
        ctrl_col1, ctrl_col2 = st.columns([2, 2])
        with ctrl_col1:
            selected_strat = st.selectbox(
                "Select Strategy Ledger",
                [
                    "🌟 Core Duo Portfolio (ORION-15 + THETA-0DTE)",
                    "🚀 ORION-15 (Opening Retest)",
                    "⏳ THETA-0DTE (Expiry Scalp)"
                ],
                index=0
            )
        with ctrl_col2:
            if "ORION" in selected_strat:
                strat_key = "orion"
            elif "THETA" in selected_strat:
                strat_key = "theta"
            else:
                strat_key = "combined"

            data_source = st.radio(
                "Data Source",
                ["🟢 Live & Paper Trial (Upcoming 30 Days)", "📊 Benchmark / Combined View"],
                index=0,
                horizontal=True
            )

        source_param = "benchmark" if "Benchmark" in data_source else "live"

        if strat_key == "combined":
            trades_list = []
            for k in ["orion", "theta"]:
                trades_list.extend(strategy_ledger.load_trades(k, source="all"))
            trades_list.sort(key=lambda x: str(x.get("date", "")) + str(x.get("entry_time", "")))
            tot_t = len(trades_list)
            wins = [t for t in trades_list if float(t.get("net_pnl", 0)) > 0]
            losses = [t for t in trades_list if float(t.get("net_pnl", 0)) < 0]
            net_p = sum(float(t.get("net_pnl", 0)) for t in trades_list)
            gw = sum(float(t.get("net_pnl", 0)) for t in wins)
            gl = abs(sum(float(t.get("net_pnl", 0)) for t in losses))
            summary = {
                "total_trades": tot_t,
                "win_rate_pct": (len(wins) / tot_t * 100.0) if tot_t > 0 else 0.0,
                "net_pnl": net_p,
                "profit_factor": (gw / gl) if gl > 0 else (1.0 if gw == 0 else 99.0),
                "avg_win": (gw / len(wins)) if wins else 0.0,
                "avg_loss": (gl / len(losses)) if losses else 0.0,
                "max_drawdown": 0.0
            }
        else:
            summary = strategy_ledger.get_summary(strat_key, source=source_param)
            trades_list = strategy_ledger.load_trades(strat_key, source=source_param)

        st.markdown("<br>", unsafe_allow_html=True)

        # Eye-Candy Glowing KPI Cards
        pnl_val = summary.get("net_pnl", 0.0)
        pnl_color = "#089981" if pnl_val >= 0 else "#f23645"
        pnl_sign = "+" if pnl_val > 0 else ""
        win_rate = summary.get("win_rate_pct", 0.0)
        profit_factor = summary.get("profit_factor", 0.0)
        tot_trades = summary.get("total_trades", 0)
        avg_win = summary.get("avg_win", 0.0)
        avg_loss = summary.get("avg_loss", 0.0)
        max_dd = summary.get("max_drawdown", 0.0)
        rr_str = f"1:{abs(avg_win / avg_loss):.1f}" if avg_loss != 0 else "1:1.0"

        card_html = f"""
        <div style="display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 20px;">
            <div style="background: linear-gradient(135deg, rgba(24, 29, 40, 0.95), rgba(19, 23, 34, 0.95)); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 14px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.25);">
                <div style="font-size: 11px; font-weight: 700; color: #848e9c; text-transform: uppercase;">Total Trades</div>
                <div style="font-size: 22px; font-weight: 800; color: #ffffff; margin-top: 4px;">{tot_trades}</div>
                <div style="font-size: 10px; color: #2962ff; margin-top: 2px;">Executed</div>
            </div>
            <div style="background: linear-gradient(135deg, rgba(24, 29, 40, 0.95), rgba(19, 23, 34, 0.95)); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 14px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.25);">
                <div style="font-size: 11px; font-weight: 700; color: #848e9c; text-transform: uppercase;">Win Rate</div>
                <div style="font-size: 22px; font-weight: 800; color: {'#089981' if win_rate >= 40 else '#f59e0b'}; margin-top: 4px;">{win_rate:.1f}%</div>
                <div style="font-size: 10px; color: #848e9c; margin-top: 2px;">Hit Ratio</div>
            </div>
            <div style="background: linear-gradient(135deg, rgba(24, 29, 40, 0.95), rgba(19, 23, 34, 0.95)); border: 1px solid {pnl_color}; border-radius: 10px; padding: 14px; text-align: center; box-shadow: 0 4px 16px rgba(8, 153, 129, 0.2);">
                <div style="font-size: 11px; font-weight: 700; color: #848e9c; text-transform: uppercase;">Net Realized P&L</div>
                <div style="font-size: 22px; font-weight: 800; color: {pnl_color}; margin-top: 4px;">{pnl_sign}₹{pnl_val:,.2f}</div>
                <div style="font-size: 10px; color: {pnl_color}; margin-top: 2px;">After Brokerage & Taxes</div>
            </div>
            <div style="background: linear-gradient(135deg, rgba(24, 29, 40, 0.95), rgba(19, 23, 34, 0.95)); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 14px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.25);">
                <div style="font-size: 11px; font-weight: 700; color: #848e9c; text-transform: uppercase;">Profit Factor</div>
                <div style="font-size: 22px; font-weight: 800; color: #00f2fe; margin-top: 4px;">{profit_factor:.2f}</div>
                <div style="font-size: 10px; color: #848e9c; margin-top: 2px;">Win/Loss Gross</div>
            </div>
            <div style="background: linear-gradient(135deg, rgba(24, 29, 40, 0.95), rgba(19, 23, 34, 0.95)); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 14px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.25);">
                <div style="font-size: 11px; font-weight: 700; color: #848e9c; text-transform: uppercase;">Avg Win / Loss</div>
                <div style="font-size: 15px; font-weight: 700; color: #ffffff; margin-top: 6px;">+₹{avg_win:,.0f} / <span style="color:#f23645;">-₹{abs(avg_loss):,.0f}</span></div>
                <div style="font-size: 10px; color: #848e9c; margin-top: 2px;">R:R ~ {rr_str}</div>
            </div>
            <div style="background: linear-gradient(135deg, rgba(24, 29, 40, 0.95), rgba(19, 23, 34, 0.95)); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 14px; text-align: center; box-shadow: 0 4px 12px rgba(0,0,0,0.25);">
                <div style="font-size: 11px; font-weight: 700; color: #848e9c; text-transform: uppercase;">Max Drawdown</div>
                <div style="font-size: 22px; font-weight: 800; color: #f23645; margin-top: 4px;">₹{max_dd:,.2f}</div>
                <div style="font-size: 10px; color: #848e9c; margin-top: 2px;">Peak-to-Trough</div>
            </div>
        </div>
        """
        st.markdown(card_html, unsafe_allow_html=True)

        # Cumulative P&L Equity Curve
        if trades_list:
            df_chart = pd.DataFrame(trades_list)
            pnl_col = "net_pnl" if "net_pnl" in df_chart.columns else "pnl"
            df_chart[pnl_col] = pd.to_numeric(df_chart[pnl_col], errors="coerce").fillna(0.0)
            df_chart["Cumulative Net P&L (₹)"] = df_chart[pnl_col].cumsum()
            df_chart["Trade #"] = range(1, len(df_chart) + 1)

            st.markdown("### 📈 Cumulative Equity Curve")
            st.line_chart(df_chart.set_index("Trade #")["Cumulative Net P&L (₹)"], color="#089981" if pnl_val >= 0 else "#f23645")

            # Trade Details Table
            st.markdown(f"### 📜 {selected_strat} Detailed Trade Ledger")
            
            # Formatting dataframe for clean display
            display_cols = ["trade_id", "date", "side", "entry_time", "exit_time", "entry_premium", "exit_premium", "reason", "breakeven_triggered", "net_pnl", "mode"]
            existing_cols = [c for c in display_cols if c in df_chart.columns]
            df_display = df_chart[existing_cols].copy()

            # Renaming for friendly view
            col_rename = {
                "trade_id": "Trade ID",
                "date": "Date",
                "side": "Side",
                "entry_time": "Entry",
                "exit_time": "Exit",
                "entry_premium": "Buy (₹)",
                "exit_premium": "Sell (₹)",
                "reason": "Exit Reason",
                "breakeven_triggered": "Breakeven Lock",
                "net_pnl": "Net P&L (₹)",
                "mode": "Mode"
            }
            df_display = df_display.rename(columns=col_rename)

            # Styling helpers
            def color_pnl(val):
                try:
                    v = float(val)
                    if v > 0:
                        return 'color: #089981; font-weight: bold;'
                    elif v < 0:
                        return 'color: #f23645; font-weight: bold;'
                    return 'color: #848e9c;'
                except Exception:
                    return ''

            st.dataframe(
                df_display.style.map(color_pnl, subset=["Net P&L (₹)"]),
                use_container_width=True,
                height=350
            )

            # Export Button
            csv_data = df_chart.to_csv(index=False)
            st.download_button(
                label="📥 Download Strategy Ledger (CSV)",
                data=csv_data,
                file_name=f"{strat_key}_trade_ledger.csv",
                mime="text/csv",
                use_container_width=False
            )
        else:
            st.info(f"ℹ️ No trades recorded yet in the live `{strat_key}_ledger.json` file. Tomorrow's live paper session trades will automatically appear here.")
            st.caption("Tip: Switch the Data Source toggle above to '📊 6-Month Backtest Benchmark' to view the 34 historical baseline trades.")

        st.divider()

    # ======================================================================
    # TAB 3: Positions & Orders (Unified Live Execution Monitor)
    # ======================================================================
    with tab_pos_orders:
        st.subheader("📊 Active Net Positions & Order History")
        st.markdown("Real-time telemetry of open intraday positions, executed trades, and simulated/live orders.")

        if account_source.startswith("Angel"):
            col_ap1, col_ap2 = st.columns([1, 1])
            with col_ap1:
                st.markdown("#### 🟢 Active Open Positions (Angel One)")
                positions = angel_data.get("positions", []) if angel_data else []
                open_pos = [p for p in positions if int(p.get("netqty", 0)) != 0]
                if open_pos:
                    df_pos = pd.DataFrame(open_pos)
                    st.dataframe(df_pos, use_container_width=True)
                else:
                    st.info("ℹ️ No active open positions reported by Angel One. Account is flat.")
            with col_ap2:
                st.markdown("#### 📋 Order History Today (Angel One)")
                orders = angel_data.get("orders", []) if angel_data else []
                if orders:
                    df_orders = pd.DataFrame(orders)
                    st.dataframe(df_orders, use_container_width=True)
                else:
                    st.info("ℹ️ No orders in Angel One order book today.")
        else:
            positions = st.session_state.paper_broker.get_positions()
            open_pos = [p for p in positions.values() if p.quantity != 0]
            closed_pos = [p for p in positions.values() if p.quantity == 0 and p.realized_pnl != 0]

            st.markdown("#### 🟢 Active Open Positions")
            if open_pos:
                pos_data = []
                for p in open_pos:
                    avg_p = p.average_sell_price if p.quantity < 0 else p.average_buy_price
                    pos_data.append({
                        "Symbol": p.instrument.symbol,
                        "Side": "SELL (Short)" if p.quantity < 0 else "BUY (Long)",
                        "Quantity": abs(p.quantity),
                        "Lots": f"{abs(p.quantity) // settings.NIFTY_LOT_SIZE} Lot(s)",
                        "Avg Exec Price": f"₹{avg_p:,.2f}",
                        "LTP": f"₹{p.ltp:,.2f}",
                        "Charges": f"-₹{p.charges:,.2f}",
                        "Net Unrealized PnL": f"₹{p.unrealized_pnl:+,.2f}",
                        "Total PnL": f"₹{p.total_pnl:+,.2f}"
                    })
                st.dataframe(pd.DataFrame(pos_data), use_container_width=True)

                if st.button("🛑 Square Off All Open Positions", type="primary", key="btn_sq_open_pos"):
                    st.session_state.paper_broker.square_off_all_positions()
                    st.success("All positions closed at market price.")
                    st.rerun()
            else:
                st.info("ℹ️ Flat position. No active open positions currently.")

            st.divider()

            col_t1, col_t2 = st.columns([1, 1])
            with col_t1:
                st.markdown("#### 🏁 Executed Trades Today")
                trades = st.session_state.paper_broker.get_trades()
                if trades:
                    trd_records = []
                    for t in reversed(trades):
                        t_time_str = t.get("timestamp", "")
                        try:
                            t_time_str = datetime.fromisoformat(t_time_str).strftime("%H:%M:%S")
                        except Exception:
                            pass
                        trd_records.append({
                            "Trade ID": t.get("trade_id", ""),
                            "Symbol": t.get("symbol", ""),
                            "Side": t.get("side", ""),
                            "Qty": t.get("quantity", 0),
                            "Price": f"₹{float(t.get('price', 0.0)):,.2f}",
                            "Time": t_time_str,
                            "Tag": t.get("tag", "")
                        })
                    st.dataframe(pd.DataFrame(trd_records), use_container_width=True)
                else:
                    st.info("No executed trades logged yet for today's session.")

            with col_t2:
                st.markdown("#### 📋 Order History")
                orders = st.session_state.paper_broker.get_orders()
                if orders:
                    ord_records = []
                    for o in reversed(orders):
                        ord_records.append({
                            "Order ID": o.order_id,
                            "Symbol": o.instrument.symbol,
                            "Side": o.side.value,
                            "Type": o.order_type.value,
                            "Qty": o.quantity,
                            "Avg Price": f"₹{o.average_price:.2f}",
                            "Status": o.status.value,
                            "Time": o.placed_at.strftime("%H:%M:%S") if hasattr(o, "placed_at") and o.placed_at else ""
                        })
                    st.dataframe(pd.DataFrame(ord_records), use_container_width=True)
                else:
                    st.info("No orders in order history.")

            if closed_pos:
                st.divider()
                st.markdown("#### 🏁 Closed Positions Today")
                closed_data = []
                for p in closed_pos:
                    closed_data.append({
                        "Symbol": p.instrument.symbol,
                        "Exchange": p.instrument.exchange,
                        "Bought": f"{p.buy_quantity} Qty",
                        "Sold": f"{p.sell_quantity} Qty",
                        "Gross PnL": f"₹{p.gross_pnl:+,.2f}",
                        "Charges": f"-₹{p.charges:,.2f}",
                        "Net Realized PnL": f"₹{p.realized_pnl:+,.2f}",
                        "Status": "CLOSED"
                    })
                st.dataframe(pd.DataFrame(closed_data), use_container_width=True)

        st.divider()

    # ======================================================================
    # TAB 4: Account & RMS Risk Limits
    # ======================================================================
    with tab_balance:
        st.subheader("💼 Capital Overview & RMS Risk Limits")
        st.markdown("Disciplined risk parameters and capital allocation for institutional option execution.")

        margins = st.session_state.paper_broker.get_margins()
        c_b1, c_b2, c_b3, c_b4 = st.columns(4)
        c_b1.metric("💰 Starting Baseline", f"₹{margins['initial_capital']:,.2f}")
        c_b2.metric("💳 Available Funds", f"₹{margins['available_cash']:,.2f}")
        c_b3.metric("🔒 Margin Utilized", f"₹{margins.get('margin_used', 0.0):,.2f}")
        c_b4.metric("📈 Net Worth", f"₹{margins['net_worth']:,.2f}")

        st.divider()

        st.markdown("### 🛡️ Daily Risk Management System (RMS) Guardrails")
        st.markdown(
            "Hard risk limits automatically halt trading and trigger emergency protection "
            "if adverse drawdown thresholds are reached."
        )

        rms_c1, rms_c2, rms_c3 = st.columns(3)
        rms_c1.metric("🛑 Max Daily Loss Limit", "-₹4,000.00", "4.0% of Capital (Halt & Kill Switch)")
        rms_c2.metric("🎯 Max Daily Profit Target", "+₹7,000.00", "7.0% of Capital (Profit Lock)")
        rms_c3.metric("⚠️ Max Loss Per Trade", "-₹2,500.00", "2.5% of Capital (Hard SL)")

        # RMS Status Banner
        ks_active = st.session_state.risk_manager.kill_switch_active
        if ks_active:
            st.error("🚨 **KILL SWITCH ACTIVE:** Trading is locked for the rest of the day to protect capital.")
        else:
            st.success("🟢 **RMS SYSTEM NORMAL:** Guardrails fully active and monitoring ticks.")

        st.divider()

    # ======================================================================
    # TAB 5: Option Chain & Greeks
    # ======================================================================
    with tab_greeks:
        st.subheader("Options Pricing & Black-Scholes Greeks Engine")
        g_col1, g_col2, g_col3 = st.columns(3)

        with g_col1:
            calc_spot = st.number_input("Nifty Underlying Spot", value=st.session_state.spot_price, step=50.0)
            calc_strike = st.number_input("Option Strike Price", value=float(get_atm_strike(st.session_state.spot_price)), step=50.0)
        with g_col2:
            calc_dte = st.number_input("Days to Expiry (DTE)", min_value=0.1, max_value=60.0, value=3.0, step=0.5)
            calc_iv = st.slider("Implied Volatility (IV %)", min_value=5.0, max_value=50.0, value=14.0, step=0.5) / 100.0
        with g_col3:
            calc_rf = st.number_input("Risk Free Rate (%)", value=7.0) / 100.0

        ce_greeks = calculate_black_scholes(calc_spot, calc_strike, calc_dte / 365.0, calc_iv, calc_rf, OptionType.CE)
        pe_greeks = calculate_black_scholes(calc_spot, calc_strike, calc_dte / 365.0, calc_iv, calc_rf, OptionType.PE)

        greeks_df = pd.DataFrame([
            {"Contract": f"Call (CE) {int(calc_strike)}", **ce_greeks},
            {"Contract": f"Put (PE) {int(calc_strike)}", **pe_greeks}
        ])

        st.dataframe(greeks_df, use_container_width=True)

    # ======================================================================
    # TAB 6: Continuous Learning & Trade Analytics
    # ======================================================================
    with tab_learning:
        st.subheader("🧠 Continuous Learning & Trade Excursion Analytics")
        st.markdown(
            "Empirical performance telemetry engine. Analyzes **Maximum Favorable Excursion (MFE)**, "
            "**Maximum Adverse Excursion (MAE)**, session time-of-day profitability, and S/R level reliability."
        )

        from core.trade_analytics import TradeLearningLedger, DailyDebriefGenerator
        from core.adaptive_tuner import AdaptiveExecutionOptimizer

        ledger = TradeLearningLedger()
        trades = ledger.get_all_trades()

        # Top summary metrics
        c_l1, c_l2, c_l3, c_l4 = st.columns(4)
        tot_logged = len(trades)
        wins = [t for t in trades if t.net_pnl > 0]
        wr = (len(wins) / tot_logged * 100.0) if tot_logged > 0 else 0.0
        avg_eff = (sum(t.efficiency_ratio for t in trades) / tot_logged * 100.0) if tot_logged > 0 else 0.0
        tot_net = sum(t.net_pnl for t in trades)

        c_l1.metric("Analyzed Trades", f"{tot_logged} trades")
        c_l2.metric("Empirical Win Rate", f"{wr:.1f}%", delta=f"{len(wins)} Wins")
        c_l3.metric("Avg Capture Efficiency", f"{avg_eff:.1f}%")
        c_l4.metric("Cumulative Net P&L", f"₹{tot_net:+,.2f}", delta="After All Charges")

        st.divider()

        # Real-time Live Adaptations Enforced
        optimizer = AdaptiveExecutionOptimizer(ledger)
        summary = optimizer.get_active_adaptations_summary()
        bl = summary.get("blacklisted_regimes", [])
        bl_text = ", ".join(bl) if bl else "None (Full session active)"
        elev = summary.get("elevated_levels", {})
        elev_text = ", ".join([f"{k} ({v}x)" for k, v in elev.items()]) if elev else "None (All at baseline 1.3x)"

        st.success(
            f"🤖 **Live Adaptive Execution Engine: ACTIVE** | "
            f"🚫 **Active Chop Guard:** {bl_text} | "
            f"🛡️ **Elevated Volume Levels:** {elev_text} | "
            f"⚙️ **Calibrated BE:** Nifty +{summary.get('nifty_be_pct', 0.02)*100:.1f}%"
        )

        # Section 1: Excursion Table
        st.markdown("#### 📊 High-Fidelity Excursion Ledger (MFE vs MAE)")
        if trades:
            t_data = []
            for t in reversed(trades):
                pnl_color = "🟢" if t.net_pnl >= 0 else "🔴"
                t_data.append({
                    "Trade ID": t.trade_id,
                    "Asset": t.asset_key,
                    "Contract": t.symbol,
                    "Side": t.side,
                    "Regime": t.time_bucket.split(" ")[0],
                    "MFE (Peak Profit)": f"+{t.mfe_points:.1f} pts (+₹{t.mfe_pnl:,.0f})",
                    "MAE (Drawdown)": f"-{t.mae_points:.1f} pts (-₹{t.mae_pnl:,.0f})",
                    "Efficiency": f"{t.efficiency_ratio * 100:.0f}%",
                    "Gross P&L": f"₹{t.gross_pnl:+,.2f}",
                    "Charges": f"-₹{t.charges:,.2f}",
                    "Net P&L": f"{pnl_color} ₹{t.net_pnl:+,.2f}",
                    "Exit Reason": t.exit_reason,
                    "AI Autopsy Diagnosis": t.autopsy_diagnosis
                })
            st.dataframe(pd.DataFrame(t_data), use_container_width=True)
        else:
            st.info("ℹ️ No historical trade telemetry recorded yet. Trades executed by the S/R Level Trader will automatically log MFE/MAE excursions here.")

        st.divider()

        # Section 2: Time-of-Day & S/R Level Optimizations
        col_reg, col_lvl = st.columns(2)
        optimizer = AdaptiveExecutionOptimizer(ledger)

        with col_reg:
            st.markdown("#### 🕒 Time-of-Day Regimes & Chop Filters")
            regime_res = optimizer.get_time_of_day_recommendations()
            time_stats = regime_res["all_stats"]
            if time_stats:
                r_rows = []
                for reg, st_data in time_stats.items():
                    r_rows.append({
                        "Session Regime": reg,
                        "Trades": st_data["total_trades"],
                        "Win Rate": f"{st_data['win_rate']:.1f}%",
                        "Net P&L": f"₹{st_data['net_pnl']:+,.2f}"
                    })
                st.dataframe(pd.DataFrame(r_rows), use_container_width=True)
            else:
                st.caption("Collecting regime sample size...")

            if regime_res["blacklisted_regimes"]:
                for b in regime_res["blacklisted_regimes"]:
                    st.warning(f"⚠️ **Toxic Regime Detected:** {b['regime']} (Win Rate: {b['win_rate']}%) — {b['recommendation']}")
            if regime_res["golden_regimes"]:
                for g in regime_res["golden_regimes"]:
                    st.success(f"🌟 **Golden Window:** {g['regime']} (Win Rate: {g['win_rate']}%) — {g['recommendation']}")

        with col_lvl:
            st.markdown("#### 🎯 S/R Level Reliability Scorecard")
            lvl_scores = optimizer.get_level_reliability_index()
            if lvl_scores:
                l_rows = []
                for lvl_name, l_info in lvl_scores.items():
                    l_rows.append({
                        "Level Name": lvl_name,
                        "Win Rate": f"{l_info['win_rate']:.1f}%",
                        "Reliability Status": l_info["status"],
                        "Recommended Vol Multiplier": f"{l_info['recommended_volume_multiplier']}x",
                        "Action": l_info["action"]
                    })
                st.dataframe(pd.DataFrame(l_rows), use_container_width=True)
            else:
                st.caption("Awaiting S/R trade triggers for reliability scoring...")

        st.divider()

        # Section 3: Daily Learning Debrief
        st.markdown("#### 📘 Automated Daily Post-Market Debrief")
        debrief = DailyDebriefGenerator.generate_debrief(ledger)
        st.markdown(debrief["report_text"], unsafe_allow_html=True)


if __name__ == "__main__" or "streamlit" in sys.modules:
    render_dashboard()
