# Nifty Options Algorithmic Trading Bot ⚡

A modular, production-ready algorithmic trading bot built in Python for trading **NSE Nifty 50 Options**. It supports option selling strategies (e.g. 9:20 AM Short Straddle / Strangle) and directional option buying (e.g. Momentum Breakout with trailing SL), backed by a Risk Management System (RMS), multi-broker abstraction, and real-time dashboards.

---

## Features

- **Multi-Broker Architecture**:
  - **Paper Trading Engine**: Real-time simulated execution with realistic fills, slippage modeling, and mark-to-market (MTM) PnL calculation. Safe to run without broker credentials.
  - **Angel One SmartAPI**: Automated TOTP session creation (`pyotp`), order routing, and positions tracking.
- **Built-in Systematic Strategies**:
  - **9:20 AM Short Straddle / Strangle**: Captures intraday theta decay on Nifty weekly options with independent leg-level Stop Loss (e.g., 25%) and time-based auto square-off.
  - **Directional Momentum Option Buyer**: Enters ATM Call or Put based on EMA crossovers with profit targets and dynamic ratcheting trailing stop-loss.
- **Strict Risk Management (RMS)**:
  - **Daily Circuit / Kill Switch**: Automatically halts trading and squares off open positions if cumulative loss hits the daily threshold (e.g., -₹5,000).
  - **Trailing Stop Loss (TSL)**: Automatically locks in paper profits as trades move favorably.
  - **Auto Square-Off**: Closes all intraday positions before market close (15:15 IST).
- **Dual User Interface**:
  - **Terminal Dashboard**: Live, auto-refreshing terminal UI built with `Rich`.
  - **Streamlit Web App**: Interactive dashboard with real-time charts, manual emergency kill switch, and an options pricing Black-Scholes Greeks calculator.
- **Backtesting Engine**:
  - Simulates multi-session strategy performance, calculating Win Rate, Profit Factor, and Max Drawdown.

---

## Project Structure

```
projectO/
├── config/
│   ├── settings.py           # Trading parameters, timings, risk thresholds
│   └── .env.example          # Environment & API keys template
├── core/
│   ├── models.py             # Domain models (Order, Position, Instrument, Tick)
│   ├── risk_manager.py       # RMS engine (Kill switch, trailing SL, auto exit)
│   ├── option_chain.py       # Strike selection, expiry finder, Black-Scholes Greeks
│   └── logger.py             # Rotating file + colored console logger
├── brokers/
│   ├── base_broker.py        # Abstract broker interface
│   ├── paper_broker.py       # Paper trading simulation broker
│   └── angel_broker.py       # Angel One SmartAPI adapter
├── strategies/
│   ├── base_strategy.py      # Abstract strategy lifecycle
│   ├── short_straddle.py     # 9:20 AM Straddle/Strangle strategy
│   └── momentum_buyer.py     # Directional momentum option buyer
├── dashboard/
│   ├── terminal_ui.py        # Live Rich terminal UI
│   └── streamlit_app.py      # Streamlit web dashboard
├── tests/                    # Comprehensive unit and integration test suite
├── main.py                   # Main CLI orchestrator
├── backtest.py               # Historical/bar backtesting engine
└── requirements.txt          # Python dependencies
```

---

## Quickstart

### 1. Installation

Ensure Python 3.10+ is installed. Install the dependencies:
```bash
pip install -r requirements.txt
```

### 2. Run the Backtest Simulator
To test strategy performance over 30 sessions:
```bash
python backtest.py
```

### 3. Run the Bot in Paper Trading Mode (Terminal UI)
Start the 9:20 Short Straddle with live simulated ticks:
```bash
python main.py --mode paper --strategy straddle --simulate
```
To run the Momentum Option Buyer:
```bash
python main.py --mode paper --strategy momentum --simulate
```

### 4. Run the Streamlit Web Dashboard
Launch the interactive web interface:
```bash
streamlit run dashboard/streamlit_app.py --server.address 0.0.0.0
```
Or via `main.py`:
```bash
python main.py --ui web
```

### 5. Telegram Mobile Bridge (Control from Phone)
Interact with the bot from your phone:
1. Create a bot on Telegram via **@BotFather** to get your `TELEGRAM_BOT_TOKEN`.
2. Get your numeric chat ID from **@userinfobot** on Telegram.
3. Add them to `.env`:
   ```ini
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNO...
   TELEGRAM_CHAT_ID=987654321
   ```
4. Start the bot normally:
   ```bash
   python main.py --mode paper --broker angel --strategy straddle
   ```
   Commands available on your phone:
   - `/status` — Live strategy, positions & Spot price
   - `/pnl` — Real-time PnL & available cash
   - `/positions` — Detailed open CE/PE legs
   - `/setlots <n>` — Change lot sizing (e.g. `/setlots 2`)
   - `/setsl <pct>` — Change leg stop loss % (e.g. `/setsl 30`)
   - `/squareoff` — Emergency kill switch & close all positions
   - `/dashboard` — Link to open web dashboard on mobile browser

---

## Configuration & Broker Credentials

Copy the example environment file:
```bash
cp .env.example .env
```
Open `.env` and configure your settings:

```ini
# Execution Mode
PAPER_TRADING=True
BROKER=paper
PAPER_INITIAL_CAPITAL=200000

# Angel One Credentials (if using BROKER=angel)
ANGEL_API_KEY=your_smartapi_key
ANGEL_CLIENT_ID=your_client_code
ANGEL_PIN=your_account_pin
ANGEL_TOTP_KEY=your_totp_secret_key

# Risk Parameters
MAX_DAILY_LOSS=5000
MAX_DAILY_PROFIT=15000
NIFTY_LOT_SIZE=75
```

---

## Command-Line Arguments

| Argument | Options | Default | Description |
|---|---|---|---|
| `--mode` | `paper`, `live` | `paper` | Paper trading simulation or live execution |
| `--broker` | `paper`, `angel` | `paper` | Broker connection to use |
| `--strategy` | `straddle`, `momentum` | `straddle` | Strategy to run |
| `--lots` | Integer | `1` | Number of Nifty lots (e.g. 1 lot = 75 qty) |
| `--ui` | `terminal`, `web`, `headless` | `terminal` | User interface mode |
| `--simulate` | Flag | `False` | Generate synthetic price ticks for testing |
| `--spot` | Float | `24500.0` | Initial Nifty spot price benchmark |

---

## Running Automated Tests

Run the test suite via `pytest`:
```bash
pytest -v
```
All 11 unit and integration tests cover:
- ATM strike rounding and strike step calculations
- Black-Scholes Greeks calculation (Delta, Gamma, Theta, Vega)
- Expiry date resolution (weekly & monthly)
- Paper broker order fills, slippage, and position tracking
- Risk Management System daily loss circuit and leg stop-loss
- Full strategy lifecycle simulation
