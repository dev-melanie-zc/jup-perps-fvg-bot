#!/usr/bin/env python3

import os, time, asyncio, json, threading, smtplib, ssl, requests
import agent as ai_agent
import market_data
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Set
from email.message import EmailMessage

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.requests import Request
import pandas as pd
import trading

LOCAL_TZ    = ZoneInfo("America/Edmonton")
SYMBOLS     = ["SOL", "ETH", "WBTC"]
TIMEFRAMES  = ["15m", "1h", "4h", "12h"]
FETCH_LIMIT = 100
MIN_GAP     = 0.05
POLL_SECS   = 60
API_SLEEP   = 0.5   # seconds between Birdeye calls (paid plan: 15 RPS)

BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_BASE    = "https://public-api.birdeye.so"

JUP_PERP_MINTS = {
    "SOL":  "So11111111111111111111111111111111111111112",
    "ETH":  "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
    "WBTC": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",
}

_BIRDEYE_TF = {
    "15m":"15m", "1h":"1H", "4h":"4H", "6h":"6H",
    "8h":"8H",  "12h":"12H", "1d":"1D",
}
_TF_SECS = {
    "1m":60,  "3m":180,  "5m":300,   "15m":900,  "30m":1800,
    "1h":3600,"2h":7200, "4h":14400, "6h":21600,
    "8h":28800,"12h":43200,"1d":86400,
}

def _load_email_cfg():
    try:
        with open("config.json") as f:
            cfg = json.load(f)
        e = cfg.get("email", {})
        return (
            e.get("sender", os.environ.get("ALERT_EMAIL_FROM", "")),
            e.get("app_pass", os.environ.get("ALERT_EMAIL_PASS", "")),
            ",".join(e.get("recipients", [])) or os.environ.get("ALERT_EMAIL_TO", ""),
        )
    except Exception:
        return (os.environ.get("ALERT_EMAIL_FROM", ""),
                os.environ.get("ALERT_EMAIL_PASS", ""),
                os.environ.get("ALERT_EMAIL_TO", ""))

ALERT_EMAIL_FROM, ALERT_EMAIL_PASS, ALERT_EMAIL_TO = _load_email_cfg()
_emailed_zones: Set[str] = set()
_emailed_ai_signals: Dict[str, float] = {}   # key → epoch, 15-min dedup
AI_SIGNAL_DEDUP_SECS = 900


def _price_dec(sym: str) -> int:
    if "BTC" in sym or sym == "WBTC": return 1
    if sym == "ETH":  return 2
    return 3  # SOL


def _send_zone_email(sym: str, tf: str, fvg: dict):
    if not ALERT_EMAIL_FROM or not ALERT_EMAIL_PASS or not ALERT_EMAIL_TO:
        return
    key = f"{sym}::{tf}::{fvg.get('formed_at')}::{fvg['type']}"
    if key in _emailed_zones:
        return
    _emailed_zones.add(key)

    side  = "LONG"     if fvg["type"] == "bull" else "SHORT"
    zone  = "DISCOUNT" if fvg["type"] == "bull" else "PREMIUM"
    dec   = _price_dec(sym)
    lo    = f"{fvg['low']:.{dec}f}"
    hi    = f"{fvg['high']:.{dec}f}"
    price = prices.get(sym)
    pstr  = f"{price:.{dec}f}" if price else "?"

    subject = f"FVG ALERT — {side} NOW  {sym} {tf.upper()}"
    body    = (
        f"{side} NOW — {sym} {tf.upper()}\n"
        f"Zone : {zone}\n"
        f"Range: {lo} – {hi}\n"
        f"Price: {pstr}\n"
        f"Formed: {fvg.get('formed_at', '?')}\n"
    )
    try:
        msg            = EmailMessage()
        msg["From"]    = ALERT_EMAIL_FROM
        msg["To"]      = ALERT_EMAIL_TO
        msg["Subject"] = subject
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.ehlo()
            s.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASS)
            s.send_message(msg)
        print(f"EMAIL SENT: {subject}")
    except Exception as e:
        print(f"Email error: {e}")


