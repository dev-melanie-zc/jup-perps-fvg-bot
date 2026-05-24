#!/usr/bin/env python3
"""
Binance market data layer — order book, trade flow, volume.
Used by the AI agent. Completely separate from Birdeye price feeds.
"""

import time, requests
from typing import Optional

BINANCE_BASE = "https://api.binance.com"

# Map jup.ag perp symbols → Binance spot pairs
BINANCE_SYMBOLS = {
    "SOL":  "SOLUSDT",
    "ETH":  "ETHUSDT",
    "WBTC": "BTCUSDT",   # BTCUSDT is the liquid proxy for WBTC
}

_TF_MAP = {"15m": "15m", "1h": "1h", "4h": "4h", "12h": "12h"}


def _get(path: str, params: dict = None) -> Optional[dict | list]:
    try:
        r = requests.get(f"{BINANCE_BASE}{path}", params=params, timeout=10)
        if r.ok:
            return r.json()
    except Exception as e:
        print(f"Binance fetch error {path}: {e}")
    return None


def order_book(symbol: str, limit: int = 20) -> dict:
    raw = _get("/api/v3/depth", {"symbol": BINANCE_SYMBOLS[symbol], "limit": limit})
    if not raw:
        return {}

    bids = [(float(p), float(q)) for p, q in raw.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in raw.get("asks", [])]
    bid_vol = sum(q for _, q in bids)
    ask_vol = sum(q for _, q in asks)
    ratio   = round(bid_vol / ask_vol, 3) if ask_vol else 1.0
    spread  = round((asks[0][0] - bids[0][0]) / bids[0][0] * 100, 4) if bids and asks else 0

    # Find largest walls
    top_bid = max(bids, key=lambda x: x[1]) if bids else (0, 0)
    top_ask = max(asks, key=lambda x: x[1]) if asks else (0, 0)

    return {
        "bid_vol":       round(bid_vol, 1),
        "ask_vol":       round(ask_vol, 1),
        "ratio":         ratio,
        "pressure":      "BULLISH" if ratio > 1.15 else "BEARISH" if ratio < 0.87 else "NEUTRAL",
        "spread_pct":    spread,
        "top_bid_price": top_bid[0],
        "top_bid_qty":   round(top_bid[1], 1),
        "top_ask_price": top_ask[0],
        "top_ask_qty":   round(top_ask[1], 1),
    }


def trade_flow(symbol: str, limit: int = 500) -> dict:
    raw = _get("/api/v3/trades", {"symbol": BINANCE_SYMBOLS[symbol], "limit": limit})
    if not raw:
        return {}

    buy_vol  = sum(float(t["qty"]) for t in raw if not t["isBuyerMaker"])
    sell_vol = sum(float(t["qty"]) for t in raw if     t["isBuyerMaker"])
    total    = buy_vol + sell_vol or 1
    buy_pct  = round(buy_vol / total * 100, 1)

    # Large trades = top 10% by size
    qtys      = sorted(float(t["qty"]) for t in raw)
    threshold = qtys[int(len(qtys) * 0.9)] if qtys else 0
    lg_buys   = sum(1 for t in raw if not t["isBuyerMaker"] and float(t["qty"]) >= threshold)
    lg_sells  = sum(1 for t in raw if     t["isBuyerMaker"] and float(t["qty"]) >= threshold)

    return {
        "buy_pct":    buy_pct,
        "sell_pct":   round(100 - buy_pct, 1),
        "bias":       "BUY" if buy_pct > 55 else "SELL" if buy_pct < 45 else "NEUTRAL",
        "large_buys": lg_buys,
        "large_sells":lg_sells,
    }


def stats_24h(symbol: str) -> dict:
    raw = _get("/api/v3/ticker/24hr", {"symbol": BINANCE_SYMBOLS[symbol]})
    if not raw:
        return {}
    return {
        "change_pct":  round(float(raw.get("priceChangePercent", 0)), 2),
        "volume_usd":  round(float(raw.get("quoteVolume", 0))),
        "high":        float(raw.get("highPrice", 0)),
        "low":         float(raw.get("lowPrice", 0)),
        "trade_count": int(raw.get("count", 0)),
    }


def volume_by_tf(symbol: str, timeframes: list) -> dict:
    """
    Fetch Binance klines for each timeframe and compute:
    - current (last closed) candle volume
    - 20-bar average volume
    - ratio and label (HIGH / NORMAL / LOW)
    - up_vol vs down_vol (directional volume bias)
    """
    result = {}
    for tf in timeframes:
        interval = _TF_MAP.get(tf)
        if not interval:
            continue
        raw = _get("/api/v3/klines", {
            "symbol":   BINANCE_SYMBOLS[symbol],
            "interval": interval,
            "limit":    22,   # 20 for avg + 1 closed + 1 forming
        })
        if not raw or len(raw) < 5:
            continue

        closed = raw[:-1]   # exclude the still-forming candle
        vols   = [float(k[5]) for k in closed]
        avg20  = sum(vols[-20:]) / min(len(vols), 20)
        cur    = vols[-1]
        ratio  = round(cur / avg20, 2) if avg20 else 1.0

        # Directional volume: up candle (close > open) vs down candle
        up_vol   = sum(float(k[5]) for k in closed[-10:] if float(k[4]) >= float(k[1]))
        down_vol = sum(float(k[5]) for k in closed[-10:] if float(k[4]) <  float(k[1]))
        dv_bias  = "BULLISH" if up_vol > down_vol * 1.2 else "BEARISH" if down_vol > up_vol * 1.2 else "NEUTRAL"

        result[tf] = {
            "current":  round(cur, 0),
            "avg_20":   round(avg20, 0),
            "ratio":    ratio,
            "label":    "HIGH" if ratio > 1.5 else "LOW" if ratio < 0.5 else "NORMAL",
            "dv_bias":  dv_bias,
            "up_vol":   round(up_vol, 0),
            "down_vol": round(down_vol, 0),
        }
        time.sleep(0.15)

    return result


def fetch_all(symbols: list, timeframes: list) -> dict:
    """Fetch all Binance market data for every symbol."""
    result = {}
    for sym in symbols:
        ob   = order_book(sym)
        time.sleep(0.2)
        tf_  = trade_flow(sym)
        time.sleep(0.2)
        s24  = stats_24h(sym)
        time.sleep(0.2)
        vol  = volume_by_tf(sym, timeframes)

        result[sym] = {
            "order_book":  ob,
            "trade_flow":  tf_,
            "stats_24h":   s24,
            "volume_by_tf": vol,
        }
        time.sleep(0.3)

    return result
