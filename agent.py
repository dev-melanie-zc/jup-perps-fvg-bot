#!/usr/bin/env python3
"""
AI agent — proximity-filtered FVG analysis with precise entries.

Flow:
  1. Classify all open zones by distance to current price
  2. Skip Claude entirely if nothing is within MAX_DIST_PCT
  3. Otherwise build context (Birdeye zones + Binance market data) and call Claude
  4. Python computes stop/target/R:R from zone math (not Claude)
  5. Log every analysis to analysis_log.jsonl
"""

import os, json
from datetime import datetime
import anthropic

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LOG_FILE    = "analysis_log.jsonl"
MAX_DIST_PCT = 3.0    # ignore zones further than this % from price
_TF_WEIGHT   = {"12h": 4, "4h": 3, "1h": 2, "15m": 1}
TAKE_PROFIT_PCT = 0.02
STOP_LOSS_PCT   = 0.01


# ── Zone classification ───────────────────────────────────────────────────────

def _classify_zones(state: dict, prices: dict, symbols: list, timeframes: list) -> dict:
    """
    Returns {"inside": [...], "approaching": [...], "watching": [...]}
    Zones further than MAX_DIST_PCT are excluded entirely.
    Each zone entry has extra keys: sym, tf, dist_pct, zone_status.
    """
    buckets = {"inside": [], "approaching": [], "watching": []}
    sorted_tfs = sorted(timeframes, key=lambda t: _TF_WEIGHT.get(t, 0), reverse=True)

    for sym in symbols:
        price = prices.get(sym)
        if not price:
            continue
        for tf in sorted_tfs:
            tf_data = state.get(sym, {}).get(tf)
            if not tf_data:
                continue
            for z in tf_data.get("fvgs", []):
                if z.get("filled"):
                    continue
                if z["low"] <= price <= z["high"]:
                    dist_pct   = 0.0
                    zone_status = "INSIDE"
                    bucket      = "inside"
                else:
                    dist_pct = min(
                        abs(price - z["low"])  / price * 100,
                        abs(price - z["high"]) / price * 100,
                    )
                    if dist_pct > MAX_DIST_PCT:
                        continue
                    if dist_pct <= 1.0:
                        zone_status = "APPROACHING"
                        bucket      = "approaching"
                    else:
                        zone_status = "WATCHING"
                        bucket      = "watching"

                buckets[bucket].append({
                    **z,
                    "sym":         sym,
                    "tf":          tf,
                    "dist_pct":    round(dist_pct, 2),
                    "zone_status": zone_status,
                })

    return buckets


# ── Stop / target math (Python, not Claude) ──────────────────────────────────

def _compute_exits(action: str, zone_low: float, zone_high: float) -> dict:
    """
    Entry  = zone midpoint
    Stop   = fixed 1% from entry
    Target = fixed 2% from entry
    """
    if action not in ("LONG", "SHORT"):
        return {}
    mid  = (zone_low + zone_high) / 2

    if action == "LONG":
        entry  = mid
        stop   = entry * (1 - STOP_LOSS_PCT)
        target = entry * (1 + TAKE_PROFIT_PCT)
    else:
        entry  = mid
        stop   = entry * (1 + STOP_LOSS_PCT)
        target = entry * (1 - TAKE_PROFIT_PCT)

    risk   = abs(entry - stop)
    reward = abs(target - entry)
    rr     = round(reward / risk, 2) if risk else 0

    return {
        "entry_price": round(entry,  6),
        "stop_loss":   round(stop,   6),
        "target":      round(target, 6),
        "rr":          f"{rr}:1",
    }


# ── Context builders ──────────────────────────────────────────────────────────

