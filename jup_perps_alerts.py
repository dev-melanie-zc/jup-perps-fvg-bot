#!/usr/bin/env python3
## Jupiter Perps FVG Alert Bot — Birdeye edition
## Pulls candles for SOL, ETH, WBTC from Birdeye (the source jup.ag/perps uses)
## Same signal logic as the Phemex BTC original. Email alerts to Gmail.

import time
import random
import ssl
import os
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Callable, Tuple
from email.message import EmailMessage
import smtplib
import pandas as pd

# --- Timezone (Alberta gang) ---
USE_FIXED_MST = False
TIMEZONE = "America/Edmonton"
ALIGN_SCHEDULE_TO_LOCAL_TZ = True

try:
    from zoneinfo import ZoneInfo
    _have_zoneinfo = True
except Exception:
    _have_zoneinfo = False

if USE_FIXED_MST:
    local_tz = timezone(timedelta(hours=-7))
    _tz_label_override = "MST"
else:
    if _have_zoneinfo:
        local_tz = ZoneInfo(TIMEZONE)
    else:
        import pytz
        local_tz = pytz.timezone(TIMEZONE)
    _tz_label_override = None

def now_local() -> datetime:
    return datetime.now(local_tz)

def now_local_str() -> str:
    dt = now_local()
    abbr = _tz_label_override or dt.tzname()
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} {abbr}"

def fmt_ts_local(ts: pd.Timestamp) -> str:
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts, tz='UTC')
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC')
    ts_local = ts.tz_convert(local_tz)
    abbr = _tz_label_override or ts_local.tzname()
    return f"{ts_local.strftime('%Y-%m-%d %H:%M:%S')} {abbr}"

# =========================
# CONFIGURATION
# =========================
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "PUT_YOUR_KEY_HERE")
BIRDEYE_BASE = "https://public-api.birdeye.so"

SYMBOLS = ["SOL", "ETH", "WBTC"]

JUP_PERP_MINTS = {
    "SOL":  "So11111111111111111111111111111111111111112",
    "ETH":  "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
    "WBTC": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",
}

TIMEFRAMES = ["4h", "6h", "8h", "12h", "15m"]

FETCH_LIMIT = 100
LOOKBACK = 10
MIN_GAP = 0.05
MAX_RETRIES = 5
BASE_SLEEP_S = 1
SCAN_DELAY_BETWEEN_SYMBOLS_SECONDS = 15
PER_TF_DELAY_SECONDS = 0
ALERT_SEND_DELAY_SECONDS = 2
RANDOM_START_JITTER_SECONDS = 5
ALERT_DEDUP_SECONDS = 15 * 60
RUN_EVERY_MINUTES = 5
RUN_AT_SECOND = 15
CLOSE_CHECK_SECOND = 15

TF_CLOSE_SCHEDULE: Dict[str, List[Tuple[int, int]]] = {
    "6h":  [(0, 0), (6, 0), (12, 0), (18, 0)],
    "4h":  [(2, 0), (6, 0), (10, 0), (14, 0), (18, 0), (22, 0)],
    "8h":  [(2, 0), (10, 0), (18, 0)],
    "12h": [(6, 0), (18, 0)],
    "15m": [
        (0, 0), (0, 15), (0, 30), (0, 45),
        (1, 0), (1, 15), (1, 30), (1, 45),
        (2, 0), (2, 15), (2, 30), (2, 45),
        (3, 0), (3, 15), (3, 30), (3, 45),
        (4, 0), (4, 15), (4, 30), (4, 45),
        (5, 0), (5, 15), (5, 30), (5, 45),
        (6, 0), (6, 15), (6, 30), (6, 45),
        (7, 0), (7, 15), (7, 30), (7, 45),
        (8, 0), (8, 15), (8, 30), (8, 45),
        (9, 0), (9, 15), (9, 30), (9, 45),
        (10, 0), (10, 15), (10, 30), (10, 45),
        (11, 0), (11, 15), (11, 30), (11, 45),
        (12, 0), (12, 15), (12, 30), (12, 45),
        (13, 0), (13, 15), (13, 30), (13, 45),
        (14, 0), (14, 15), (14, 30), (14, 45),
        (15, 0), (15, 15), (15, 30), (15, 45),
        (16, 0), (16, 15), (16, 30), (16, 45),
        (17, 0), (17, 15), (17, 30), (17, 45),
        (18, 0), (18, 15), (18, 30), (18, 45),
        (19, 0), (19, 15), (19, 30), (19, 45),
        (20, 0), (20, 15), (20, 30), (20, 45),
        (21, 0), (21, 15), (21, 30), (21, 45),
        (22, 0), (22, 15), (22, 30), (22, 45),
        (23, 0), (23, 15), (23, 30), (23, 45),
    ],
}