def _send_ai_signal_email(result: dict):
    """Email when AI detects an APPROACHING or INSIDE trade signal."""
    action = result.get("action", "WAIT")
    if action not in ("LONG", "SHORT"):
        return
    sym  = result.get("symbol") or ""
    tf   = result.get("timeframe") or ""
    zs   = result.get("zone_status") or ""
    conf = result.get("confidence", "")
    if not sym:
        return

    key = f"ai::{action}::{sym}::{tf}::{result.get('zone_low')}::{result.get('zone_high')}"
    now_ts = time.time()
    last_ts = _emailed_ai_signals.get(key)
    if last_ts is not None and (now_ts - last_ts) < AI_SIGNAL_DEDUP_SECS:
        return
    _emailed_ai_signals[key] = now_ts

    dec   = _price_dec(sym)
    def fp(v): return f"{float(v):.{dec}f}" if v is not None else "?"

    entry  = fp(result.get("entry_price"))
    sl     = fp(result.get("stop_loss"))
    tp     = fp(result.get("target"))
    rr     = result.get("rr") or "?"
    reason = result.get("reasoning") or ""
    conf_  = result.get("confluence") or ""
    zlo    = fp(result.get("zone_low"))
    zhi    = fp(result.get("zone_high"))

    subject = f"AI SIGNAL — {action} {sym} {tf.upper()} [{conf}]  {zs}"
    body = (
        f"AI {action} SIGNAL\n"
        f"Symbol    : {sym}  {tf.upper()}\n"
        f"Zone      : {zlo} – {zhi}  ({zs})\n"
        f"Entry     : {entry}\n"
        f"Stop Loss : {sl}\n"
        f"Take Profit: {tp}\n"
        f"R:R       : {rr}\n"
        f"Confidence: {conf}\n"
        f"Confluence: {conf_}\n"
        f"Reasoning : {reason}\n"
    )
    _smtp_send(subject, body)


def _send_trade_executed_email(action: str, sym: str, entry: float,
                                stop: float, target: float, rr: str, tx: str):
    """Email when a trade order is submitted on-chain."""
    dec = _price_dec(sym)
    def fp(v): return f"{float(v):.{dec}f}" if v is not None else "?"
    subject = f"TRADE ENTERED — {action} {sym} @ {fp(entry)}"
    body = (
        f"TRADE SUBMITTED ON-CHAIN\n"
        f"Direction  : {action}\n"
        f"Symbol     : {sym}\n"
        f"Entry      : {fp(entry)}\n"
        f"Stop Loss  : {fp(stop)}\n"
        f"Take Profit: {fp(target)}\n"
        f"R:R        : {rr}\n"
        f"TX         : {tx}\n"
    )
    _smtp_send(subject, body)


def _smtp_send(subject: str, body: str):
    if not ALERT_EMAIL_FROM or not ALERT_EMAIL_PASS or not ALERT_EMAIL_TO:
        return
    try:
        msg            = EmailMessage()
        msg["From"]    = ALERT_EMAIL_FROM
        msg["To"]      = ALERT_EMAIL_TO
        msg["Subject"] = subject
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.ehlo()
            s.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASS)
            s.send_message(msg)
        print(f"EMAIL SENT: {subject}")
    except Exception as e:
        print(f"Email error ({subject}): {e}")


def send_zone_alerts():
    for sym in SYMBOLS:
        price = prices.get(sym)
        if not price:
            continue
        for tf in TIMEFRAMES:
            tf_data = state.get(sym, {}).get(tf)
            if not tf_data:
                continue
            for fvg in tf_data.get("fvgs", []):
                if not fvg.get("filled") and fvg["low"] <= price <= fvg["high"]:
                    print(f"ZONE HIT: {sym} {tf} {fvg['type']} {fvg['low']}-{fvg['high']} price={price}")
                    threading.Thread(
                        target=_send_zone_email, args=(sym, tf, fvg), daemon=True
                    ).start()


# ── Birdeye data layer ────────────────────────────────────────────────────────

