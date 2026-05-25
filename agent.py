#!/usr/bin/env python3
"""
Rule-based FVG bot — proximity-filtered zone analysis with precise entries.

Flow:
  1. Classify all open zones by distance to current price
  2. Skip if nothing is within MAX_DIST_PCT
  3. Pick the best zone (inside > approaching > watching, then highest TF weight)
  4. Signal LONG (bull FVG) or SHORT (bear FVG) based on zone type
  5. Python computes stop/target/R:R from zone math
  6. Log every analysis to analysis_log.jsonl
"""

import json
from datetime import datetime

LOG_FILE     = "analysis_log.jsonl"
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
                    dist_pct    = 0.0
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


# ── Stop / target math ────────────────────────────────────────────────────────

def _compute_exits(action: str, zone_low: float, zone_high: float) -> dict:
    """
    Entry  = zone midpoint
    Stop   = fixed 1% from entry
    Target = fixed 2% from entry
    """
    if action not in ("LONG", "SHORT"):
        return {}
    mid = (zone_low + zone_high) / 2

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


# ── Zone context string (for logging) ─────────────────────────────────────────

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


# ── Rule-based zone picker ────────────────────────────────────────────────────

def _pick_best_zone(buckets: dict) -> dict | None:
    """
    Priority: inside > approaching > watching.
    Within a bucket, pick the highest-TF zone (then first found).
    Returns the zone dict or None.
    """
    for bucket in ("inside", "approaching", "watching"):
        zones = buckets[bucket]
        if not zones:
            continue
        # Sort by TF weight descending, then dist_pct ascending
        best = sorted(zones, key=lambda z: (-_TF_WEIGHT.get(z["tf"], 0), z["dist_pct"]))
        return best[0]
    return None


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

    # Trade slot full — skip all signals
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

    # No zones in range
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

    # Pick the best zone by rule
    zone = _pick_best_zone(buckets)
    if zone is None:
        result = {
            "action": "WAIT", "symbol": None, "timeframe": None,
            "zone_status": None, "zone_low": None, "zone_high": None,
            "entry_price": None, "stop_loss": None, "target": None, "rr": None,
            "reasoning": "No actionable zone found.",
            "confluence": "none", "confidence": "LOW", "zones_in_range": total_in_range,
        }
        _log(result)
        return result

    action = "LONG" if zone["type"] == "bull" else "SHORT"
    exits  = _compute_exits(action, zone["low"], zone["high"])

    # Confidence by zone status
    confidence_map = {"INSIDE": "HIGH", "APPROACHING": "MEDIUM", "WATCHING": "LOW"}
    confidence = confidence_map.get(zone["zone_status"], "LOW")

    reasoning = (
        f"{zone['zone_status']} {zone['type'].upper()} FVG on {zone['sym']} "
        f"{zone['tf'].upper()} ({zone['low']} – {zone['high']}), "
        f"{zone['dist_pct']}% from price. "
        f"Rule-based entry: {action} at zone midpoint."
    )

    result = {
        "action":      action,
        "symbol":      zone["sym"],
        "timeframe":   zone["tf"],
        "zone_status": zone["zone_status"],
        "zone_low":    zone["low"],
        "zone_high":   zone["high"],
        "entry_price": exits.get("entry_price"),
        "stop_loss":   exits.get("stop_loss"),
        "target":      exits.get("target"),
        "rr":          exits.get("rr"),
        "reasoning":   reasoning,
        "confluence":  f"{zone['tf']} FVG {zone['zone_status'].lower()}",
        "confidence":  confidence,
        "zones_in_range":   total_in_range,
        "trade_slot_full":  bool(slot_status.get("full")),
    }
    _log(result)
    return result
