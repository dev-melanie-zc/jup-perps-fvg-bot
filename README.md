# Jupiter Perps FVG Bot

FVG trading bot for Jupiter Perps on Solana. Watches SOL, ETH and wBTC for Fair Value Gap setups, fires email alerts, and can place trades on-chain automatically.

---

## Strategy

Trades Fair Value Gaps - price imbalances left behind when the market moves too fast. When price comes back to fill the gap, the bot enters at the zone midpoint with a fixed 1% stop loss and 2% take profit (2:1 R:R). Entry and exit levels are calculated by the bot, not the AI Agent.

Setups are filtered by an AI agent (Anthropic) that looks at zone quality and market context before deciding LONG, SHORT, or WAIT. Only HIGH confidence signals trigger auto-trades.

---

## Backtest results - SOL 15m, February 2025

**This is a personal project. These are real backtest numbers on one month of data. Do not run this with money you can't afford to lose.**

```
Trades:    157
Wins:       59
Losses:     98
Win rate:  37.6%  (breakeven at 2:1 R:R is 33.3%)
Net PnL:  +$40 on $100 collateral at 2x leverage
Max drawdown: $34
```

SOL dropped 41% that month ($232 to $136). The short entries caught the move late in the month which is what kept it green. One good month of data is not a reason to run this live.

Full trade log in [BACKTEST.md](BACKTEST.md).

---

## Overview

Scans for FVG zones across multiple timeframes 24/7. When price enters a zone the setup goes to the AI agent. If auto-trade is on and the signal is HIGH confidence it submits the order on-chain through Jupiter Perps.

Candle data comes from Birdeye (same source as jup.ag/perps).

---

## Features

- FVG detection across 15m, 4h, 6h, 8h and 12h timeframes
- SOL, ETH and wBTC support
- AI agent (Anthropic) analyzes each setup
- On-chain trade execution via Jupiter Perps
- Email alerts on every signal
- Web dashboard with live zone charts
- Full risk management - position limits, daily loss caps, cooldowns
- All settings configurable from the UI

---

## Requirements

- Python 3.10+
- Anthropic API key
- Birdeye API key
- Solana wallet private key (for trade execution)
- Gmail account with an app password (for alerts)

---

## Setup

Set your environment variables:

```
ANTHROPIC_API_KEY=...
BIRDEYE_API_KEY=...
SOLANA_PRIVATE_KEY=...
```

Install dependencies and run:

```bash
pip install -r requirements.txt
python server.py
```

Open the web UI, go to CONFIG, enter your wallet address and email settings, hit save.

---

## Risk management

Configurable from the web UI:

- Per-pair position size and leverage
- Per-pair daily loss limit
- Total daily loss limit
- Max concurrent open positions
- Post-open and post-close cooldown timers
- One trade per symbol at a time

Auto-trade is off by default.

---

## Files

```
server.py           - web server, websocket, main loop
agent.py            - Anthropic AI analysis
trading.py          - risk engine and position tracking
jup_perps_exec.py   - Jupiter Perps on-chain transaction builder
jup_perps_alerts.py - standalone email alert script
market_data.py      - order book and trade flow
backtest.py         - historical strategy backtester
static/index.html   - web dashboard
config.json         - local settings (gitignored)
```