def _birdeye_headers() -> dict:
    return {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana", "accept": "application/json"}


def birdeye_fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> list:
    tf_key  = _BIRDEYE_TF.get(timeframe)
    tf_secs = _TF_SECS.get(timeframe, 3600)
    if not tf_key:
        return []
    now_ts = int(time.time())
    try:
        resp = requests.get(
            f"{BIRDEYE_BASE}/defi/v3/ohlcv",
            headers=_birdeye_headers(),
            params={
                "address":   JUP_PERP_MINTS[symbol],
                "type":      tf_key,
                "time_from": now_ts - tf_secs * (limit + 5),
                "time_to":   now_ts,
            },
            timeout=30,
        )
        if not resp.ok:
            print(f"Birdeye {resp.status_code} for {symbol}/{timeframe}")
            return []
        items = resp.json().get("data", {}).get("items", [])
        return [
            [int(item["unix_time"]) * 1000,
             float(item["o"]), float(item["h"]),
             float(item["l"]), float(item["c"]),
             float(item.get("v") or 0)]
            for item in items
        ][-limit:]
    except Exception as e:
        print(f"birdeye_fetch_ohlcv({symbol},{timeframe}): {e}")
        return []


def fetch_ticker_price(sym: str) -> Optional[float]:
    try:
        resp = requests.get(
            f"{BIRDEYE_BASE}/defi/price",
            headers=_birdeye_headers(),
            params={"address": JUP_PERP_MINTS[sym]},
            timeout=10,
        )
        if resp.ok:
            return float(resp.json()["data"]["value"])
    except Exception as e:
        print(f"Ticker error {sym}: {e}")
    return None


# ── OHLCV helpers ─────────────────────────────────────────────────────────────

def ohlcv_to_df(raw) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df


def resample_ohlcv(df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    mapping = {
        "4h":"4h","6h":"6h","8h":"8h","12h":"12h",
        "1h":"1h","2h":"2h","1d":"1d",
    }
    rule = mapping.get(target_tf, target_tf)
    agg  = {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
    return df.resample(rule, label="right", closed="right").agg(agg).dropna()


def to_local(ts: pd.Timestamp) -> str:
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%S")


# ── FVG detection ─────────────────────────────────────────────────────────────

def detect_fvgs(df: pd.DataFrame, tf: str, last_ts: pd.Timestamp) -> List[Dict]:
    if len(df) < 3:
        return []
    lows, highs, closes = df["low"].values, df["high"].values, df["close"].values
    ts  = df.index
    n   = len(df)
    fvgs = []
    future = last_ts + pd.Timedelta(seconds=5 * _TF_SECS.get(tf, 3600))

    for i in range(2, n):
        price = float(closes[i])

        if lows[i] > highs[i - 2]:
            gl, gh = float(highs[i - 2]), float(lows[i])
            if (gh - gl) / max(price, 1e-12) >= MIN_GAP / 100:
                fvg = {
                    "type": "bull", "zone_type": "DISCOUNT",
                    "low": gl, "high": gh, "mid": (gl + gh) / 2,
                    "formed_at": to_local(ts[i]),
                    "filled": False, "filled_at": None,
                    "x1": to_local(future),
                }
                for j in range(i + 1, n):
                    if lows[j] <= gh and highs[j] >= gl:
                        fvg["filled"]    = True
                        fvg["filled_at"] = to_local(ts[j])
                        fvg["x1"]        = to_local(ts[j])
                        break
                fvgs.append(fvg)

        if highs[i] < lows[i - 2]:
            gh, gl = float(lows[i - 2]), float(highs[i])
            if (gh - gl) / max(price, 1e-12) >= MIN_GAP / 100:
                fvg = {
                    "type": "bear", "zone_type": "PREMIUM",
                    "low": gl, "high": gh, "mid": (gl + gh) / 2,
                    "formed_at": to_local(ts[i]),
                    "filled": False, "filled_at": None,
                    "x1": to_local(future),
                }
                for j in range(i + 1, n):
                    if lows[j] <= gh and highs[j] >= gl:
                        fvg["filled"]    = True
                        fvg["filled_at"] = to_local(ts[j])
                        fvg["x1"]        = to_local(ts[j])
                        break
                fvgs.append(fvg)

    return fvgs


def process_df(sym: str, tf: str, df: pd.DataFrame):
    df_closed = df.iloc[:-1]
    last_ts   = df.index[-1]
    fvgs      = detect_fvgs(df_closed, tf, last_ts)
    state[sym][tf] = {
        "candles": [
            {"t": to_local(df.index[i]),
             "o": float(df.iloc[i]["open"]),
             "h": float(df.iloc[i]["high"]),
             "l": float(df.iloc[i]["low"]),
             "c": float(df.iloc[i]["close"]),
             "v": float(df.iloc[i]["volume"])}
            for i in range(len(df))
        ],
        "fvgs":      fvgs,
        "open_fvgs": sum(1 for z in fvgs if not z["filled"]),
    }


# ── Main state ────────────────────────────────────────────────────────────────

state:  Dict = {sym: {tf: None for tf in TIMEFRAMES} for sym in SYMBOLS}
prices: Dict = {sym: None for sym in SYMBOLS}
ai_rec: Dict = {}
ready        = False
clients: List[WebSocket] = []
_loop:  Optional[asyncio.AbstractEventLoop] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_event_loop()
    threading.Thread(target=polling_loop, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)


def fetch_and_update():
    global ready
    for sym in SYMBOLS:
        try:
            prices[sym] = fetch_ticker_price(sym)
            time.sleep(API_SLEEP)

            # 1h fetch → resample to 4h and 12h (saves API calls)
            raw_1h = birdeye_fetch_ohlcv(sym, "1h", limit=500)
            df_1h  = ohlcv_to_df(raw_1h)
            time.sleep(API_SLEEP)

            if not df_1h.empty:
                process_df(sym, "1h", df_1h.iloc[-FETCH_LIMIT:])
                for tf in ("4h", "12h"):
                    rs = resample_ohlcv(df_1h, tf)
                    if not rs.empty:
                        process_df(sym, tf, rs.iloc[-FETCH_LIMIT:])

            raw_15m = birdeye_fetch_ohlcv(sym, "15m", limit=FETCH_LIMIT)
            df_15m  = ohlcv_to_df(raw_15m)
            if not df_15m.empty:
                process_df(sym, "15m", df_15m)
            time.sleep(API_SLEEP)

        except Exception as e:
            print(f"Error {sym}: {e}")

    cfg = trading.load_config()
    sync = trading.reconcile_positions(cfg)
    if sync.get("synced"):
        print(f"[risk] chain reconcile: {sync['msg']}")
    slot_status = trading.trade_slot_status(cfg)
    if slot_status["full"] and slot_status.get("ignore_signals_when_full", True):
        trading.record_ignored_signal(slot_status["reason"], {"source": "email_alerts"})
    else:
        send_zone_alerts()
    has_data = any(
        state[sym][tf] is not None
        for sym in SYMBOLS for tf in TIMEFRAMES
    )
    if has_data:
        ready = True
    elif not ready:
        print("No data from Birdeye — check API key")


def check_alerts() -> dict:
    cfg = trading.load_config()
    slot_status = trading.trade_slot_status(cfg)
    if slot_status["full"] and slot_status.get("ignore_signals_when_full", True):
        trading.record_ignored_signal(slot_status["reason"], {"source": "ui_alerts"})
        return {sym: [] for sym in SYMBOLS}

    result = {}
    for sym in SYMBOLS:
        sym_alerts = []
        price = prices.get(sym)
        if price:
            for tf in TIMEFRAMES:
                tf_data = state.get(sym, {}).get(tf)
                if not tf_data:
                    continue
                for fvg in tf_data.get("fvgs", []):
                    if not fvg.get("filled") and fvg["low"] <= price <= fvg["high"]:
                        sym_alerts.append({**fvg, "tf": tf})
        result[sym] = sym_alerts
    return result


def build_payload() -> dict:
    return {
        "type":        "update",
        "prices":      prices,
        "state":       state,
        "symbols":     SYMBOLS,
        "timeframes":  TIMEFRAMES,
        "alerts":      check_alerts(),
        "ai":          ai_rec,
        "ai_log":      ai_agent.load_log(30),
        "daily_stats": trading.daily_stats(),
    }


def broadcast_sync(msg: dict):
    if _loop and clients:
        asyncio.run_coroutine_threadsafe(_do_broadcast(json.dumps(msg)), _loop)


async def _do_broadcast(data: str):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in clients:
            clients.remove(ws)


def run_agent():
    global ai_rec
    print("Bot: fetching market data…")
    cfg    = trading.load_config()
    sync   = trading.reconcile_positions(cfg)
    if sync.get("synced"):
        print(f"[risk] chain reconcile: {sync['msg']}")
    exits = trading.ensure_exit_orders(cfg)
    if exits.get("submitted") or exits.get("errors"):
        print(f"[risk] exit orders: {exits['msg']}")
    slots  = trading.trade_slot_status(cfg)
    mkt    = market_data.fetch_all(SYMBOLS, TIMEFRAMES)
    result = ai_agent.analyze(state, prices, SYMBOLS, TIMEFRAMES, mkt, slots)
    if result:
        ai_rec = result
        action = result.get("action", "?")
        sym    = result.get("symbol") or ""
        conf   = result.get("confidence", "")
        print(f"Bot → {action} {sym} [{conf}]")

        # Email alert for any actionable AI signal (approaching or inside zone)
        if action in ("LONG", "SHORT") and sym:
            threading.Thread(
                target=_send_ai_signal_email, args=(result,), daemon=True
            ).start()

        # Auto-trade if configured and signal is actionable
        if action in ("LONG", "SHORT") and sym and conf == "HIGH":
            if cfg.get("wallet", {}).get("auto_trade", False):
                trade_result = trading.execute_trade(
                    action, sym,
                    entry  = result.get("entry_price", 0),
                    stop   = result.get("stop_loss",   0),
                    target = result.get("target",      0),
                    config = cfg,
                )
                print(f"[auto-trade] {trade_result['msg']}")
                if trade_result.get("ok") and trade_result.get("tx"):
                    threading.Thread(
                        target=_send_trade_executed_email,
                        args=(
                            action, sym,
                            result.get("entry_price", 0),
                            result.get("stop_loss",   0),
                            result.get("target",      0),
                            result.get("rr", "?"),
                            trade_result["tx"],
                        ),
                        daemon=True,
                    ).start()

        broadcast_sync(build_payload())


def polling_loop():
    print("Fetching initial data from Birdeye…")
    fetch_and_update()
    broadcast_sync(build_payload())
    threading.Thread(target=run_agent, daemon=True).start()
    while True:
        time.sleep(POLL_SECS)
        print("Polling Birdeye…")
        fetch_and_update()
        broadcast_sync(build_payload())
        threading.Thread(target=run_agent, daemon=True).start()


@app.get("/")
async def index():
    with open("static/index.html") as f:
        return HTMLResponse(f.read())


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    try:
        if ready:
            await websocket.send_text(json.dumps(build_payload()))
        else:
            await websocket.send_text(json.dumps({"type": "loading", "msg": "Fetching from Birdeye…"}))
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in clients:
            clients.remove(websocket)


@app.get("/config")
async def get_config():
    cfg = trading.load_config()
    cfg.get("wallet", {}).pop("private_key", None)
    cfg.get("email",  {}).pop("app_pass",    None)   # never send password to browser
    return JSONResponse(cfg)


@app.post("/config")
async def post_config(request: Request):
    body = await request.json()

    # Private key → .env only, never stored in config.json
    pk = body.get("wallet", {}).pop("private_key", None)
    if pk and pk.strip():
        _save_env_key("SOLANA_PRIVATE_KEY", pk.strip())
        os.environ["SOLANA_PRIVATE_KEY"] = pk.strip()

    # Email app_pass — preserve existing if not provided in this save
    existing = trading.load_config()
    if "email" in body:
        if not body["email"].get("app_pass"):
            body["email"]["app_pass"] = existing.get("email", {}).get("app_pass", "")

    trading.save_config(body)

    # Reload email credentials into memory
    global ALERT_EMAIL_FROM, ALERT_EMAIL_PASS, ALERT_EMAIL_TO
    ALERT_EMAIL_FROM, ALERT_EMAIL_PASS, ALERT_EMAIL_TO = _load_email_cfg()

    return JSONResponse({"ok": True})


def _save_env_key(key: str, value: str):
    """Upsert KEY=value in .env without touching other lines."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    lines = []
    found = False
    try:
        with open(env_path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        pass
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(new_lines)
    print(f"[config] {key} written to .env")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"FVG chart server → http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
