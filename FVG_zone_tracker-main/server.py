#!/usr/bin/env python3

import os, time, asyncio, json, threading, smtplib, ssl
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Set
from email.message import EmailMessage

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import ccxt
import pandas as pd

LOCAL_TZ    = ZoneInfo("America/Edmonton")
SYMBOLS     = ['ETH/USDT:USDT', 'BTC/USDT:USDT', 'SOL/USDT:USDT']
TIMEFRAMES  = ['5m', '15m', '1h', '4h', '6h', '8h', '12h']
FETCH_LIMIT = 100
MIN_GAP     = 0.05   # % minimum gap to count as FVG
POLL_SECS   = 60

TF_SECS = {'5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '6h': 21600, '8h': 28800, '12h': 43200}
# 8h is not a native Phemex resolution — built by resampling 4h with a larger fetch
NATIVE_TFS  = ['5m', '15m', '1h', '4h', '6h', '12h']

# ── Email config (set these as Railway env vars) ──────────────────────────
ALERT_EMAIL_FROM = os.environ.get('ALERT_EMAIL_FROM', '')
ALERT_EMAIL_PASS = os.environ.get('ALERT_EMAIL_PASS', '')
ALERT_EMAIL_TO   = os.environ.get('ALERT_EMAIL_TO',   '')
_emailed_zones: Set[str] = set()


def _send_zone_email(sym: str, tf: str, fvg: dict):
    if not ALERT_EMAIL_FROM or not ALERT_EMAIL_PASS or not ALERT_EMAIL_TO:
        return
    key = f"{sym}::{tf}::{fvg.get('formed_at')}::{fvg['type']}"
    if key in _emailed_zones:
        return
    _emailed_zones.add(key)

    sym_short = sym.split('/')[0]
    is_bull   = fvg['type'] == 'bull'
    side      = 'LONG'     if is_bull else 'SHORT'
    zone      = 'DISCOUNT' if is_bull else 'PREMIUM'
    dec       = 1 if 'BTC' in sym else 2
    lo        = f"{fvg['low']:.{dec}f}"
    hi        = f"{fvg['high']:.{dec}f}"
    price     = prices.get(sym)
    price_str = f"{price:.{dec}f}" if price else '?'

    subject = f"FVG ALERT — {side} NOW  {sym_short} {tf.upper()}"
    body    = (
        f"{side} NOW — {sym_short} {tf.upper()}\n"
        f"Zone : {zone}\n"
        f"Range: {lo} – {hi}\n"
        f"Price: {price_str}\n"
        f"Formed: {fvg.get('formed_at', '?')}\n"
    )
    try:
        msg            = EmailMessage()
        msg['From']    = ALERT_EMAIL_FROM
        msg['To']      = ALERT_EMAIL_TO
        msg['Subject'] = subject
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.ehlo()
            s.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASS)
            s.send_message(msg)
        print(f"EMAIL SENT: {subject}")
    except Exception as e:
        print(f"Email error: {e}")


def send_zone_alerts():
    print(f"EMAIL CFG: from={bool(ALERT_EMAIL_FROM)} pass={bool(ALERT_EMAIL_PASS)} to={bool(ALERT_EMAIL_TO)}")
    for sym in SYMBOLS:
        price = prices.get(sym)
        if not price:
            continue
        for tf in TIMEFRAMES:
            tf_data = state.get(sym, {}).get(tf)
            if not tf_data:
                continue
            for fvg in tf_data.get('fvgs', []):
                if not fvg.get('filled') and fvg['low'] <= price <= fvg['high']:
                    print(f"ZONE HIT: {sym} {tf} {fvg['type']} {fvg['low']}-{fvg['high']} price={price}")
                    threading.Thread(
                        target=_send_zone_email, args=(sym, tf, fvg), daemon=True
                    ).start()


