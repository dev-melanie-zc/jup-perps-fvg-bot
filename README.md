# jupiter Perps FVG Bot

FVG trading bot for Jupiter Perps on Solana. It watches SOL, ETH, and wBTC for Fair Value Gap setups, emails you when something looks good, and can place the trades on-chain by itself if you want it to.

---

## what it actually does

it runs 24/7 and scans for Fair Value Gaps and price imbalances left behind when the market moves too fast. When price comes back to fill one of those gaps it's usually a good trade. That's the whole strategy.

- Pulls live candle data from Birdeye (same source as jup.ag/perps) across 15m, 4h, 6h, 8h, and 12h timeframes
- Detects bull and bear FVG zones on SOL, ETH, and wBTC
- When price enters a zone, it sends the setup to Claude (AI) which decides long, short, or skip based on zone quality and market context
- If auto-trade is on, it signs and submits the transaction on-chain through Jupiter Perps
- Emails you every signal — whether it trades or not
- Has a web dashboard to watch zones in real time and tweak settings without touching any files

---

## How the AI part works

When price hits a zone, the bot builds a prompt with the zone info and sends it to anthropic Haiku. anthropic replies with a JSON decision - LONG, SHORT, or WAIT - along with a confidence level. The bot only auto-trades HIGH confidence signals. The entry, stop, and target are calculated by Python (1% stop, 2% target from zone midpoint) so Claude can't mess up the math.

---

## What you need

Three API keys:

```
ANTHROPIC_API_KEY    — Claude makes the trade decisions
BIRDEYE_API_KEY      — pulls candle data for all three pairs
SOLANA_PRIVATE_KEY   — signs transactions on-chain (keep this safe)
```

Set these as environment variables. Never put them in the code or commit them.

For email alerts you'll also need a Gmail account with an app password enabled (Google account → Security → 2-Step Verification → App passwords). You set that up through the web UI after deploying, not in the code.

---

## Running it

```bash
pip install -r requirements.txt
python server.py
```

Then open the web UI in your browser, go to CONFIG, and fill in your wallet address, email settings, and trading limits. Hit save. That's it.

The server handles everything — FVG detection, AI analysis, trade execution, and email alerts all run automatically in the background.

---

## Web UI

There's a dashboard at whatever port the server runs on. It shows:

- Live FVG zones for each pair and timeframe
- Which zones price is currently inside or approaching
- Open positions and pending orders
- A config panel where you can change every setting without restarting

---

## Risk controls

The bot has a bunch of built-in limits you can set from the UI:

- Max position size and leverage per pair
- Daily loss limit per pair and overall
- Max number of open positions at once
- Cooldown period after opening or closing a trade
- One trade per symbol at a time

Auto-trade is off by default. You have to explicitly turn it on.

---

## Backtest — SOL 15m, February 2025

157 trades, 59 wins, 98 losses. Net +$40 on $100 collateral at 2x leverage. Win rate 37.6% with a 2:1 R:R (1% stop, 2% target).

SOL dropped hard that month ($232 down to $136) so there were a lot of losing longs early on, but the short entries caught the move and kept it green overall. Full trade log in [BACKTEST.md](BACKTEST.md).

---

## Files

```
server.py          — main server, web UI, websocket, trade loop
agent.py           — Claude AI analysis
trading.py         — risk engine, position tracking
jup_perps_exec.py  — on-chain transaction builder for Jupiter Perps
jup_perps_alerts.py — standalone email alert script (runs separately)
market_data.py     — order book and trade flow data
backtest.py        — backtest the FVG strategy on historical data
static/index.html  — the web dashboard
config.json        — your settings (gitignored, never pushed)
```
