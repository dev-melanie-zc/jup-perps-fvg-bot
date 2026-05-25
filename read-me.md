# Jupiter Perps FVG Bot

Rule-based FVG trading bot for Jupiter Perps on Solana. Watches SOL, ETH, and wBTC 24/7 across four timeframes, fires email alerts when price enters a zone, and optionally places trades on-chain — no AI, no API costs, no latency.

![dashboard](https://github.com/user-attachments/assets/e11e58fb-2372-4e61-8f8b-e02fbef2b19c)
![config](https://github.com/user-attachments/assets/598520b7-dfc4-41da-8887-569f8ff0a15a)

---

## Strategy

Fair Value Gaps are price imbalances left behind when the market moves too fast through a 3-candle gap. When price retraces into one, the bot enters at the zone midpoint.

- **Entry:** zone midpoint
- **Stop loss:** 1% from entry
- **Take profit:** 2% from entry
- **R:R:** 2:1 fixed — breakeven win rate is 33.3%

Zone priority: `INSIDE > APPROACHING (<1%) > WATCHING (1–3%)`. Within a priority tier it picks the highest timeframe. No indicators, no filters, no ML — just the gap and the math.

---

## Backtest — SOL 15m, February 2025

**Personal project. Real numbers. One month. Do not run this with money you can't afford to lose.**

```
Trades:       157
Wins:          59   (37.6%)
Losses:        98
Net PnL:      +$40 on $100 collateral at 2x leverage
Max drawdown: $34
```

SOL dropped 41% that month ($232 → $136). Short entries caught the move in the last two weeks and carried the result. Weeks 1–2 were choppy and mostly losses — that's what unfiltered FVG entries look like in a ranging market.

37.6% win rate clears the 33.3% breakeven threshold at 2:1. Barely. One good month isn't a reason to go live.

Full trade log → [BACKTEST.md](BACKTEST.md)

---

## How it works

Every 60 seconds:

1. Fetches OHLCV from Birdeye (same source as jup.ag/perps), resamples 1h data into 4h and 12h to save API calls
2. Detects all open FVG zones, classifies by distance to current price
3. Picks the best zone by priority + timeframe weight
4. If the signal is HIGH confidence (price inside the zone) and auto-trade is on — submits an on-chain open order through Jupiter Perps
5. Pushes update to the web dashboard over WebSocket

Chain reconciliation runs every cycle. Pending orders expire after 3 minutes if the position never confirms on-chain.

---

## Features

- FVG detection on 15m, 1h, 4h, 12h
- SOL, ETH, wBTC
- On-chain execution via Jupiter Perps
- TP/SL orders submitted immediately after position confirms
- Email alerts on zone hits and trade entries
- Live web dashboard
- Risk controls: position limits, per-pair and total daily loss caps, cooldowns, one trade per symbol

---

## Requirements

- Python 3.10+
- Birdeye API key (paid plan recommended — free tier will rate-limit)
- Solana wallet + private key (for trade execution)
- Gmail app password (for alerts)

---

## Setup

```bash
# .env
BIRDEYE_API_KEY=...
SOLANA_PRIVATE_KEY=...
```

```bash
pip install -r requirements.txt
python server.py
```

Open `localhost:8080`, go to CONFIG, enter your wallet address and email settings, save. Auto-trade is off by default.

---

## Risk controls

All configurable from the UI, no code changes needed:

| Setting | Default |
|---|---|
| Collateral per trade | $50 |
| Leverage | 2× |
| Max concurrent positions | 1 |
| Max pending orders | 1 |
| Per-pair daily loss cap | $100 |
| Total daily loss cap | $250 |
| Cooldown after open | 30 min |
| Cooldown after close | 15 min |
| One trade per symbol | yes |

---

## Files

```
server.py           — web server, websocket, polling loop
agent.py            — rule-based zone picker and signal logic
trading.py          — risk engine, position tracking, on-chain reconciliation
jup_perps_exec.py   — Jupiter Perps transaction builder
jup_perps_alerts.py — standalone alert script (no server needed)
market_data.py      — Binance order book and trade flow
backtest.py         — historical backtester
static/index.html   — web dashboard
config.json         — settings (gitignored)
```