SMTP_HOST        = "smtp.gmail.com"
PORT_STARTTLS    = 587
PORT_SSL         = 465
TRANSPORT_MODE   = "starttls"
EMAIL_MAX_RETRIES = 4
EMAIL_BACKOFF_SECONDS = [30, 120, 300, 600]
TRANSIENT_4XX_CODES = {421, 450, 451, 452, 455, 471, 472, 473, 499}

def _load_email_cfg():
    try:
        with open("config.json") as f:
            e = json.load(f).get("email", {})
        return e.get("sender", ""), e.get("app_pass", ""), e.get("recipients", [])
    except Exception:
        return "", "", []

SENDER_EMAIL, SENDER_APP_PASS, ALERT_RECIPIENTS = _load_email_cfg()

# =========================
# UTILS
# =========================
def log(msg: str) -> None:
    print(f"[{now_local_str()}] {msg}", flush=True)

def norm_symbol(sym: str) -> str:
    return sym

def normalize_for_sms(s: str) -> str:
    return (s.replace('—', '-').replace('–', '-').replace('▶', '>').replace('‘', "'").replace('“', '"').replace('”', '"'))

ALERTS_FILE = "alerts.txt"

def append_to_alerts_file(line: str) -> None:
    try:
        with open(ALERTS_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{now_local_str()}] {line}\n")
    except Exception as e:
        log(f"alerts.txt write failed: {e}")

# =========================
# EXCHANGE INIT — stub (no exchange needed for Birdeye)
# =========================
def init_exchange():
    log("Birdeye data source — no exchange object to init")
    return None

# =========================
# BIRDEYE CLIENT
# =========================
_BIRDEYE_TF = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "8h": "8H", "12h": "12H", "1d": "1D",
}

_TF_SECS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400,
}

def birdeye_fetch_ohlcv(symbol: str, timeframe: str, limit: int):
    tf_key = _BIRDEYE_TF.get(timeframe)
    if not tf_key:
        log(f"birdeye_fetch_ohlcv: unknown timeframe {timeframe}")
        return []
    tf_secs = _TF_SECS.get(timeframe, 3600)
    now_ts = int(time.time())
    time_from = now_ts - tf_secs * (limit + 5)
    time_to = now_ts

    headers = {
        "X-API-KEY": BIRDEYE_API_KEY,
        "x-chain": "solana",
        "accept": "application/json",
    }
    params = {
        "address": JUP_PERP_MINTS[symbol],
        "type": tf_key,
        "time_from": time_from,
        "time_to": time_to,
    }

    backoff = 1.0
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                f"{BIRDEYE_BASE}/defi/v3/ohlcv",
                headers=headers,
                params=params,
                timeout=30,
            )
            if resp.status_code == 429:
                log(f"Birdeye 429 for {symbol}/{timeframe} — backing off {backoff:.1f}s")
                time.sleep(backoff)
                backoff *= 2
                continue
            if not resp.ok:
                log(f"Birdeye HTTP {resp.status_code} for {symbol}/{timeframe} — retrying in {backoff:.1f}s")
                time.sleep(backoff)
                backoff *= 2
                continue
            data = resp.json()
            raw_data = data.get("data", {})
            items = raw_data.get("items", []) if isinstance(raw_data, dict) else []
            result = [
                [int(item["unix_time"]) * 1000,
                 float(item["o"]),
                 float(item["h"]),
                 float(item["l"]),
                 float(item["c"]),
                 float(item.get("v") or 0)]
                for item in items
            ]
            if len(result) > limit:
                result = result[-limit:]
            return result
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                log(f"birdeye_fetch_ohlcv({symbol}, {timeframe}) failed after {MAX_RETRIES} attempts: {e}")
                return []
            log(f"birdeye_fetch_ohlcv({symbol}, {timeframe}) attempt {attempt+1} error: {e} — retrying in {backoff:.1f}s")
            time.sleep(backoff)
            backoff *= 2
    return []