_ex = ccxt.phemex({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
_ex.load_markets()

state:  Dict = {sym: {tf: None for tf in TIMEFRAMES} for sym in SYMBOLS}
prices: Dict = {sym: None for sym in SYMBOLS}
ready        = False

app     = FastAPI()
clients: List[WebSocket] = []
_loop:  Optional[asyncio.AbstractEventLoop] = None


def to_local(ts: pd.Timestamp) -> str:
    """UTC Timestamp → naive local ISO string (no offset) so Plotly displays as-is."""
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC')
    return ts.tz_convert(LOCAL_TZ).strftime('%Y-%m-%dT%H:%M:%S')


def ohlcv_to_df(raw) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    return df


def detect_fvgs(df: pd.DataFrame, tf: str, last_ts: pd.Timestamp) -> List[Dict]:
    """Detect bullish and bearish FVGs; mark which are filled within the dataset."""
    if len(df) < 3:
        return []
    lows, highs, closes = df['low'].values, df['high'].values, df['close'].values
    ts = df.index
    n  = len(df)
    fvgs = []
    # Open FVGs extend 5 periods beyond the last fetched candle
    future = last_ts + pd.Timedelta(seconds=5 * TF_SECS.get(tf, 3600))

    for i in range(2, n):
        price = float(closes[i])

        # Bullish FVG: candle[i].low > candle[i-2].high
        if lows[i] > highs[i - 2]:
            gl, gh = float(highs[i - 2]), float(lows[i])
            if (gh - gl) / max(price, 1e-12) >= MIN_GAP / 100:
                fvg = {
                    'type': 'bull', 'zone_type': 'DISCOUNT',
                    'low': gl, 'high': gh, 'mid': (gl + gh) / 2,
                    'formed_at': to_local(ts[i]),
                    'filled': False, 'filled_at': None,
                    'x1': to_local(future),
                }
                for j in range(i + 1, n):
                    if lows[j] <= gh and highs[j] >= gl:
                        fvg['filled']    = True
                        fvg['filled_at'] = to_local(ts[j])
                        fvg['x1']        = to_local(ts[j])
                        break
                fvgs.append(fvg)

        # Bearish FVG: candle[i].high < candle[i-2].low
        if highs[i] < lows[i - 2]:
            gh, gl = float(lows[i - 2]), float(highs[i])
            if (gh - gl) / max(price, 1e-12) >= MIN_GAP / 100:
                fvg = {
                    'type': 'bear', 'zone_type': 'PREMIUM',
                    'low': gl, 'high': gh, 'mid': (gl + gh) / 2,
                    'formed_at': to_local(ts[i]),
                    'filled': False, 'filled_at': None,
                    'x1': to_local(future),
                }
                for j in range(i + 1, n):
                    if lows[j] <= gh and highs[j] >= gl:
                        fvg['filled']    = True
                        fvg['filled_at'] = to_local(ts[j])
                        fvg['x1']        = to_local(ts[j])
                        break
                fvgs.append(fvg)

    return fvgs


def resample_8h(df: pd.DataFrame) -> pd.DataFrame:
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    return df.resample('8h', label='right', closed='right').agg(agg).dropna()


def fetch_ticker_price(sym: str) -> Optional[float]:
    try:
        tick = _ex.fetch_ticker(sym)
        for k in ('last', 'close', 'bid', 'ask'):
            v = tick.get(k)
            if v:
                f = float(v)
                if f > 0:
                    return f
    except Exception as e:
        print(f"Ticker error {sym}: {e}")
    return None


def process_df(sym: str, tf: str, df: pd.DataFrame):
    """Detect FVGs and build the state entry for one symbol+timeframe."""
    df_closed = df.iloc[:-1]
    last_ts   = df.index[-1]
    fvgs      = detect_fvgs(df_closed, tf, last_ts)
    state[sym][tf] = {
        'candles': [
            {'t': to_local(df.index[i]),
             'o': float(df.iloc[i]['open']),
             'h': float(df.iloc[i]['high']),
             'l': float(df.iloc[i]['low']),
             'c': float(df.iloc[i]['close'])}
            for i in range(len(df))
        ],
        'fvgs':      fvgs,
        'open_fvgs': sum(1 for z in fvgs if not z['filled']),
    }


def fetch_and_update():
    global ready
    for sym in SYMBOLS:
        try:
            prices[sym] = fetch_ticker_price(sym)
            time.sleep(0.3)

            # Fetch 4h (max 100 candles on Phemex) — reused for both 4h and 8h resample
            df_4h_ext: Optional[pd.DataFrame] = None
            try:
                raw_4h = _ex.fetch_ohlcv(sym, '4h', limit=FETCH_LIMIT)
                if raw_4h:
                    df_4h_ext = ohlcv_to_df(raw_4h)
                time.sleep(0.3)
            except Exception as e:
                print(f"  {sym} 4h (extended): {e}")

            for tf in TIMEFRAMES:
                try:
                    if tf == '4h':
                        if df_4h_ext is not None:
                            df = df_4h_ext.iloc[-FETCH_LIMIT:]
                            process_df(sym, tf, df)
                    elif tf == '8h':
                        if df_4h_ext is not None:
                            df_8h = resample_8h(df_4h_ext)
                            if not df_8h.empty:
                                df = df_8h.iloc[-FETCH_LIMIT:]
                                process_df(sym, tf, df)
                    else:
                        raw = _ex.fetch_ohlcv(sym, tf, limit=FETCH_LIMIT)
                        if raw:
                            process_df(sym, tf, ohlcv_to_df(raw))
                        time.sleep(0.3)
                except Exception as e:
                    print(f"  {sym} {tf}: {e}")

        except Exception as e:
            print(f"Error {sym}: {e}")

    send_zone_alerts()
    ready = True


def check_alerts() -> dict:
    result = {}
    for sym in SYMBOLS:
        sym_alerts = []
        price = prices.get(sym)
        if price:
            for tf in TIMEFRAMES:
                tf_data = state.get(sym, {}).get(tf)
                if not tf_data:
                    continue
                for fvg in tf_data.get('fvgs', []):
                    if not fvg.get('filled') and fvg['low'] <= price <= fvg['high']:
                        sym_alerts.append({**fvg, 'tf': tf})
        result[sym] = sym_alerts
    return result


def build_payload() -> dict:
    return {
        'type':       'update',
        'prices':     prices,
        'state':      state,
        'symbols':    SYMBOLS,
        'timeframes': TIMEFRAMES,
        'alerts':     check_alerts(),
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


def polling_loop():
    print("Fetching initial data from Phemex…")
    fetch_and_update()
    broadcast_sync(build_payload())
    while True:
        time.sleep(POLL_SECS)
        print("Polling Phemex…")
        fetch_and_update()
        broadcast_sync(build_payload())


@app.on_event("startup")
async def on_startup():
    global _loop
    _loop = asyncio.get_event_loop()
    threading.Thread(target=polling_loop, daemon=True).start()


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
            await websocket.send_text(json.dumps({'type': 'loading', 'msg': 'Fetching from Phemex…'}))
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in clients:
            clients.remove(websocket)


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8000))
    print(f"FVG chart server → http://localhost:{port}")
    uvicorn.run(app, host='0.0.0.0', port=port, log_level='warning')
