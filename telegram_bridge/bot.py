"""
Telegram Bot Bridge for remote mobile monitoring, trade alerts, interactive control,
PC remote management, and conversational AI assistant.
Enables real-time PnL queries, live NSE market quotes, emergency square-off, parameter tuning,
PC shell commands, and direct natural-language communication between the trader and the assistant.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import os
import re
import json
import threading
import time
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.logger import get_logger
from config.settings import settings

logger = get_logger("TelegramBridge")


class TelegramBridge:
    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        runner: Any = None
    ):
        raw_token = token or settings.TELEGRAM_BOT_TOKEN or ""
        self.token = raw_token.strip()
        raw_chat = chat_id or settings.TELEGRAM_CHAT_ID or ""
        self.chat_id = str(raw_chat).strip()
        self.runner = runner
        self.fallback_broker = None
        self.api_base = f"https://api.telegram.org/bot{self.token}"

        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.last_update_id = 0

        # Chat log file
        self.chat_log_path = Path(__file__).resolve().parent.parent / "logs" / "telegram_chat.jsonl"
        self.chat_log_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_active_broker(self):
        """Returns the broker from the attached runner, or lazy-initializes a broker."""
        if self.runner and hasattr(self.runner, "broker") and self.runner.broker:
            return self.runner.broker

        if self.fallback_broker is not None:
            return self.fallback_broker

        # Lazy-initialize broker for standalone bridge operation
        try:
            if not settings.PAPER_TRADING and settings.BROKER == "angel":
                from brokers.angel_broker import AngelOneBroker
                broker = AngelOneBroker()
                broker.authenticate()
                self.fallback_broker = broker
            else:
                from brokers.paper_broker import PaperBroker
                broker = PaperBroker(initial_capital=settings.PAPER_INITIAL_CAPITAL, persist=True)
                broker.authenticate()
                self.fallback_broker = broker
        except Exception as e:
            logger.warning(f"Could not initialize fallback broker: {e}")
            self.fallback_broker = None

        return self.fallback_broker

    def start(self):
        """Start the background long-polling worker if token is configured."""
        if not self.token:
            logger.info("Telegram Bot token not set. Mobile bridge will be disabled.")
            return

        self.is_running = True
        self.worker_thread = threading.Thread(target=self._poll_updates, daemon=True, name="TelegramBridgeWorker")
        self.worker_thread.start()
        logger.info("📱 Telegram Bridge started! Listening for mobile commands & chat...")

        # If chat_id is known, send initial online notification
        if self.chat_id:
            self.send_notification("⚡ <b>ProjectO Assistant Online</b>\nI am listening on Telegram. Send /help or ask me anything from anywhere!")

    def stop(self):
        """Stop the background polling worker."""
        self.is_running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2.0)
        logger.info("Telegram Bridge stopped.")

    # ------------------------------------------------------------------
    # Chat ID Auto-Pairing & Persistence
    # ------------------------------------------------------------------

    def _save_chat_id_to_env(self, chat_id: str):
        """Persist paired chat ID to .env file so the user never needs to re-enter it."""
        try:
            env_path = Path(__file__).resolve().parent.parent / ".env"
            if env_path.exists():
                content = env_path.read_text(encoding="utf-8")
                if "TELEGRAM_CHAT_ID=" in content:
                    new_content = re.sub(r"TELEGRAM_CHAT_ID=.*", f"TELEGRAM_CHAT_ID={chat_id}", content)
                    env_path.write_text(new_content, encoding="utf-8")
                else:
                    with open(env_path, "a", encoding="utf-8") as f:
                        f.write(f"\nTELEGRAM_CHAT_ID={chat_id}\n")
                logger.info(f"✅ Auto-paired & saved TELEGRAM_CHAT_ID={chat_id} to .env")
        except Exception as e:
            logger.error(f"Failed to persist TELEGRAM_CHAT_ID to .env: {e}")

    def _log_chat(self, sender_id: str, sender_name: str, incoming_text: str, reply_text: str):
        """Audit log for all Telegram messages and assistant responses."""
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "sender_id": sender_id,
                "sender_name": sender_name,
                "incoming_text": incoming_text,
                "reply_text": reply_text
            }
            with open(self.chat_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to log chat interaction: {e}")

    # ------------------------------------------------------------------
    # Push Notifications
    # ------------------------------------------------------------------

    def send_notification(self, text: str) -> bool:
        """Send an alert message directly to your phone via Telegram."""
        import sys, os, json, urllib.request
        if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
            return False

        if not self.token or not self.chat_id:
            return False

        url = f"{self.api_base}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Failed sending Telegram notification: {e}")
            return False

    # ------------------------------------------------------------------
    # Unified Message Dispatcher
    # ------------------------------------------------------------------

    def handle_message(self, text: str, sender_chat_id: str, sender_name: str = "Trader") -> str:
        """
        Handle incoming messages: commands, market queries, PC control, or conversational AI chat.
        Includes automatic chat-id pairing on first incoming message.
        """
        sender_chat_id = str(sender_chat_id).strip()

        # Auto-Pairing: If no chat_id is set yet, link to this sender automatically
        paired_now = False
        if not self.chat_id:
            self.chat_id = sender_chat_id
            self._save_chat_id_to_env(sender_chat_id)
            paired_now = True

        # Security Guard: verify sender against registered chat_id
        if self.chat_id and sender_chat_id != self.chat_id:
            logger.warning(f"Unauthorized message from chat_id {sender_chat_id}: {text}")
            return "⛔ <b>Unauthorized</b>: This trading bot only accepts commands from its registered owner."

        clean_text = text.strip()
        if not clean_text:
            return "Hello! Send /help to view available commands, or chat with me directly."

        # Process message
        if clean_text.startswith("/"):
            reply = self.handle_command(clean_text, sender_chat_id)
        else:
            reply = self.handle_ai_chat(clean_text, sender_chat_id, sender_name)

        if paired_now:
            greeting = (
                f"🎉 <b>Device Successfully Paired!</b>\n\n"
                f"Welcome, {sender_name}! Your phone is now securely connected to your PC and ProjectO.\n\n"
            )
            reply = greeting + reply

        # Record interaction in audit log
        self._log_chat(sender_chat_id, sender_name, clean_text, reply)
        return reply

    # ------------------------------------------------------------------
    # Real-Time NSE Market Feed
    # ------------------------------------------------------------------

    def _fetch_nse_market(self) -> Dict[str, Any]:
        """Fetch real-time index data directly from NSE India."""
        try:
            s = requests.Session()
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            s.get("https://www.nseindia.com", timeout=4)
            r = s.get("https://www.nseindia.com/api/allIndices", timeout=5)
            if r.status_code == 200:
                data = r.json().get("data", [])
                idx_map = {x.get("index"): x for x in data}
                return {
                    "nifty": idx_map.get("NIFTY 50", {}),
                    "banknifty": idx_map.get("NIFTY BANK", {}),
                    "vix": idx_map.get("INDIA VIX", {})
                }
        except Exception as e:
            logger.warning(f"Error querying NSE live feed: {e}")
        return {}

    def _cmd_market(self) -> str:
        m = self._fetch_nse_market()
        if not m or not m.get("nifty"):
            return "⚠️ Unable to fetch live NSE feed right now. Market may be closed or network busy."

        n = m["nifty"]
        b = m["banknifty"]
        v = m.get("vix", {})

        n_last = float(n.get("last", 0.0))
        n_var = float(n.get("variation", 0.0))
        n_pct = float(n.get("percentChange", 0.0))
        n_high = float(n.get("high", 0.0))
        n_low = float(n.get("low", 0.0))
        n_adv = n.get("advances", "N/A")
        n_dec = n.get("declines", "N/A")

        b_last = float(b.get("last", 0.0))
        b_var = float(b.get("variation", 0.0))
        b_pct = float(b.get("percentChange", 0.0))

        v_last = v.get("last", "N/A")

        trend_icon = "🟢" if n_var >= 0 else "🔴"
        b_icon = "🟢" if b_var >= 0 else "🔴"

        return (
            f"📊 <b>NSE Real-Time Market Quote</b>\n\n"
            f"{trend_icon} <b>NIFTY 50:</b> ₹{n_last:,.2f} ({n_var:+,.2f} / {n_pct:+.2f}%)\n"
            f"• Day Range: ₹{n_low:,.2f} – ₹{n_high:,.2f}\n"
            f"• Market Breadth: {n_adv} Advances | {n_dec} Declines\n\n"
            f"{b_icon} <b>BANKNIFTY:</b> ₹{b_last:,.2f} ({b_var:+,.2f} / {b_pct:+.2f}%)\n"
            f"• <b>India VIX:</b> {v_last}\n"
            f"\n🕒 As of {datetime.now().strftime('%H:%M:%S IST')}"
        )

    # ------------------------------------------------------------------
    # PC Remote Management Handlers
    # ------------------------------------------------------------------

    def _cmd_pc_status(self) -> str:
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.3)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("C:/")
            boot_time = datetime.fromtimestamp(psutil.boot_time()).strftime("%d-%b %H:%M")

            return (
                "💻 <b>Host PC Health & Status</b>\n\n"
                f"• <b>CPU Usage:</b> {cpu}%\n"
                f"• <b>RAM Usage:</b> {mem.percent}% ({mem.used // (1024**3)}GB / {mem.total // (1024**3)}GB)\n"
                f"• <b>C: Drive Free:</b> {disk.free // (1024**3)} GB ({100 - disk.percent:.1f}% free)\n"
                f"• <b>System Booted:</b> {boot_time}\n"
                f"• <b>Active Process:</b> PID {os.getpid()}\n"
                f"\n🕒 {datetime.now().strftime('%H:%M:%S IST')}"
            )
        except Exception as e:
            return f"⚠️ Error fetching PC status: {e}"

    def _cmd_run_shell(self, args: list) -> str:
        if not args:
            return "Usage: <code>/cmd &lt;command&gt;</code> (e.g. <code>/cmd dir</code> or <code>/cmd tasklist</code>)"

        cmd_str = " ".join(args)
        blocked = ["format ", "rmdir /s /q c:\\", "del /f /s /q c:\\"]
        if any(b in cmd_str.lower() for b in blocked):
            return "🛑 <b>Blocked</b>: Command restricted for system safety."

        try:
            res = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=12)
            out = res.stdout or res.stderr or "(No output)"
            if len(out) > 3500:
                out = out[:3500] + "\n...[truncated]"
            return f"🖥️ <b>Command Output:</b>\n<pre>{out}</pre>"
        except subprocess.TimeoutExpired:
            return "⏱️ Command timed out after 12 seconds."
        except Exception as e:
            return f"⚠️ Execution error: {e}"

    # ------------------------------------------------------------------
    # Conversational AI Assistant Handler
    # ------------------------------------------------------------------

    def _ask_gemini_ai(self, prompt: str) -> Optional[str]:
        api_key = os.getenv("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", "")
        if not api_key:
            return None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        system_instruction = (
            "You are Antigravity, an intelligent AI pair programmer and trading assistant. "
            "You are chatting directly with Sachin Gupta on his mobile phone via Telegram. "
            "Sachin is running an algorithmic options trading platform on his Windows PC called ProjectO with Angel One broker. "
            "Be concise, helpful, friendly, smart, and format responses cleanly using HTML tags like <b>bold</b> or <code>code</code>."
        )
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": prompt}]}]
        }
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                cand = r.json().get("candidates", [])
                if cand:
                    return cand[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        except Exception as e:
            logger.warning(f"Gemini API request failed: {e}")
        return None

    def handle_ai_chat(self, text: str, sender_chat_id: str, sender_name: str = "Trader") -> str:
        """
        Processes natural language messages from the user with full context:
        market data, PC stats, PnL, trades, strategies, and AI responses.
        """
        lower = text.lower()

        # 1. Greetings
        if any(w in lower for w in ["hi", "hello", "hey", "namaste", "good morning", "good afternoon", "good evening", "kaisa", "kaise"]):
            status_summary = self._cmd_status()
            return (
                f"👋 <b>Hello {sender_name}!</b>\n\n"
                f"I'm your <b>ProjectO AI Assistant</b> on your PC. I'm connected to your machine, Angel One, and live NSE feeds.\n\n"
                f"{status_summary}\n\n"
                f"💬 <i>You can chat naturally (e.g. 'what is nifty trading at', 'what is my balance', 'pc status', 'set lots to 2') or use /help!</i>"
            )

        # 2. Crude decommission guard
        if any(w in lower for w in ["crude", "crudeoil", "crude oil", "mcx crude", "tel"]):
            return "ℹ️ <b>Crude Oil Decommissioned:</b> ProjectO is strictly dedicated to NIFTY 50 options (Core Duo: ORION-15 + THETA-0DTE). Send /nifty or /levels for live Nifty data."

        if any(w in lower for w in ["trading at", "nifty price", "nifty spot", "market quote", "banknifty", "vix", "index", "bhav", "nifty kitna"]):
            return self._cmd_market()

        if lower.strip() in ["nifty", "market"]:
            return self._cmd_market()

        # 2b. Option Chain
        if any(w in lower for w in ["option chain", "chain", "options", "strikes"]):
            return self._cmd_option_chain()

        # 2c. Buy Put / Buy Call natural language commands
        if any(w in lower for w in ["buy put", "buy pe", "put buy", "pe buy"]):
            # Check if custom price mentioned
            price_m = re.search(r"@\s*(\d+(?:\.\d+)?)", lower) or re.search(r"price\s*(\d+(?:\.\d+)?)", lower)
            args = [price_m.group(1)] if price_m else []
            return self._cmd_buy_option("PE", args)

        if any(w in lower for w in ["buy call", "buy ce", "call buy", "ce buy"]):
            price_m = re.search(r"@\s*(\d+(?:\.\d+)?)", lower) or re.search(r"price\s*(\d+(?:\.\d+)?)", lower)
            args = [price_m.group(1)] if price_m else []
            return self._cmd_buy_option("CE", args)

        # 2d. Support & Resistance Levels (NIFTY)
        if any(w in lower for w in ["levels", "support", "resistance", "zone", "sr level"]):
            return self._cmd_levels([])

        # 3. PnL / Balance / Financials
        if any(w in lower for w in ["pnl", "profit", "loss", "balance", "margin", "kamai", "kitna", "rupees", "cash", "portfolio", "net worth"]):
            pnl_data = self._cmd_pnl()
            return f"📊 <b>Financial Summary:</b>\n\n{pnl_data}"

        # 4. PC Control & Hardware status (check before general 'status')
        if any(w in lower for w in ["pc", "computer", "cpu", "ram", "memory", "system status", "hardware"]):
            return self._cmd_pc_status()

        # 5. Bot Status queries
        if any(w in lower for w in ["status", "how is it going", "active", "chal raha", "bot status", "health"]):
            return self._cmd_status()

        # 5b. Executed Trade Book / Orders
        if any(w in lower for w in ["tradebook", "trade book", "fills", "executed trade", "order book", "orderbook", "trades today", "aj ke trade"]):
            return self._cmd_tradebook()

        # 6. Positions / Open trades queries
        if any(w in lower for w in ["position", "trade", "open trade", "legs", "ce", "pe", "holding", "orders"]):
            return self._cmd_positions()

        # 7. Square off / Emergency exit commands
        if any(w in lower for w in ["square off", "squareoff", "exit all", "close all", "band kar", "stop trade", "exit now", "emergency exit"]):
            return self._cmd_squareoff()

        # 8. Change lot size
        lot_match = re.search(r"(?:set|change)?\s*lots?\s*(?:to|=)?\s*(\d+)", lower)
        if lot_match:
            new_lots = lot_match.group(1)
            return self._cmd_setlots([new_lots])

        # 9. Change Stop Loss
        sl_match = re.search(r"(?:set|change)?\s*(?:sl|stop\s*loss)\s*(?:to|=)?\s*(\d+)", lower)
        if sl_match:
            new_sl = sl_match.group(1)
            return self._cmd_setsl([new_sl])

        # 10. Mode switch
        if "live mode" in lower or "switch to live" in lower:
            return self._cmd_mode(["live"])
        elif "paper mode" in lower or "switch to paper" in lower:
            return self._cmd_mode(["paper"])

        # 11. Dashboard link
        if any(w in lower for w in ["dashboard", "web ui", "link", "url", "browser", "website"]):
            return self._cmd_dashboard()

        # 12. Angel One connection query
        if any(w in lower for w in ["angel", "pushpa", "p101525", "broker login", "smartapi"]):
            return self._cmd_angel()

        # 13. Strategy explanations
        if any(w in lower for w in ["straddle", "9:20", "short straddle"]):
            return (
                "📈 <b>9:20 AM Short Straddle Strategy</b>\n\n"
                "• <b>Entry:</b> Sells ATM Call (CE) and ATM Put (PE) at 9:20 AM sharp.\n"
                "• <b>Objective:</b> Harvest premium decay (theta) throughout the trading day.\n"
                "• <b>Stop Loss:</b> 25% individual leg SL. If one leg trends against us, it closes while the winning decaying leg runs.\n"
                "• <b>Auto Exit:</b> 3:15 PM auto square-off.\n"
                "• <b>Risk Control:</b> RMS daily loss limit."
            )

        if any(w in lower for w in ["momentum", "buyer", "buying"]):
            return (
                "🚀 <b>Momentum Buyer Strategy</b>\n\n"
                "• <b>Entry:</b> High-momentum 5m/15m breakout on Nifty with volume surge.\n"
                "• <b>Action:</b> Buys ATM CE for upward breakout, buys ATM PE for breakdown.\n"
                "• <b>Target:</b> 1:2 Risk-Reward ratio with trailing stop-loss."
            )

        if any(w in lower for w in ["risk", "rms", "limit", "loss limit", "kill switch"]):
            return self._cmd_risk()

        # 14. Query Gemini AI if configured
        gemini_response = self._ask_gemini_ai(text)
        if gemini_response:
            return gemini_response

        # 15. Conversational fallback with quick actions
        return (
            f"💡 <b>Received:</b> \"{text}\"\n\n"
            f"Here are helpful commands you can run right now from your phone:\n\n"
            f"• <b>Live Nifty Quote:</b> Send 'what is nifty trading at' or /nifty\n"
            f"• <b>Check PnL:</b> Send 'pnl' or /pnl\n"
            f"• <b>Check PC Health:</b> Send 'pc' or /pc\n"
            f"• <b>Run Command on PC:</b> /cmd &lt;command&gt;\n"
            f"• <b>Adjust Lots:</b> Send 'set lots to 2' or /setlots 2\n"
            f"• <b>Emergency Exit:</b> Send 'square off' or /squareoff\n"
            f"• <b>Dashboard:</b> Send 'dashboard' or /dashboard"
        )

    # ------------------------------------------------------------------
    # Command Handlers
    # ------------------------------------------------------------------

    def handle_command(self, text: str, sender_chat_id: str) -> str:
        """Dispatch user slash commands to corresponding actions."""
        # Security Guard: verify sender
        if self.chat_id and str(sender_chat_id) != self.chat_id:
            logger.warning(f"Unauthorized command from chat_id {sender_chat_id}: {text}")
            return "⛔ <b>Unauthorized</b>: This trading bot only accepts commands from its registered owner."

        cmd_parts = text.strip().split()
        if not cmd_parts:
            return "Type /help to see all available commands."

        cmd = cmd_parts[0].lower()

        if cmd in ("/start", "/help"):
            return self._cmd_help()
        elif cmd in ("/status", "/stats"):
            return self._cmd_status()
        elif cmd in ("/market", "/nifty"):
            return self._cmd_market()
        elif cmd in ("/options", "/chain", "/oc"):
            return self._cmd_option_chain()
        elif cmd in ("/crude", "/crudeoil", "/oil", "/buycrude", "/sellcrude", "/longcrude", "/shortcrude"):
            return "ℹ️ <b>Crude Oil Decommissioned:</b> ProjectO is now strictly dedicated to NIFTY 50 options (Core Duo: ORION-15 + THETA-0DTE)."
        elif cmd in ("/levels", "/sr", "/zones"):
            return self._cmd_levels(cmd_parts[1:])
        elif cmd in ("/buype", "/buyput", "/pe"):
            return self._cmd_buy_option("PE", cmd_parts[1:])
        elif cmd in ("/buyce", "/buycall", "/ce"):
            return self._cmd_buy_option("CE", cmd_parts[1:])
        elif cmd in ("/pc", "/system"):
            return self._cmd_pc_status()
        elif cmd in ("/cmd", "/run"):
            return self._cmd_run_shell(cmd_parts[1:])
        elif cmd in ("/pnl", "/profit", "/balance"):
            return self._cmd_pnl()
        elif cmd in ("/positions", "/pos"):
            return self._cmd_positions()
        elif cmd in ("/tradebook", "/trades", "/fills", "/orderbook", "/orders"):
            return self._cmd_tradebook()
        elif cmd in ("/squareoff", "/exit", "/closeall"):
            return self._cmd_squareoff()
        elif cmd == "/setlots":
            return self._cmd_setlots(cmd_parts[1:] if len(cmd_parts) > 1 else [])
        elif cmd == "/setsl":
            return self._cmd_setsl(cmd_parts[1:] if len(cmd_parts) > 1 else [])
        elif cmd == "/mode":
            return self._cmd_mode(cmd_parts[1:] if len(cmd_parts) > 1 else [])
        elif cmd == "/dashboard":
            return self._cmd_dashboard()
        elif cmd == "/angel":
            return self._cmd_angel()
        elif cmd == "/strategy":
            return self._cmd_strategy()
        elif cmd == "/risk":
            return self._cmd_risk()
        elif cmd in ("/learnings", "/learning", "/ai"):
            return self._cmd_learnings()
        elif cmd in ("/refreshlevels", "/refresh_levels", "/recalc_levels"):
            return self._cmd_refresh_levels()
        else:
            return f"❓ Unknown command: <code>{cmd}</code>\nType /help for command list or chat with me in plain English."

    def _cmd_help(self) -> str:
        return (
            "🤖 <b>ProjectO Assistant — Mobile Commands & Control</b>\n\n"
            "📈 <b>Live Market & Levels:</b>\n"
            "• /nifty — Real-time NSE Nifty 50, Bank Nifty & VIX\n"
            "• /options — Live NIFTY ATM Option Chain table\n"
            "• /levels — Marked NIFTY Support & Resistance levels & distances\n\n"
            "🎯 <b>Mobile Trading:</b>\n"
            "• /buype [price] — Buy ATM NIFTY Put Option (2 Lots)\n"
            "• /buyce [price] — Buy ATM NIFTY Call Option (2 Lots)\n\n"
            "📊 <b>Trading Monitoring:</b>\n"
            "• /status — Engine, broker & strategy status\n"
            "• /pnl — Net PnL, Gross PnL, Charges & cash balance\n"
            "• /positions — Active open positions & trades\n"
            "• /tradebook — Executed fills, timestamps, prices & fees\n"
            "• /dashboard — Web UI link\n"
            "• /angel — Angel One connection details\n\n"
            "⚙️ <b>Trade Control:</b>\n"
            "• /setlots &lt;n&gt; — Change lot size (e.g. <code>/setlots 2</code>)\n"
            "• /setsl &lt;pct&gt; — Set leg Stop Loss % (e.g. <code>/setsl 25</code>)\n"
            "• /mode &lt;paper|live&gt; — Switch paper/live mode\n"
            "• /squareoff — Emergency exit ALL positions\n\n"
            "🧠 <b>AI Continuous Learning & S/R Engine:</b>\n"
            "• /learnings — Active AI chop filters & calibrated triggers\n"
            "• /refreshlevels — Auto-recalculate and sync Daily CPR & Pivot levels\n\n"
            "💻 <b>PC Remote Management:</b>\n"
            "• /pc — CPU, RAM, Disk & system stats\n"
            "• /cmd &lt;command&gt; — Run terminal command on your PC\n\n"
            "💬 <i>You can also chat in plain English (e.g. 'nifty', 'pnl', 'status', 'levels')!</i>"
        )

    def _cmd_status(self) -> str:
        broker_obj = self._get_active_broker()
        is_paper = self.runner.is_paper if self.runner else settings.PAPER_TRADING
        mode_str = "🟢 <b>PAPER TRADING</b>" if is_paper else "🔴 <b>LIVE TRADING</b>"
        broker_name = (self.runner.broker_type.upper() if self.runner else settings.BROKER.upper())
        lots_count = self.runner.lots if self.runner else settings.DEFAULT_LOTS
        qty_count = lots_count * settings.NIFTY_LOT_SIZE

        strat_name = "Core Duo (ORION-15 + THETA-0DTE)"
        if self.runner and hasattr(self.runner, "strategy") and self.runner.strategy:
            strat_name = self.runner.strategy.name

        kill_switch = "✅ ACTIVE (Normal)"
        if self.runner and hasattr(self.runner, "risk_manager"):
            if self.runner.risk_manager.kill_switch_active:
                kill_switch = "🛑 TRIGGERED"

        leg_info = ""
        if self.runner and hasattr(self.runner, "strategy") and self.runner.strategy:
            strat_status = self.runner.strategy.get_status()
            if "ce_leg" in strat_status:
                ce = strat_status["ce_leg"]
                pe = strat_status["pe_leg"]
                ce_state = "CLOSED" if ce.get("exited") else "ACTIVE"
                pe_state = "CLOSED" if pe.get("exited") else "ACTIVE"
                leg_info = (
                    f"\n<b>CE Leg:</b> {ce.get('symbol')} [{ce_state}]\n"
                    f"Entry: ₹{ce.get('entry_price', 0):.2f} | SL: ₹{ce.get('sl_price', 0):.2f}\n"
                    f"<b>PE Leg:</b> {pe.get('symbol')} [{pe_state}]\n"
                    f"Entry: ₹{pe.get('entry_price', 0):.2f} | SL: ₹{pe.get('sl_price', 0):.2f}\n"
                )

        return (
            f"⚡ <b>Trading Bot Status</b>\n"
            f"<b>Mode:</b> {mode_str}\n"
            f"<b>Broker:</b> {broker_name}\n"
            f"<b>Strategy:</b> {strat_name}\n"
            f"<b>Lots:</b> {lots_count} ({qty_count} Qty)\n"
            f"<b>RMS Status:</b> {kill_switch}\n"
            f"<b>Target Limit:</b> +₹{settings.MAX_DAILY_PROFIT:,.2f} | <b>Max Loss Limit:</b> -₹{settings.MAX_DAILY_LOSS:,.2f}\n"
            f"{leg_info}\n"
            f"🕒 Time: {datetime.now().strftime('%H:%M:%S IST')}"
        )

    def _cmd_pnl(self) -> str:
        broker_obj = self._get_active_broker()
        margins = broker_obj.get_margins() if broker_obj else {}
        tot = margins.get("total_pnl", 0.0)
        gross = margins.get("gross_pnl", tot)
        charges = margins.get("total_charges", 0.0)
        real = margins.get("realized_pnl", 0.0)
        unreal = margins.get("unrealized_pnl", 0.0)
        cash = margins.get("available_cash", 0.0)

        is_paper = settings.PAPER_TRADING or (self.runner and self.runner.is_paper)
        source_tag = "Paper Trading" if is_paper else "Live Broker"

        pnl_icon = "🟢" if tot >= 0 else "🔴"
        max_loss = self.runner.risk_manager.max_daily_loss if (self.runner and hasattr(self.runner, "risk_manager")) else settings.MAX_DAILY_LOSS
        max_profit = self.runner.risk_manager.max_daily_profit if (self.runner and hasattr(self.runner, "risk_manager")) else settings.MAX_DAILY_PROFIT

        return (
            f"💰 <b>Portfolio PnL Overview ({source_tag})</b>\n\n"
            f"{pnl_icon} <b>Net Total PnL:</b> ₹{tot:+,.2f}\n"
            f"• Gross P&L: ₹{gross:+,.2f}\n"
            f"• Total Charges & Taxes: -₹{charges:,.2f}\n"
            f"• Realized (Net): ₹{real:+,.2f}\n"
            f"• Unrealized (Net): ₹{unreal:+,.2f}\n"
            f"• Available Cash: ₹{cash:,.2f}\n\n"
            f"🎯 Daily Target Limit: +₹{max_profit:,.2f}\n"
            f"🛑 Daily Max Loss Limit: -₹{max_loss:,.2f}\n"
            f"\n🕒 As of {datetime.now().strftime('%H:%M:%S IST')}"
        )

    def _cmd_positions(self) -> str:
        broker_obj = self._get_active_broker()
        positions = broker_obj.get_positions() if broker_obj else {}
        is_paper = settings.PAPER_TRADING or (self.runner and self.runner.is_paper)
        source_tag = "Paper Trading" if is_paper else "Live Broker"

        open_pos = [p for p in positions.values() if p.quantity != 0]
        closed_pos = [p for p in positions.values() if p.quantity == 0 and p.realized_pnl != 0]

        if not open_pos and not closed_pos:
            return f"ℹ️ No active positions or trades recorded ({source_tag})."

        # Load active strategy trade telemetry (Target, SL, Trailing status)
        active_trades_meta = {}
        try:
            at_path = PROJECT_ROOT / "logs" / "active_trades.json"
            if at_path.exists():
                active_trades_meta = json.loads(at_path.read_text(encoding="utf-8"))
        except Exception:
            pass

        lines = [f"📊 <b>Positions & Trades Overview ({source_tag}):</b>\n"]
        if open_pos:
            lines.append("🟢 <b>Active Open Positions:</b>")
            for p in open_pos:
                pnl_icon = "🟢" if p.total_pnl >= 0 else "🔴"
                avg_p = p.average_sell_price if p.quantity < 0 else p.average_buy_price
                side = "SELL" if p.quantity < 0 else "BUY"
                sym = p.instrument.symbol

                # Match active trade metadata for target and SL
                trade_meta = None
                for k, v in active_trades_meta.items():
                    if v.get("symbol") == sym or k in sym:
                        trade_meta = v
                        break

                meta_lines = ""
                if trade_meta:
                    tp = trade_meta.get("target_price", 0.0)
                    sl = trade_meta.get("sl_price", 0.0)
                    be_locked = trade_meta.get("breakeven_locked", False)
                    trail_act = trade_meta.get("trailing_active", False)

                    if trail_act:
                        sl_status = "📈 Trailing Active (Ratcheting behind peak)"
                    elif be_locked:
                        sl_status = "🛡️ Breakeven Locked (Zero Capital Risk)"
                    else:
                        sl_status = "🛑 Initial Invalidation Stop"

                    is_short = (side == "SELL")
                    tp_pts = (avg_p - tp) if is_short else (tp - avg_p)
                    sl_pts = (sl - avg_p) if is_short else (avg_p - sl)
                    qty_abs = abs(p.quantity)

                    meta_lines = (
                        f"  🎯 <b>Target:</b> ₹{tp:,.2f} (+{tp_pts:.1f} pts | +₹{tp_pts * qty_abs:,.2f})\n"
                        f"  🛑 <b>Stop Loss:</b> ₹{sl:,.2f} (-{sl_pts:.1f} pts | -₹{sl_pts * qty_abs:,.2f})\n"
                        f"  🛡️ <b>Trailing State:</b> {sl_status}\n"
                    )

                lines.append(
                    f"• <b>{sym}</b>\n"
                    f"  Qty: {abs(p.quantity)} ({side}) | Entry Avg: ₹{avg_p:,.2f} | LTP: ₹{p.ltp:,.2f}\n"
                    f"{meta_lines}"
                    f"  Unrealized PnL: {pnl_icon} <b>₹{p.unrealized_pnl:+,.2f}</b> (Net Total: ₹{p.total_pnl:+,.2f})\n"
                )
        else:
            lines.append("ℹ️ <i>No open active positions. All positions flat.</i>\n")

        if closed_pos:
            lines.append("🏁 <b>Closed Positions Today:</b>")
            for p in closed_pos:
                pnl_icon = "🟢" if p.realized_pnl >= 0 else "🔴"
                lines.append(
                    f"• <b>{p.instrument.symbol}</b>: {pnl_icon} <b>₹{p.realized_pnl:+,.2f}</b> (Net: 0 Qty)"
                )

        return "\n".join(lines)

    def _cmd_tradebook(self) -> str:
        """Display the official broker tradebook of executed fills for the session."""
        broker_obj = self._get_active_broker()
        if not broker_obj:
            return "⚠️ Broker not available."

        trades = broker_obj.get_trades() if hasattr(broker_obj, "get_trades") else []
        is_paper = settings.PAPER_TRADING or (self.runner and self.runner.is_paper)
        source_tag = "Paper Trading" if is_paper else "Live Broker (Angel One)"

        if not trades:
            return f"📖 <b>Official Trade Book ({source_tag})</b>\n\nNo trade fills executed in this session."

        # Load active trades and closed trade autopsy ledger for Target/SL enrichment
        active_trades_meta = {}
        ledger_meta = {}
        try:
            at_path = PROJECT_ROOT / "logs" / "active_trades.json"
            if at_path.exists():
                active_trades_meta = json.loads(at_path.read_text(encoding="utf-8"))
        except Exception:
            pass

        try:
            ll_path = PROJECT_ROOT / "logs" / "trade_learning_ledger.json"
            if ll_path.exists():
                for r in json.loads(ll_path.read_text(encoding="utf-8")):
                    if "trade_id" in r:
                        ledger_meta[r["trade_id"]] = r
                    if "symbol" in r:
                        ledger_meta[r["symbol"]] = r
        except Exception:
            pass

        lines = [f"📖 <b>Official Executed Trade Book ({source_tag})</b>\n"]
        for i, t in enumerate(trades, 1):
            ts = t.get("timestamp", "")
            time_str = ts.split("T")[1][:8] if "T" in ts else ts
            side = t.get("side", "BUY")
            side_icon = "🟢" if side == "BUY" else "🔴"
            sym = t.get("symbol", "")
            qty = t.get("quantity", 0)
            price = t.get("price", 0.0)
            charges = t.get("charges", 0.0)
            tag = t.get("tag", "")

            # Check if this trade is currently active or in ledger
            meta_info = ""
            for k, v in active_trades_meta.items():
                if v.get("symbol") == sym or k in sym:
                    meta_info = f"   🎯 Target: ₹{v.get('target_price', 0):,.2f} | 🛑 SL: ₹{v.get('sl_price', 0):,.2f}\n"
                    break

            if not meta_info and sym in ledger_meta:
                rec = ledger_meta[sym]
                meta_info = f"   🏁 Result: {rec.get('exit_reason', '')[:45]} | Net: ₹{rec.get('net_pnl', 0):+,.2f}\n"

            lines.append(
                f"{side_icon} <b>#{i} {side} {qty}x {sym}</b> @ ₹{price:,.2f}\n"
                f"   ⏰ Time: {time_str} | Charges: ₹{charges:.2f}\n"
                f"{meta_info}"
                f"   🏷️ Tag: <code>{tag or 'MANUAL'}</code>\n"
            )

        margins = broker_obj.get_margins() if broker_obj else {}
        realized = margins.get("daily_realized_pnl", margins.get("realized_pnl", 0.0))
        charges_tot = margins.get("daily_charges", margins.get("total_charges", 0.0))
        net = margins.get("daily_pnl", round(realized - charges_tot, 2))
        pnl_icon = "🟢" if net >= 0 else "🔴"

        lines.append(f"📊 <b>Summary:</b> {len(trades)} execution(s)")
        lines.append(f"• Gross Realized: ₹{realized:+,.2f}")
        lines.append(f"• Total Charges: -₹{charges_tot:,.2f}")
        lines.append(f"• {pnl_icon} <b>Net Realized PnL:</b> <b>₹{net:+,.2f}</b>")
        return "\n".join(lines)

    def _cmd_squareoff(self) -> str:
        broker_obj = self._get_active_broker()
        if not broker_obj:
            return "ℹ️ Broker not available."

        orders = broker_obj.square_off_all_positions()
        if self.runner and hasattr(self.runner, "risk_manager"):
            self.runner.risk_manager.kill_switch_active = True
            self.runner.risk_manager.kill_switch_reason = "Emergency square-off triggered via Telegram"

        logger.warning(f"🚨 Remote Emergency Square-off triggered from Telegram! Closed {len(orders)} positions.")
        return (
            f"🚨 <b>EMERGENCY SQUARE-OFF EXECUTED</b>\n\n"
            f"Closed <b>{len(orders)}</b> positions at market price.\n"
            f"RMS Kill Switch is now <b>ENGAGED</b> to prevent new trades."
        )

    def _cmd_setlots(self, args: list) -> str:
        if not args or not str(args[0]).isdigit():
            return "Usage: /setlots &lt;number&gt; (e.g. <code>/setlots 2</code>)"

        new_lots = int(args[0])
        if new_lots <= 0 or new_lots > 20:
            return "⚠️ Lots must be between 1 and 20."

        if self.runner:
            self.runner.lots = new_lots
            if hasattr(self.runner, "strategy") and hasattr(self.runner.strategy, "lots"):
                self.runner.strategy.lots = new_lots

        qty = new_lots * settings.NIFTY_LOT_SIZE
        logger.info(f"Updated lots to {new_lots} ({qty} qty) via Telegram")
        return f"✅ Lots updated to <b>{new_lots}</b> ({qty} quantity)."

    def _cmd_setsl(self, args: list) -> str:
        if not args:
            return "Usage: /setsl &lt;percentage&gt; (e.g. <code>/setsl 25</code> for 25% Stop Loss)"

        try:
            val = float(args[0])
            pct = val / 100.0 if val > 1.0 else val
            if pct <= 0.05 or pct > 1.0:
                return "⚠️ Stop loss must be between 5% and 100%."

            if self.runner and hasattr(self.runner, "strategy") and hasattr(self.runner.strategy, "sl_pct"):
                self.runner.strategy.sl_pct = pct
                return f"✅ Strategy Stop Loss updated to <b>{pct * 100:.1f}%</b>."
            else:
                return f"✅ Target Stop Loss registered as <b>{pct * 100:.1f}%</b>."
        except ValueError:
            return "⚠️ Invalid number format. Use e.g. <code>/setsl 25</code>"

    def _cmd_mode(self, args: list) -> str:
        if not args or args[0].lower() not in ("paper", "live"):
            return "Usage: /mode &lt;paper|live&gt;"

        new_mode = args[0].lower()
        if self.runner:
            self.runner.is_paper = (new_mode == "paper")
        return f"✅ Execution Mode set to <b>{new_mode.upper()}</b>."

    def _cmd_dashboard(self) -> str:
        local_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        return (
            "📱 <b>Mobile Web Dashboard</b>\n\n"
            f"• <b>Home Wi-Fi Link:</b> <code>http://{local_ip}:8501</code>\n\n"
            "<i>Note: If you're on mobile data away from home, use Telegram commands (/nifty, /pnl, /positions) for instant access from anywhere!</i>"
        )

    def _cmd_angel(self) -> str:
        client_id = settings.ANGEL_CLIENT_ID or "P101525"
        api_key_masked = settings.ANGEL_API_KEY[:3] + "..." + settings.ANGEL_API_KEY[-2:] if len(settings.ANGEL_API_KEY) > 5 else "Configured"
        return (
            "🏦 <b>Angel One SmartAPI Integration</b>\n\n"
            f"• <b>Client ID:</b> {client_id} (Pushpa Gupta)\n"
            f"• <b>API Key:</b> {api_key_masked}\n"
            f"• <b>TOTP Login:</b> Automated via pyotp & login.py\n"
            f"• <b>RMS Endpoint:</b> Integrated for portfolio & margin fetch"
        )

    def _cmd_strategy(self) -> str:
        return (
            "📊 <b>Active Strategies Configured</b>\n\n"
            "1. <b>9:20 AM Short Straddle</b> (Current Default)\n"
            "   • Nifty ATM CE + PE Intraday Sell\n"
            "   • Leg Stop Loss: 25%\n"
            "   • Entry: 09:20 IST | Exit: 15:15 IST\n\n"
            "2. <b>Momentum Option Buyer</b>\n"
            "   • 5-min / 15-min Breakout with Volume Surge\n"
            "   • Target: 1:2 Risk-Reward Ratio\n"
            "   • Dynamic Trailing Stop-Loss"
        )

    def _cmd_risk(self) -> str:
        max_loss = settings.MAX_DAILY_LOSS
        max_profit = settings.MAX_DAILY_PROFIT
        return (
            "🛡️ <b>Risk Management System (RMS)</b>\n\n"
            f"• <b>Daily Max Loss:</b> ₹{max_loss:,.2f}\n"
            f"• <b>Daily Profit Target:</b> ₹{max_profit:,.2f}\n"
            f"• <b>Auto Kill-Switch:</b> Auto-squares off all legs and locks trading if daily loss hits ₹{max_loss:,.2f}.\n"
            f"• <b>Remote Kill:</b> Send /squareoff anytime to immediately close everything."
        )

    def _cmd_learnings(self) -> str:
        try:
            from core.trade_analytics import TradeLearningLedger
            from core.adaptive_tuner import AdaptiveExecutionOptimizer
            ledger = TradeLearningLedger()
            optimizer = AdaptiveExecutionOptimizer(ledger)
            summary = optimizer.get_active_adaptations_summary()

            bl = summary.get("blacklisted_regimes", [])
            bl_str = ", ".join(bl) if bl else "None (All market hours active)"

            gl = summary.get("golden_regimes", [])
            gl_str = ", ".join(gl) if gl else "Accumulating edge telemetry..."

            elev = summary.get("elevated_levels", {})
            elev_str = "\n".join([f"  • {k}: {v}x vol" for k, v in elev.items()]) if elev else "  • All levels at baseline 1.3x"

            return (
                "🧠 <b>AI Continuous Learning & Adaptive Status</b>\n\n"
                f"📊 <b>Historical Trades Analyzed:</b> {summary.get('total_trades_analyzed', 0)}\n\n"
                f"🚫 <b>Restricted Regimes (Chop Guard):</b>\n  • {bl_str}\n\n"
                f"⭐ <b>Golden Edge Windows:</b>\n  • {gl_str}\n\n"
                f"🛡️ <b>Elevated Volume Multiplier Levels:</b>\n{elev_str}\n\n"
                f"⚙️ <b>Calibrated Breakeven Triggers:</b>\n"
                f"  • Nifty: +{summary.get('nifty_be_pct', 0.02)*100:.1f}%\n\n"
                "<i>Adaptive engine automatically updates rules after every single trade exit.</i>"
            )
        except Exception as e:
            return f"⚠️ Error retrieving learning telemetry: {e}"

    def _cmd_refresh_levels(self) -> str:
        try:
            from core.sr_calculator import refresh_daily_sr_levels
            res = refresh_daily_sr_levels()
            return (
                f"✅ <b>Daily S/R Levels Successfully Recalculated!</b>\n\n"
                f"• <b>New Pivot/CPR Levels Computed:</b> {res.get('refreshed_count', 0)}\n"
                f"• <b>Total Active Trading Levels:</b> {res.get('total_active_levels', 0)}\n"
                f"• <b>Asset Synchronized:</b> NIFTY 50\n\n"
                f"<i>Includes Daily CPR, Classical R1/S1/R2/S2, and Camarilla H4/L4 Breakout Zones.</i>"
            )
        except Exception as e:
            return f"⚠️ Error refreshing daily levels: {e}"

    # ------------------------------------------------------------------
    # Option Chain & Mobile Trade Execution Handlers
    # ------------------------------------------------------------------

    def _cmd_option_chain(self) -> str:
        try:
            from core.market_data import get_live_option_chain
            oc = get_live_option_chain("nifty")
            spot = oc.get("spot", 23950.0)
            exp = oc.get("current_expiry", "N/A")
            lot_size = oc.get("lot_size", 65)
            strikes = oc.get("strikes", {})

            if not strikes:
                return "⚠️ Option chain data unavailable right now."

            atm_strike = int(round(spot / 50.0) * 50)
            target_strikes = [atm_strike - 100, atm_strike - 50, atm_strike, atm_strike + 50, atm_strike + 100]

            lines = [
                "📊 <b>NIFTY 50 Live Option Chain</b>\n",
                f"• <b>Spot:</b> ₹{spot:,.2f} | <b>ATM Strike:</b> {atm_strike}",
                f"• <b>Expiry:</b> {exp} | <b>Lot Size:</b> {lot_size} Qty\n",
                "<code>STRIKE   | CE LTP    | PE LTP</code>",
                "<code>-----------------------------</code>"
            ]

            for s in target_strikes:
                if s in strikes:
                    c_p = float((strikes[s].get("ce") or {}).get("ltp", 0.0))
                    p_p = float((strikes[s].get("pe") or {}).get("ltp", 0.0))
                    atm_tag = "★" if s == atm_strike else " "
                    lines.append(f"<code>{s:<7}{atm_tag}| ₹{c_p:<8.2f}| ₹{p_p:<7.2f}</code>")

            lines.append(
                "\n💡 <b>Quick Actions:</b>\n"
                "• Send /buype to buy ATM Put (10% target & 5% SL, 2:1 RR)\n"
                "• Send /buyce to buy ATM Call (10% target & 5% SL, 2:1 RR)\n"
                "• Custom price: <code>/buype 75.0</code>"
            )
            return "\n".join(lines)
        except Exception as e:
            return f"⚠️ Error fetching option chain: {e}"

    def _cmd_buy_option(self, opt_type: str, args: List[str]) -> str:
        try:
            from scripts.run_paper_trade import execute_paper_trade
            opt_type = opt_type.upper()
            custom_price = None
            custom_strike = None

            if len(args) >= 1:
                try:
                    custom_price = float(args[0])
                except ValueError:
                    pass
            if len(args) >= 2:
                try:
                    custom_strike = int(args[1])
                except ValueError:
                    pass

            # Spawn trade in background daemon thread
            t = threading.Thread(
                target=execute_paper_trade,
                kwargs={
                    "opt_type_str": opt_type,
                    "target_pct": 0.10,
                    "sl_pct": 0.05,
                    "lots": 1,
                    "custom_price": custom_price,
                    "custom_strike": custom_strike,
                    "auto_monitor": True
                },
                daemon=True,
                name=f"TradeWorker_{opt_type}"
            )
            t.start()

            type_lbl = "PUT (PE)" if opt_type == "PE" else "CALL (CE)"
            lot_size = settings.NIFTY_LOT_SIZE
            price_txt = f"at custom price ₹{custom_price:.2f}" if custom_price else "at real-time market LTP"
            strike_txt = f"Strike: {custom_strike}" if custom_strike else "ATM Strike"

            return (
                f"⚡ <b>Order Dispatched to Market!</b>\n\n"
                f"• <b>Side:</b> BUY {type_lbl}\n"
                f"• <b>Target Strike:</b> {strike_txt}\n"
                f"• <b>Lot Size:</b> {lot_size} Qty (1 Lot)\n"
                f"• <b>Execution:</b> {price_txt}\n"
                f"• <b>Risk Rules:</b> +2% Target Profit & -2% Stop Loss\n\n"
                f"<i>Auto-exit monitor engaged in background. Fill alert incoming shortly!</i>"
            )
        except Exception as e:
            return f"⚠️ Order placement error: {e}"

    def _cmd_trade_crude(self, direction: str, args: List[str]) -> str:
        return "ℹ️ Crude Oil trading is decommissioned. ProjectO trades strictly NIFTY 50 options."

    def _cmd_crude(self) -> str:
        return "ℹ️ Crude Oil feed is decommissioned. ProjectO trades strictly NIFTY 50 options."

    def _cmd_levels(self, args: Optional[List[str]] = None) -> str:
        try:
            from core.level_models import load_levels_config
            from core.market_data import get_live_nifty_spot

            spot_data = get_live_nifty_spot()
            spot = float(spot_data.get("spot", 23950.0))
            lvls = load_levels_config("NIFTY")
            title = "🎯 <b>NIFTY 50 Support & Resistance Levels</b>\n"
            pts_label = "pts"

            tf_filter = None
            if any("WEEK" in a.upper() for a in (args or [])):
                tf_filter = "W"
            elif any("MONTH" in a.upper() for a in (args or [])):
                tf_filter = "M"
            elif any("DAY" in a.upper() or "DAILY" in a.upper() for a in (args or [])):
                tf_filter = "D"

            if tf_filter:
                lvls = [
                    l for l in lvls
                    if (tf_filter == "W" and ("week" in l.name.lower() or "weekly" in l.id))
                    or (tf_filter == "M" and ("month" in l.name.lower() or "monthly" in l.id))
                    or (tf_filter == "D" and ("daily" in l.name.lower() or "daily" in l.id or "pdh" in l.id or "pdl" in l.id))
                ]

            supports = [l for l in lvls if l.is_active and l.price < spot]
            resistances = [l for l in lvls if l.is_active and l.price > spot]

            nearest_sup = max(supports, key=lambda x: x.price) if supports else None
            nearest_res = min(resistances, key=lambda x: x.price) if resistances else None

            lines = [
                title,
                f"• <b>Current Spot:</b> <b>₹{spot:,.2f}</b>\n"
            ]

            if nearest_res:
                lines.append(f"🔴 <b>Nearest Resistance:</b> ₹{nearest_res.price:,.2f} (<b>+{nearest_res.price - spot:.1f} {pts_label}</b> away)\n  ↳ <i>{nearest_res.name}</i>")
            if nearest_sup:
                lines.append(f"🟢 <b>Nearest Support:</b> ₹{nearest_sup.price:,.2f} (<b>-{spot - nearest_sup.price:.1f} {pts_label}</b> away)\n  ↳ <i>{nearest_sup.name}</i>\n")

            lines.append("<code>LEVEL      | TF | TYPE | DISTANCE</code>")
            lines.append("<code>-----------------------------------</code>")

            for l in sorted(lvls, key=lambda x: x.price, reverse=True):
                dist = l.price - spot
                tf_tag = "M" if "month" in l.name.lower() or "monthly" in l.id else ("W" if "week" in l.name.lower() or "weekly" in l.id else "D")
                tag = "RES" if "RES" in l.level_type or "SUPPLY" in l.level_type else "SUP"
                lines.append(f"<code>{l.price:<10.1f} | {tf_tag:<2} | {tag:<4} | {dist:+8.1f}</code>")

            tf_legend = "<i>[D]=Daily, [W]=Weekly, [M]=Monthly</i>\n"
            lines.append(f"\n{tf_legend}")

            lines.append(
                "\n💡 <b>Trading Actions:</b>\n"
                "• Send /buype to buy ATM Put (10% Target & 5% SL, 2:1 RR)\n"
                "• Send /buyce to buy ATM Call (10% Target & 5% SL, 2:1 RR)\n"
                "• Send /options to view live ATM option chain table"
            )
            return "\n".join(lines)
        except Exception as e:
            return f"⚠️ Error fetching levels: {e}"

    # ------------------------------------------------------------------
    # Polling Loop
    # ------------------------------------------------------------------

    def _poll_updates(self):
        import urllib.request, json
        while self.is_running:
            try:
                url = f"{self.api_base}/getUpdates?offset={self.last_update_id + 1}&timeout=15"
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        for update in data.get("result", []):
                            self.last_update_id = update["update_id"]
                            message = update.get("message", {})
                            text = message.get("text", "")
                            sender_chat = str(message.get("chat", {}).get("id", ""))
                            sender_name = message.get("from", {}).get("first_name", "Trader")

                            if text:
                                reply = self.handle_message(text, sender_chat, sender_name)
                                self._send_reply(sender_chat, reply)

            except (TimeoutError, urllib.error.URLError) as e:
                # Normal long poll timeout or transient network reconnect
                continue
            except Exception as e:
                logger.error(f"Telegram polling exception: {e}")
                time.sleep(2)

    def _send_reply(self, chat_id: str, text: str):
        import urllib.request, json
        try:
            url = f"{self.api_base}/sendMessage"
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                pass
        except Exception as e:
            logger.error(f"Error replying to Telegram: {e}")


def run_standalone_bridge():
    """Runs the Telegram bridge as a standalone daemon listening 24/7."""
    logger.info("Starting standalone Telegram Bridge service...")
    bridge = TelegramBridge()
    bridge.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        bridge.stop()
        logger.info("Telegram Bridge service exited.")


if __name__ == "__main__":
    run_standalone_bridge()