def fetch_symbol_frames(exchange, symbol: str, target_bars: int) -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}

    # Base 1h candles from Birdeye → resample to 4h/6h/8h/12h
    raw_1h = birdeye_fetch_ohlcv(symbol, "1h", limit=500)
    df_1h = ohlcv_to_df(raw_1h)
    time.sleep(0.3)  # gentle pacing between Birdeye calls

    for tf in ("4h", "6h", "8h", "12h"):
        frames[tf] = resample_ohlcv(df_1h, tf)
        if len(frames[tf]) > target_bars:
            frames[tf] = frames[tf].iloc[-target_bars:]

    # 15m fetched natively
    raw_15m = birdeye_fetch_ohlcv(symbol, "15m", limit=target_bars + 20)
    frames["15m"] = ohlcv_to_df(raw_15m)
    if len(frames["15m"]) > target_bars:
        frames["15m"] = frames["15m"].iloc[-target_bars:]

    return frames

# =========================
# THE IMMORTAL CLOSE CHECKER (bulletproof retry)
# =========================
_last_alert_sent_at: Dict[str, float] = {}  # global dedup

def check_tf_close_confirmations(exchange, tfs: List[str]) -> None:
    log(f"About to check for candle close alerts (BULLETPROOF MODE) (TF: {', '.join(tfs)})")

    MAX_CLOSE_RETRIES = 12
    base_delay = 30

    for attempt in range(MAX_CLOSE_RETRIES):
        failed_symbols = []

        for sym in SYMBOLS:
            sym_norm = norm_symbol(sym)
            fmtp = get_price_formatter(sym)
            live_px = safe_fetch_ticker_last(exchange, sym)

            try:
                frames = fetch_symbol_frames(exchange, sym, max(LOOKBACK + 60, 80))

                for tf in tfs:
                    df = frames.get(tf)
                    if df is None or len(df) < 3:
                        continue

                    # Update watchlist with any brand new gaps
                    df_closed = df.iloc[:-1]
                    df_recent = df_closed.iloc[-(LOOKBACK + 2):]
                    detected = detect_fvgs_with_zones(df_recent, MIN_GAP)
                    if detected:
                        existing = {(g['low'], g['high'], g['timestamp']) for g in watchlists[tf][sym]}
                        for g in detected:
                            gkey = (g['type'], g['low'], g['high'], g['timestamp'])
                            if gkey not in existing:
                                watchlists[tf][sym].append(g)
                                log(f"New {g['zone_type']} zone > {sym_norm} [{fmtp(g['low'])}-{fmtp(g['high'])}] at {fmt_ts_local(g['timestamp'])}")

                    mark_mitigations(watchlists[tf][sym], df.iloc[-1])

                    last_closed_ts = df.index[-2]
                    last_closed = df.iloc[-2]
                    close_px = float(last_closed['close'])

                    any_confirmed = False
                    for gap in [g for g in only_unmitigated(watchlists[tf][sym]) if not g.get('entered')]:
                        gl, gh = float(gap['low']), float(gap['high'])
                        if gl <= close_px <= gh:
                            log(f"DEBUG {tf} {sym_norm} CLOSE IN ZONE {gl}-{gh} @ {close_px}")

                            gap['entered'] = True
                            gap['entered_on'] = last_closed_ts
                            atr_last = compute_atr_last(df_closed, period=14)
                            t = compute_zone_targets(gap, atr_last)
                            zone = gap['zone_type']
                            side = "LONG" if zone == "DISCOUNT" else "SHORT"

                            live_px_str = fmtp(live_px) if live_px else "?"
                            body = (
                                f"{tf.upper()} {sym_norm} → {zone} ZONE CONFIRMED → {side} NOW @ {fmtp(close_px)} "
                                f"[{fmtp(gl)}–{fmtp(gh)}] TP2 {fmtp(t['tp2'])} | {live_px_str}"
                            )
                            alert_key = normalize_for_sms(body)
                            now_ts = time.time()
                            last_ts = _last_alert_sent_at.get(alert_key)
                            if last_ts is None or (now_ts - last_ts) >= ALERT_DEDUP_SECONDS:
                                _last_alert_sent_at[alert_key] = now_ts
                                sms_subj = f"ALERT {sym_norm}"
                                send_email_with_retry(sms_subj, alert_key, ALERT_RECIPIENTS)
                                append_to_alerts_file(body)
                                log(f"SMS ALERT SENT -> {alert_key}")
                                if ALERT_SEND_DELAY_SECONDS > 0:
                                    time.sleep(ALERT_SEND_DELAY_SECONDS)
                            any_confirmed = True

                    if not any_confirmed:
                        log(f"{tf.upper()} {sym_norm}: no confirmed entries on this close")

                # if we made it here, symbol succeeded
                continue

            except Exception as e:
                failed_symbols.append(sym_norm)
                log(f"{sym_norm}: close-check attempt {attempt+1}/{MAX_CLOSE_RETRIES} failed: {e}")

        if not failed_symbols:
            log(f"Close check completed perfectly on attempt {attempt+1 if attempt > 0 else 'first'}")
            return

        delay = base_delay * (2 ** attempt)
        log(f"Close check attempt {attempt+1} failed for {', '.join(failed_symbols)} — fighting on in {delay}s...")
        time.sleep(delay)

    # Absolute apocalypse fallback
    critical_msg = f"CRITICAL CLOSE CHECK FAILED after {MAX_CLOSE_RETRIES} attempts at {now_local_str()}"
    log(critical_msg)
    send_email_with_retry("CRITICAL FAILURE", critical_msg, ALERT_RECIPIENTS)

