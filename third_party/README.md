# ProjectO third-party catalogue

The repositories below are local reference checkouts. They are **not imported by
the live bot** and are deliberately ignored by ProjectO Git. Recreate them with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_third_party.ps1
```

| Repository | Snapshot | Reuse status | ProjectO use |
| --- | --- | --- | --- |
| `smartapi-python` | `7f10dad` | Official SDK; no licence file in the checkout | Use only through the published `smartapi-python` dependency already used by `AngelOneBroker`. Do not copy source files. |
| `bhav` | `562dc21` | MIT | Candidate backtest engine: realistic Indian charges, multi-leg options and robustness analysis. Requires an Upstox data adapter/token. |
| `ui-trading-system` | `3305f97` | MIT | Reference/adaptable components: trade journal, position guard, GTT/order monitoring and notification patterns. Zerodha-specific code stays isolated. |
| `options-straddle-dashboard` | `49e4807` | No licence file found | Reference only. Its Angel One option-chain and ATM visualisation ideas must be independently reimplemented. |
| `options-day-trader-agent` | `e122b10` | No licence file found | Reference only. Do not copy its AI agent or live execution code. |
| `awesome-algo-trading-india` | `0454b8d` | No licence file found | Discovery index only; no executable integration. |

## Integration boundary

ProjectO remains the owner of strategy signals, risk limits, paper/live mode,
and order approval. Any third-party component is first wrapped behind a small,
testable adapter and starts in offline or paper mode. No downloaded strategy or
broker code is automatically enabled for real-money trading.

## Planned reusable components

1. **Backtesting:** adapt the MIT-licensed Bhav data/metrics model behind a
   ProjectO backtest adapter, then compare it with the existing ORION backtest.
2. **Execution safety:** adapt selected MIT-licensed UI Trading System patterns
   (order reconciliation, position guard, journal) without its Zerodha client.
3. **Angel transport:** continue using Angel One's installed, official SDK;
   improve ProjectO's retry and recovery layer rather than copying the SDK.

Each component requires its own test, dependency check, and paper-trading
verification before it becomes active.