def _zone_ctx(buckets: dict, prices: dict) -> str:
    lines = []
    labels = [("inside", "*** PRICE INSIDE — LIVE ENTRY ***"),
              ("approaching", "APPROACHING (<1% from price)"),
              ("watching", "WATCHING (1–3% from price)")]
    for key, header in labels:
        zones = buckets[key]
        if not zones:
            continue
        lines.append(f"\n{header}")
        for z in zones:
            sym   = z["sym"]
            price = prices.get(sym, 0)
            tag   = "▲ BULL" if z["type"] == "bull" else "▼ BEAR"
            exits = _compute_exits(
                "LONG" if z["type"] == "bull" else "SHORT",
                z["low"], z["high"]
            )
            lines.append(
                f"  {sym} [{z['tf'].upper()}] {tag}  "
                f"{z['low']} – {z['high']}  "
                f"|  dist {z['dist_pct']}%  "
                f"|  entry ~{exits.get('entry_price','')}  "
                f"stop {exits.get('stop_loss','')}  "
                f"target {exits.get('target','')}  "
                f"R:R {exits.get('rr','')}  "
                f"|  formed {z['formed_at']}"
            )
    return "\n".join(lines) if lines else "  (none)"


def _market_ctx(mkt: dict, symbols: list, timeframes: list) -> str:
    if not mkt:
        return "  (no Binance data)"
    lines = []
    sorted_tfs = sorted(timeframes, key=lambda t: _TF_WEIGHT.get(t, 0), reverse=True)
    for sym in symbols:
        d = mkt.get(sym, {})
        if not d:
            continue
        lines.append(f"\n{sym}")
        ob = d.get("order_book", {})
        if ob:
            lines.append(
                f"  Book: {ob.get('pressure')}  ratio {ob.get('ratio')}  "
                f"spread {ob.get('spread_pct')}%  "
                f"bid wall {ob.get('top_bid_price')} ({ob.get('top_bid_qty')} units)  "
                f"ask wall {ob.get('top_ask_price')} ({ob.get('top_ask_qty')} units)"
            )
        tf_ = d.get("trade_flow", {})
        if tf_:
            lines.append(
                f"  Flow: {tf_.get('buy_pct')}% buys / {tf_.get('sell_pct')}% sells  "
                f"→ {tf_.get('bias')}  "
                f"large: {tf_.get('large_buys')} buys vs {tf_.get('large_sells')} sells"
            )
        s24 = d.get("stats_24h", {})
        if s24:
            lines.append(
                f"  24h: {s24.get('change_pct'):+}%  vol ${s24.get('volume_usd',0):,.0f}"
            )
        vol = d.get("volume_by_tf", {})
        if vol:
            vlines = []
            for tf in sorted_tfs:
                v = vol.get(tf)
                if v:
                    vlines.append(
                        f"{tf.upper()} {v['label']} {v['ratio']}x ({v['dv_bias']})"
                    )
            if vlines:
                lines.append(f"  Vol: {' | '.join(vlines)}")
    return "\n".join(lines)


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(entry: dict):
    record = {"ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_log(n: int = 30) -> list:
    try:
        with open(LOG_FILE) as f:
            lines = [l.strip() for l in f if l.strip()]
        return [json.loads(l) for l in lines[-n:]][::-1]
    except FileNotFoundError:
        return []


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze(state: dict, prices: dict, symbols: list,
            timeframes: list, mkt: dict = None,
            slot_status: dict = None) -> dict | None:

    buckets = _classify_zones(state, prices, symbols, timeframes)
    total_in_range = sum(len(v) for v in buckets.values())
    slot_status = slot_status or {}

    if slot_status.get("full") and slot_status.get("ignore_signals_when_full", True):
        result = {
            "action": "WAIT", "symbol": None, "timeframe": None,
            "zone_status": None, "zone_low": None, "zone_high": None,
            "entry_price": None, "stop_loss": None, "target": None, "rr": None,
            "reasoning": f"Trade slot full: {slot_status.get('reason', 'no trade capacity')}. Ignoring new signals until space is available.",
            "confluence": "trade capacity full",
            "confidence": "LOW",
            "zones_in_range": total_in_range,
            "trade_slot_full": True,
        }
        _log(result)
        return result

    if total_in_range == 0:
        result = {
            "action": "WAIT", "symbol": None, "timeframe": None,
            "zone_status": None, "zone_low": None, "zone_high": None,
            "entry_price": None, "stop_loss": None, "target": None, "rr": None,
            "reasoning": f"No open FVG zones within {MAX_DIST_PCT}% of price on any symbol.",
            "confluence": "none", "confidence": "LOW", "zones_in_range": 0,
        }
        _log(result)
        return result

    if not ANTHROPIC_API_KEY:
        return None

    zone_ctx = _zone_ctx(buckets, prices)
    mkt_ctx  = _market_ctx(mkt, symbols, timeframes)
    slot_ctx = (
        f"Open positions: {slot_status.get('open_count', 0)} / "
        f"{slot_status.get('max_concurrent_positions', 1)} | "
        f"Pending orders: {slot_status.get('pending_count', 0)} / "
        f"{slot_status.get('max_pending_orders', 1)} | "
        f"Slots available: {slot_status.get('slots_available', 0)} | "
        f"Status: {slot_status.get('reason', 'trade slot available')}"
    )

    prompt = f"""You are an ICT/SMC analyst for Jupiter Perps (Solana on-chain perpetuals).
Only zones within {MAX_DIST_PCT}% of current price are shown. All entry/stop/target levels are pre-calculated.

════ TRADE CAPACITY RULE ════
{slot_ctx}

You must honor trade capacity before market analysis:
  - If slots available is 0, reply WAIT.
  - If pending orders are at the limit, reply WAIT.
  - Do not recommend a new LONG or SHORT while the bot has no trade slot.
  - A full trade slot means all new signals are ignored until a trade closes or a pending order clears.

════ FVG ZONES IN RANGE ════
{zone_ctx}

════ BINANCE MARKET DATA (directional bias) ════
{mkt_ctx}

════ DECISION RULES ════
Priority order:
  1. INSIDE zones — price is live in the zone right now — highest priority
  2. APPROACHING (<1%) — price almost there, good to alert
  3. WATCHING (1-3%) — monitor, not yet actionable

Confirm with market data:
  - Bull FVG + BULLISH order book + BUY flow + bullish volume = HIGH confidence LONG
  - Bear FVG + BEARISH order book + SELL flow + bearish volume = HIGH confidence SHORT
  - Conflicting signals = MEDIUM or LOW confidence
  - If multiple zones qualify, pick the highest timeframe + best market data alignment

Exit levels are fixed by Python after your reply: take profit = 2% from entry, stop loss = 1% from entry.

Reply with ONLY one compact JSON line, no newlines inside, no markdown:
{{"action":"LONG","symbol":"SOL","timeframe":"4h","zone_status":"INSIDE","zone_low":85.5,"zone_high":86.2,"entry_price":85.85,"stop_loss":84.9915,"target":87.567,"rr":"2.0:1","reasoning":"One or two sentences max.","confluence":"brief or none","confidence":"HIGH"}}

action: LONG / SHORT / WAIT — confidence: HIGH / MEDIUM / LOW"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "```" in text:
            parts = text.split("```")
            text  = parts[1] if len(parts) > 1 else parts[0]
            if text.lower().startswith("json"):
                text = text[4:]
        text  = text.strip()
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]
        result = json.loads(text)

        # Recompute exits from zone math — don't trust Claude's arithmetic
        if result.get("action") in ("LONG", "SHORT") and result.get("zone_low") and result.get("zone_high"):
            exits = _compute_exits(result["action"], result["zone_low"], result["zone_high"])
            result.update(exits)

        result["zones_in_range"] = total_in_range
        result["trade_slot_full"] = bool(slot_status.get("full"))
        _log(result)
        return result

    except json.JSONDecodeError as e:
        print(f"Agent JSON error: {e}")
        result = {
            "action": "WAIT", "symbol": None, "timeframe": None,
            "zone_status": None, "zone_low": None, "zone_high": None,
            "entry_price": None, "stop_loss": None, "target": None, "rr": None,
            "reasoning": "Malformed response from model.", "confluence": "none",
            "confidence": "LOW", "zones_in_range": total_in_range,
        }
        _log(result)
        return result
    except Exception as e:
        print(f"Agent error: {e}")
        return None