# =========================
# EMAIL (raw SMTP) — unchanged
# =========================
def _smtp_connect():
    if TRANSPORT_MODE.lower() == "ssl":
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(SMTP_HOST, PORT_SSL, context=context, timeout=30)
        server.login(SENDER_EMAIL, SENDER_APP_PASS)
        return server
    else:
        server = smtplib.SMTP(SMTP_HOST, PORT_STARTTLS, timeout=30)
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(SENDER_EMAIL, SENDER_APP_PASS)
        return server

def _extract_smtp_codes(exc: Exception) -> List[int]:
    codes: List[int] = []
    if isinstance(exc, smtplib.SMTPResponseException):
        try: codes.append(int(exc.smtp_code))
        except Exception: pass
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        try:
            for _, (code, _msg) in (exc.recipients or {}).items():
                try: codes.append(int(code))
                except Exception: pass
        except Exception: pass
    elif isinstance(exc, smtplib.SMTPDataError):
        try: codes.append(int(exc.smtp_code))
        except Exception: pass
    else:
        s = str(exc)
        for token in s.replace('{', ' ').replace('}', ' ').replace('(', ' ').replace(')', ' ').split():
            if token.isdigit():
                code = int(token)
                if 400 <= code < 600:
                    codes.append(code)
    return codes

def send_email_with_retry(subject: str, body: str, to_addrs: List[str]) -> None:
    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.set_content(body)

    attempt = 0
    while True:
        try:
            server = _smtp_connect()
            try:
                server.send_message(msg)
                server.quit()
                return
            finally:
                try: server.quit()
                except Exception: pass
        except (smtplib.SMTPRecipientsRefused,
                smtplib.SMTPDataError,
                smtplib.SMTPResponseException,
                smtplib.SMTPServerDisconnected,
                smtplib.SMTPException) as e:
            codes = _extract_smtp_codes(e)
            is_transient = any(code in TRANSIENT_4XX_CODES for code in codes)
            if is_transient and attempt < EMAIL_MAX_RETRIES:
                backoff = EMAIL_BACKOFF_SECONDS[min(attempt, len(EMAIL_BACKOFF_SECONDS) - 1)]
                log(f"EMAIL transient {codes or 'unknown'} — retrying in {backoff}s")
                time.sleep(backoff)
                attempt += 1
                continue
            else:
                log(f"EMAIL ERROR (final): {e}")
                return
        except Exception as e:
            log(f"EMAIL ERROR (unexpected): {e}")
            return

# =========================
# PRICE FORMATTER
# =========================
def get_price_formatter(symbol: str) -> Callable[[float], str]:
    s = symbol.upper()
    if 'BTC' in s:   prec = 1
    elif 'ETH' in s: prec = 2
    elif 'SOL' in s: prec = 3
    else:            prec = 4

    def formatter(x) -> str:
        try:
            val = float(x)
        except Exception:
            return "?"
        if val >= 1:
            return f"{val:.{prec}f}"
        elif val >= 0.000001:
            return f"{val:.8f}".lstrip("0")
        else:
            return "0.00"
    return formatter

# =========================
# OHLCV HELPERS + TICKER
# =========================
def ohlcv_to_df(ohlcv) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    return df

