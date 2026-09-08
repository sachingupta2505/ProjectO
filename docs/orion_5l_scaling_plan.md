# ORION 2.0 — ₹5 Lakh Paper Scaling Plan

Status: deployed in paper mode on 8 September 2026. This plan applies to
ORION 2.0 only; THETA-0DTE is intentionally excluded from the scaled run.

## Risk limits

| Control | Value |
| --- | ---: |
| Capital reference | ₹500,000 |
| ORION risk budget per trade | ₹5,000 (1.0%) |
| ORION daily loss limit | ₹10,000 (2.0%) |
| ORION daily profit limit | ₹10,000 (2.0%) |
| ORION maximum position size | 3 NIFTY lots |
| NIFTY lot size | 65 units |

## Sizing rule

At a qualifying entry, the bot calculates:

`estimated loss per lot = max(10 option points, 0.50 × structural spot stop distance) × 65 + ₹55 charges`

`lots = min(3, floor(₹5,000 / estimated loss per lot))`

If one lot would exceed the risk budget, the bot skips the trade. The estimate
is intentionally conservative because reliable option-at-stop pricing is not
available from the current live feed. Actual option fills, spread, and
slippage must be reviewed during paper trading.

## Operating policy

1. Run `main.py --strategy orion --index NIFTY --mode paper --lots 3 --no-telegram-listener`.
2. Keep THETA disabled during the evaluation; the older Duo command applies a
   shared lot count to both strategies.
3. Review at least 40 additional paper ORION entries before increasing the
   three-lot cap or enabling live execution.
4. Stop the session after the ₹10,000 daily loss limit; do not manually add
   size after a losing trade.

This is a risk-control policy, not a promise of profitability.