def resample_ohlcv(df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    mapping = {
        "1m": "1T", "3m": "3T", "5m": "5T", "15m": "15T", "30m": "30T",
        "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
        "1d": "1d", "1w": "1W", "1M": "1MS",
    }
    rule = mapping.get(target_tf, target_tf)
    ohlc = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    return df.resample(rule, label='right', closed='right').agg(ohlc).dropna()

def safe_fetch_ticker_last(exchange, symbol: str) -> Optional[float]:
    headers = {
        "X-API-KEY": BIRDEYE_API_KEY,
        "x-chain": "solana",
        "accept": "application/json",
    }
    params = {"address": JUP_PERP_MINTS[symbol]}

    for attempt in range(2):
        try:
            resp = requests.get(
                f"{BIRDEYE_BASE}/defi/price",
                headers=headers,
                params=params,
                timeout=10,
            )
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status_code}")
            data = resp.json()
            return float(data["data"]["value"])
        except Exception as e:
            if attempt == 0:
                log(f"Ticker warning for {symbol}: {e} — retrying")
                time.sleep(1)
            else:
                log(f"Ticker error for {symbol}: {e}")
    return None

# =========================
# SIGNAL DETECTION — ICT/SMC FVGs -> ZONES
# =========================
def detect_fvgs_with_zones(df: pd.DataFrame, min_gap_percent: float = 0.05) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    if len(df) < 3:
        return gaps

    lows = df['low'].values
    highs = df['high'].values
    closes = df['close'].values
    timestamps = df.index

    for i in range(2, len(df)):
        price_here = float(closes[i])

        # Bullish FVG
        if lows[i] > highs[i-2]:
            gap_low = float(highs[i-2])
            gap_high = float(lows[i])
            gap_size = gap_high - gap_low
            if gap_size / max(price_here, 1e-12) >= min_gap_percent / 100.0:
                gaps.append({
                    'type': 'bull',
                    'fvg_type': 'bullish_fvg',
                    'zone_type': 'DISCOUNT',
                    'low': gap_low,
                    'high': gap_high,
                    'mid': (gap_low + gap_high) / 2.0,
                    'size': gap_size,
                    'timestamp': timestamps[i],
                    'entered': False,
                    'entered_on': None,
                    'mitigated': False,
                })

        # Bearish FVG
        if highs[i] < lows[i-2]:
            gap_high = float(lows[i-2])
            gap_low  = float(highs[i])
            gap_size = gap_high - gap_low
            if gap_size / max(price_here, 1e-12) >= min_gap_percent / 100.0:
                gaps.append({
                    'type': 'bear',
                    'fvg_type': 'bearish_fvg',
                    'zone_type': 'PREMIUM',
                    'low': gap_low,
                    'high': gap_high,
                    'mid': (gap_low + gap_high) / 2.0,
                    'size': gap_size,
                    'timestamp': timestamps[i],
                    'entered': False,
                    'entered_on': None,
                    'mitigated': False,
                })

    gaps.sort(key=lambda x: x['timestamp'], reverse=True)
    return gaps

def mark_mitigations(gaps: List[Dict[str, Any]], last_bar: pd.Series) -> None:
    if last_bar is None or last_bar.empty:
        return
    lb_low = float(last_bar['low'])
    lb_high = float(last_bar['high'])
    for g in gaps:
        if g.get('mitigated'):
            continue
        if g['type'] == 'bull' and lb_low < float(g['low']):
            g['mitigated'] = True
        if g['type'] == 'bear' and lb_high > float(g['high']):
            g['mitigated'] = True

def only_unmitigated(gaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [g for g in gaps if not g.get('mitigated')]

# =========================
# ATR + TARGETS
# =========================
def compute_atr_last(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if len(df) < period + 2:
        return None
    high = df['high']; low = df['low']; close = df['close']
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=period).mean()
    last = atr.dropna()
    return float(last.iloc[-1]) if not last.empty else None

def compute_sma(df: pd.DataFrame, period: int) -> pd.Series:
    return df['close'].rolling(window=period, min_periods=period).mean()

def compute_zone_targets(gap: Dict[str, Any], atr: Optional[float]) -> Dict[str, float]:
    low = float(gap['low'])
    high = float(gap['high'])
    band = max(1e-12, high - low)

    zt = gap.get('zone_type')
    side = "LONG" if zt == "DISCOUNT" else "SHORT"

    if atr is not None and atr > 0:
        if side == "LONG":
            tp1 = high + 0.5 * atr
            tp2 = high + 1.0 * atr
        else:
            tp1 = low  - 0.5 * atr
            tp2 = low  - 1.0 * atr
    else:
        if side == "LONG":
            tp1 = high + 1.0 * band
            tp2 = high + 2.0 * band
        else:
            tp1 = low  - 1.0 * band
            tp2 = low  - 2.0 * band

    buf_components = [0.10 * band]
    if atr is not None and atr > 0:
        buf_components.append(0.25 * float(atr))
    sl_buf = max(buf_components)

    sl = (low - sl_buf) if side == "LONG" else (high + sl_buf)
    return {
        "side": side,
        "entry_low": low,
        "entry_high": high,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl
    }

# =========================
# RUNTIME STATE
# =========================
last_candle_ts: Dict[str, Dict[str, Optional[pd.Timestamp]]] = {}
watchlists: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
for _tf in TIMEFRAMES:
    last_candle_ts[_tf] = {sym: None for sym in SYMBOLS}
    watchlists[_tf] = {sym: [] for sym in SYMBOLS}

# _last_alert_sent_at is defined earlier for dedup (keep using it)

# =========================
# SCHEDULING HELPERS
# =========================
def _now_for_sched() -> datetime:
    return now_local() if ALIGN_SCHEDULE_TO_LOCAL_TZ else datetime.now(timezone.utc)

def seconds_until_next_interval(every_minutes: int, at_second: int = 0,
                                from_dt: Optional[datetime] = None) -> float:
    if every_minutes <= 0:
        return 0.0
    now = from_dt or _now_for_sched()

    next_min_bucket = ((now.minute // every_minutes) + 1) * every_minutes
    next_hour = now.hour
    next_day = now.date()
    if next_min_bucket >= 60:
        next_min_bucket -= 60
        next_hour += 1
        if next_hour >= 24:
            next_hour = 0
            next_day = (now + timedelta(days=1)).date()

    target = now.replace(year=next_day.year, month=next_day.month, day=next_day.day,
                         hour=next_hour, minute=next_min_bucket, second=at_second, microsecond=0)
    if target <= now:
        target += timedelta(minutes=every_minutes)
    return max(0.0, (target - now).total_seconds())

def seconds_until_next_tf_close(from_dt: Optional[datetime] = None) -> Tuple[float, List[str], datetime]:
    now = from_dt or now_local()
    candidates: List[Tuple[datetime, str]] = []
    for tf, HM in TF_CLOSE_SCHEDULE.items():
        for h, m in HM:
            target = now.replace(hour=h, minute=m, second=CLOSE_CHECK_SECOND, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            candidates.append((target, tf))
    candidates.sort(key=lambda x: x[0])
    earliest_time = candidates[0][0]
    due_tfs = [tf for t, tf in candidates if t == earliest_time]
    secs = max(0.0, (earliest_time - now).total_seconds())
    return secs, due_tfs, earliest_time

# =========================
# ACTIONABLE LINES + SMA TREND FILTER
# =========================
def actionable_line(tf: str, sym_norm: str, last_closed: pd.Series, last_closed_ts: pd.Timestamp,
                    current_bar: pd.Series, gaps: List[Dict[str, Any]], atr_last: Optional[float],
                    fmtp: Callable[[float], str], live_px: Optional[float], df: pd.DataFrame) -> Optional[str]:
    cur_px_str = fmtp(live_px) if live_px else "?"
    candidate_gaps = only_unmitigated(gaps)

    # 1) CONFIRMED ZONE ENTRY — when a candle CLOSES inside a zone
    close_px = float(last_closed['close'])
    for g in candidate_gaps:
        if g.get('entered'):
            continue
        gl, gh = float(g['low']), float(g['high'])
        if gl <= close_px <= gh:
            zone = g['zone_type']
            side = "LONG" if zone == "DISCOUNT" else "SHORT"

            # === NEW: 50/200 SMA TREND FILTER ===
            df_closed = df.iloc[:-1]                     # only closed candles
            if len(df_closed) >= 200:
                sma_50  = compute_sma(df_closed, 50).iloc[-1]
                sma_200 = compute_sma(df_closed, 200).iloc[-1]

                if side == "LONG":
                    if not (sma_50 > sma_200 and close_px > sma_200):
                        log(f"DEBUG {tf} {sym_norm} LONG BLOCKED — price or trend bearish (50={sma_50:.2f}, 200={sma_200:.2f}, close={close_px:.2f})")
                        continue
                elif side == "SHORT":
                    if not (sma_50 < sma_200 and close_px < sma_200):
                        log(f"DEBUG {tf} {sym_norm} SHORT BLOCKED — price or trend bullish")
                        continue
            else:
                log(f"DEBUG {tf} {sym_norm} not enough data for SMA filter ({len(df_closed)} bars)")

            # If we get here → trend is good → proceed with normal confirmation
            g['entered'] = True
            g['entered_on'] = last_closed_ts
            g.pop('confirmed_alert_sent', None)
            t = compute_zone_targets(g, atr_last)

            if live_px is not None and gl <= live_px <= gh and not g.get('confirmed_alert_sent'):
                g['confirmed_alert_sent'] = True
                body = (
                    f"{tf.upper()} {sym_norm} → {zone} ZONE CONFIRMED → {side} NOW @ {fmtp(live_px)} "
                    f"[{fmtp(gl)}–{fmtp(gh)}] TP2 {fmtp(t['tp2'])} | {fmtp(live_px)}"
                )
                key = normalize_for_sms(body)
                if _last_alert_sent_at.get(key) is None or time.time() - _last_alert_sent_at[key] >= ALERT_DEDUP_SECONDS:
                    _last_alert_sent_at[key] = time.time()
                    subject = f"ALERT {sym_norm} {tf.upper()} {zone} ZONE → {side} NOW"
                    pretty_body = f"""
{now_local_str()}
{body}
Live price: {fmtp(live_px)}
                    """.strip()
                    send_email_with_retry(subject, pretty_body, ALERT_RECIPIENTS)
                    append_to_alerts_file(body)
                    log(f"REAL-TIME CONFIRMED ALERT → {body}")
                return body

    # 2) LIVE price in an unconfirmed zone
    if live_px:
        for g in candidate_gaps:
            if g.get('entered'):
                continue
            gl, gh = float(g['low']), float(g['high'])
            if gl <= live_px <= gh:
                zone = g['zone_type']
                side = "LONG" if zone == "DISCOUNT" else "SHORT"
                t = compute_zone_targets(g, atr_last)
                return (
                    f"{tf.upper()} {sym_norm} → PRICE IN {zone} ZONE → {side} SETUP ACTIVE "
                    f"[{fmtp(gl)}–{fmtp(gh)}] WAIT CLOSE | TP2 {fmtp(t['tp2'])} | {cur_px_str}"
                )

    # 3) Nearest unmitigated zone (context)
    unentered = [g for g in candidate_gaps if not g.get('entered')]
    if unentered:
        latest = unentered[0]
        zone = latest['zone_type']
        side = "LONG" if zone == "DISCOUNT" else "SHORT"
        t = compute_zone_targets(latest, atr_last)
        return (
            f"{tf.upper()} {sym_norm} → WATCH {zone} ZONE [{fmtp(latest['low'])}–{fmtp(latest['high'])}] "
            f"→ {side} on touch | TP2 {fmtp(t['tp2'])} | {cur_px_str}"
        )
    return None

# =========================
# LIVE PRICE ENTRY-ZONE TOUCH HEADS-UPS
# =========================
def check_entry_zone_price_hits(exchange, sym: str, fmtp: Callable[[float], str]) -> None:
    live_px = safe_fetch_ticker_last(exchange, sym)
    if live_px is None or live_px <= 0:
        return

    sym_norm = norm_symbol(sym)
    for tf in TIMEFRAMES:
        gaps = watchlists.get(tf, {}).get(sym, [])
        if not gaps:
            continue

        open_zones = [g for g in only_unmitigated(gaps) if not g.get('entered')]
        if not open_zones:
            continue

        for g in open_zones:
            gl, gh = float(g['low']), float(g['high'])
            if gl <= live_px <= gh:
                zone = g['zone_type']
                side = "LONG" if zone == "DISCOUNT" else "SHORT"
                t = compute_zone_targets(g, None)
                body = (f"{tf.upper()} {sym_norm} → PRICE IN {zone} ZONE → {side} SETUP ACTIVE "
                        f"[{fmtp(gl)}–{fmtp(gh)}] WAIT CLOSE | TP2 {fmtp(t['tp2'])} | {fmtp(live_px)}")
                key = normalize_for_sms(body)
                now_ts = time.time()
                last_ts = _last_alert_sent_at.get(key)
                if last_ts is not None and (now_ts - last_ts) < ALERT_DEDUP_SECONDS:
                    continue
                _last_alert_sent_at[key] = now_ts
                append_to_alerts_file(body)
                log(f"FILE ALERT -> {body}")
                if ALERT_SEND_DELAY_SECONDS > 0:
                    time.sleep(ALERT_SEND_DELAY_SECONDS)

# =========================
# CORE SCANNER
# =========================
def process_symbol(exchange, sym: str) -> None:
    if RANDOM_START_JITTER_SECONDS > 0:
        time.sleep(random.uniform(0, RANDOM_START_JITTER_SECONDS))

    sym_norm = norm_symbol(sym)
    fmtp = get_price_formatter(sym)
    log(f"Scanning {sym_norm} across {', '.join(TIMEFRAMES)}")

    live_px = safe_fetch_ticker_last(exchange, sym)

    try:
        frames = fetch_symbol_frames(exchange, sym, FETCH_LIMIT)
    except Exception as e:
        log(f"{sym_norm}: fetch frames failed: {e}")
        check_entry_zone_price_hits(exchange, sym, fmtp)
        return

    lines: List[str] = []

    for i, tf in enumerate(TIMEFRAMES):
        try:
            df = frames.get(tf)
            if df is None or len(df) < 3:
                continue

            last_closed_ts = df.index[-2]   # UTC internally
            last_closed = df.iloc[-2]
            current_bar = df.iloc[-1]       # forming candle

            # On new closed candle, detect new gaps and update watchlist
            if last_candle_ts[tf][sym] is None or last_closed_ts > last_candle_ts[tf][sym]:
                df_closed = df.iloc[:-1]
                df_recent = df_closed.iloc[-(LOOKBACK + 2):]
                detected = detect_fvgs_with_zones(df_recent, MIN_GAP)
                if detected:
                    existing = {(g['type'], g['low'], g['high'], g['timestamp']) for g in watchlists[tf][sym]}
                    for gap in detected:
                        key = (gap['type'], gap['low'], gap['high'], gap['timestamp'])
                        if key not in existing:
                            watchlists[tf][sym].append(gap)
                            log(f"New {gap['zone_type']} zone > {sym} "
                                f"[{fmtp(gap['low'])}-{fmtp(gap['high'])}] at {fmt_ts_local(gap['timestamp'])}")

            # Update mitigation status using forming bar
            mark_mitigations(watchlists[tf][sym], current_bar)

            # watermark
            last_candle_ts[tf][sym] = last_closed_ts

            atr_last = compute_atr_last(df.iloc[:-1], period=14)

            line = actionable_line(tf, sym_norm, last_closed, last_closed_ts, current_bar,
                                   watchlists[tf][sym], atr_last, fmtp, live_px, df)
            if line:
                lines.append(line)

        except Exception as e:
            lines.append(f"{tf.upper()} {sym_norm}: ERROR {e}")

        if PER_TF_DELAY_SECONDS and i < len(TIMEFRAMES) - 1:
            time.sleep(PER_TF_DELAY_SECONDS)

    # FILE ONLY for periodic scan lines
    if lines:
        for line in lines:
            append_to_alerts_file(line)
    else:
        log(f"{sym_norm}: no actionable updates; file write skipped")

    # Heads-ups (file only)
    check_entry_zone_price_hits(exchange, sym, fmtp)

# =========================
# MAIN
# =========================
def main():
    exchange = init_exchange()
    log("=== IMMEDIATE STARTUP SCAN (RUNNING NOW) ===")
    for idx, sym in enumerate(SYMBOLS):
        process_symbol(exchange, sym)
        if idx < len(SYMBOLS) - 1:
            time.sleep(SCAN_DELAY_BETWEEN_SYMBOLS_SECONDS)
    log("=== STARTUP SCAN COMPLETED ===\n")

    while True:
        now = now_local()
        secs_to_periodic = seconds_until_next_interval(RUN_EVERY_MINUTES, RUN_AT_SECOND, from_dt=now)
        secs_to_close, due_tfs, target_close_dt = seconds_until_next_tf_close(from_dt=now)
        sleep_secs = min(secs_to_periodic, secs_to_close)
        if sleep_secs > 0:
            log(f"Sleeping {int(sleep_secs)}s until next event...")
            time.sleep(sleep_secs)

        now = now_local()

        if seconds_until_next_interval(RUN_EVERY_MINUTES, RUN_AT_SECOND, from_dt=now) > (RUN_EVERY_MINUTES * 60 - 30):
            log(f"=== PERIODIC SCAN EVERY {RUN_EVERY_MINUTES}m STARTING ===")
            for idx, sym in enumerate(SYMBOLS):
                process_symbol(exchange, sym)
                if idx < len(SYMBOLS) - 1:
                    time.sleep(SCAN_DELAY_BETWEEN_SYMBOLS_SECONDS)
            log("=== PERIODIC SCAN FINISHED ===\n")

        if abs((now - target_close_dt).total_seconds()) < 30:
            _, due_tfs_now, _ = seconds_until_next_tf_close(from_dt=now - timedelta(seconds=1))
            check_tf_close_confirmations(exchange, due_tfs_now)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log("Shutting down on user interrupt — peace out")
