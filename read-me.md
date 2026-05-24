# Jupiter Perps FVG Bot

FVG trading bot for Jupiter Perps on Solana. Watches SOL, ETH, and wBTC for Fair Value Gap setups across multiple timeframes, sends alerts, and places trades on-chain automatically.

---

## Setup

You'll need three API keys, set as environment variables:

```
ANTHROPIC_API_KEY=your_key    # powers the AI trade decisions (claude-haiku)
BIRDEYE_API_KEY=your_key      # pulls candle data for SOL/ETH/WBTC
SOLANA_PRIVATE_KEY=your_key   # signs on-chain transactions (never shared)
```

Email and trading settings are configured through the web UI after deploy.

---

## Backtest — SOL 15m, February 2025

157 trades, 59 wins, 98 losses. Net +$40 on $100 collateral at 2x leverage. Win rate 37.6% with a 2:1 R:R (1% stop, 2% target).

SOL dropped hard that month ($232 down to $136) so there were a lot of losing longs early on, but the short entries caught the move and kept it green overall. Full trade log in [BACKTEST.md](BACKTEST.md).

---

## Build Specification

This document is the canonical instruction for building `jup_perps_alerts.py`. Follow it exactly. When in doubt, prefer the existing Phemex script's behavior over your own judgment.

---

## Mission

Build a single Python script that:

1. Pulls OHLCV candles for the three Jupiter Perp markets — **SOL, ETH, wBTC** — from the **Birdeye API** (the same data source that powers the charts on `jup.ag/perps`).
2. Runs the existing ICT/SMC Fair Value Gap (FVG) zone detection across multiple timeframes.
3. Sends Gmail alerts when price confirms inside a zone, with a 50/200 SMA trend filter to suppress counter-trend signals.
4. Persists zone watchlists across scans and writes a log of all activity to `alerts.txt`.

The signal logic, scheduling, email pipeline, and dedup machinery are **already proven** in the Phemex/ccxt version. They must be preserved exactly. Only the data layer changes.

---

## Source material

Two reference files exist:

1. **`melanies_immortal_phemex_btc.py`** — the working Phemex/ccxt original. This is the canonical source for every preserved function.
2. **This spec file** — describes what to change.

Both will be provided to you. **Copy from the Phemex script verbatim** for any function not explicitly modified in this spec.

---

## Deliverable

A single file:

| | |
|---|---|
| **Path** | `jup_perps_alerts.py` (project working directory) |
| **Python** | 3.10+ |
| **Run** | `python jup_perps_alerts.py` |
| **Deps** | `pandas`, `requests` |
| **Removed deps** | `ccxt` |

No `.env`, no `README`, no helper modules. One file.

---

## File header

The script must begin with this block exactly:

```python
#!/usr/bin/env python3
## Jupiter Perps FVG Alert Bot — Birdeye edition
## Pulls candles for SOL, ETH, WBTC from Birdeye (the source jup.ag/perps uses)
## Same signal logic as the Phemex BTC original. Email alerts to Gmail.
```

---

## What stays IDENTICAL to the Phemex script

Copy these verbatim. Do **not** "improve," "refactor," or "modernize" them.

### Preserved functions

- `detect_fvgs_with_zones(df, min_gap_percent)`
- `mark_mitigations(gaps, last_bar)`
- `only_unmitigated(gaps)`
- `compute_atr_last(df, period)`
- `compute_sma(df, period)`
- `compute_zone_targets(gap, atr)`
- `ohlcv_to_df(ohlcv)`
- `resample_ohlcv(df, target_tf)`
- `actionable_line(tf, sym_norm, ..., df)` — the entire SMA-filtered confirmation logic, including the `# === NEW: 50/200 SMA TREND FILTER ===` block
- `check_entry_zone_price_hits(exchange, sym, fmtp)`
- `process_symbol(exchange, sym)`
- `check_tf_close_confirmations(exchange, tfs)` — the bulletproof retry close checker
- Entire email subsystem: `_smtp_connect`, `_extract_smtp_codes`, `send_email_with_retry`
- Timezone block: `USE_FIXED_MST`, `TIMEZONE`, `ALIGN_SCHEDULE_TO_LOCAL_TZ`, `local_tz`, `now_local`, `now_local_str`, `fmt_ts_local`
- Scheduling helpers: `_now_for_sched`, `seconds_until_next_interval`, `seconds_until_next_tf_close`
- `TF_CLOSE_SCHEDULE` — the entire dict with all 96 × 15-minute tuples plus the 4h/6h/8h/12h schedules
- `main()` — the scheduler-driven loop
- `log()`, `append_to_alerts_file()`, `normalize_for_sms()`, `ALERTS_FILE`

### Preserved constants (copy values exactly)

```python
LOOKBACK = 10
MIN_GAP = 0.05
MAX_RETRIES = 5
BASE_SLEEP_S = 1
PER_TF_DELAY_SECONDS = 0
ALERT_SEND_DELAY_SECONDS = 2
RANDOM_START_JITTER_SECONDS = 5
ALERT_DEDUP_SECONDS = 15 * 60
RUN_EVERY_MINUTES = 5
RUN_AT_SECOND = 15
CLOSE_CHECK_SECOND = 15
FETCH_LIMIT = 100
```

### Preserved email config

```python
SMTP_HOST     = "smtp.gmail.com"
PORT_STARTTLS = 587
PORT_SSL      = 465
TRANSPORT_MODE = "starttls"
EMAIL_MAX_RETRIES = 4
EMAIL_BACKOFF_SECONDS = [30, 120, 300, 600]
TRANSIENT_4XX_CODES = {421, 450, 451, 452, 455, 471, 472, 473, 499}

SENDER_EMAIL     = "your_email@gmail.com"
SENDER_APP_PASS  = "your_gmail_app_password"
ALERT_RECIPIENTS = ['your_email@gmail.com']
```

### Preserved global state

```python
_last_alert_sent_at: Dict[str, float] = {}

last_candle_ts: Dict[str, Dict[str, Optional[pd.Timestamp]]] = {}
watchlists: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
for _tf in TIMEFRAMES:
    last_candle_ts[_tf] = {sym: None for sym in SYMBOLS}
    watchlists[_tf] = {sym: [] for sym in SYMBOLS}
```

(Note: this block must execute *after* the new `SYMBOLS` and `TIMEFRAMES` are defined.)

---

## What CHANGES

Three distinct changes. Everything else flows from these.

### Change 1 — Remove ccxt entirely

- Delete `import ccxt`
- Delete `EXCHANGE_ID = "phemex"`
- Delete the original `SYMBOLS = ['BTC/USDT:USDT']` line
- Delete `SCAN_DELAY_BETWEEN_SYMBOLS_SECONDS = 60` (replaced — see below)
- Delete the body of `init_exchange()` — replace with a stub:
  ```python
  def init_exchange():
      log("Birdeye data source — no exchange object to init")
      return None
  ```
- Delete `safe_fetch_ohlcv()` entirely — replaced by `birdeye_fetch_ohlcv()`
- Replace the body of `safe_fetch_ticker_last()` (see Change 3b)
- Replace the body of `fetch_symbol_frames()` (see Change 3c)

Downstream functions (`process_symbol`, `check_tf_close_confirmations`, etc.) keep their `exchange` parameter for signature compatibility. The argument is simply unused.

### Change 2 — New SYMBOLS, mint mapping, formatter, scan delay

```python
SYMBOLS = ["SOL", "ETH", "WBTC"]

JUP_PERP_MINTS = {
    "SOL":  "So11111111111111111111111111111111111111112",   # wrapped SOL
    "ETH":  "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",   # Wormhole ETH (Portal)
    "WBTC": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",   # Wormhole wBTC (Portal)
}

TIMEFRAMES = ["4h", "6h", "8h", "12h", "15m"]   # same set as the original

SCAN_DELAY_BETWEEN_SYMBOLS_SECONDS = 15   # was 60 for a single symbol; now we have 3
```

Rewrite `norm_symbol()` as a pass-through (no slashes or colons to strip):

```python
def norm_symbol(sym: str) -> str:
    return sym
```

`get_price_formatter()` keeps its existing structure but matches on the new bare strings:

- `'BTC' in s` (catches "WBTC") → 1 dp
- `'ETH' in s` → 2 dp
- `'SOL' in s` → 3 dp
- else → 4 dp

The original substring-match logic already does this correctly for "WBTC" without modification — verify by inspection.

### Change 3 — Birdeye data layer

Add to the config section, near the top:

```python
import os
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "PUT_YOUR_KEY_HERE")
BIRDEYE_BASE = "https://public-api.birdeye.so"
```

Add a `requests` import at the top with the other imports.

#### 3a. `birdeye_fetch_ohlcv(symbol, timeframe, limit)`

Drop-in replacement for `safe_fetch_ohlcv`. Returns ccxt-compatible OHLCV.

**Endpoint:** `GET https://public-api.birdeye.so/defi/v3/ohlcv`

**Headers:**
```
X-API-KEY: <BIRDEYE_API_KEY>
x-chain:   solana
accept:    application/json
```

**Query parameters:**
| Param | Value |
|---|---|
| `address` | `JUP_PERP_MINTS[symbol]` |
| `type` | Birdeye-cased timeframe (see mapping below) |
| `time_from` | `int(now) - tf_seconds * (limit + 5)` |
| `time_to` | `int(now)` |

**Timeframe mapping** (Birdeye is case-sensitive on the suffix):

```python
_BIRDEYE_TF = {
    "1m":"1m", "3m":"3m", "5m":"5m", "15m":"15m", "30m":"30m",
    "1h":"1H", "2h":"2H", "4h":"4H", "6h":"6H", "8h":"8H", "12h":"12H", "1d":"1D",
}

_TF_SECS = {
    "1m":60, "3m":180, "5m":300, "15m":900, "30m":1800,
    "1h":3600, "2h":7200, "4h":14400, "6h":21600, "8h":28800, "12h":43200, "1d":86400,
}
```

**Response shape from Birdeye:**
```json
{ "data": { "items": [
    { "unixTime": 1731000000, "o": 240.1, "h": 240.5, "l": 239.8, "c": 240.2, "v": 12345.6 },
    ...
] } }
```

**Return shape (must be ccxt-compatible — downstream depends on this exact format):**
```python
[
  [ts_ms_int, open_float, high_float, low_float, close_float, volume_float],
  ...
]
```
Where `ts_ms_int = int(item["unixTime"]) * 1000`.

**Behavior:**
- Up to 5 retries with exponential backoff starting at 1.0s (double each attempt)
- 429 status → backoff and retry
- Any other non-2xx → backoff and retry; raise on final attempt
- Truncate result to last `limit` items (Birdeye sometimes returns +1)
- Birdeye already returns oldest-first; preserve that order (matches ccxt)
- Return `[]` on total failure (do not raise from the public function — downstream uses `len(df) < 3` checks)

#### 3b. `safe_fetch_ticker_last(exchange, symbol)` rewrite

Keep the existing function signature for call-site compatibility. The `exchange` argument is unused.

**Endpoint:** `GET https://public-api.birdeye.so/defi/price`

**Headers:** same as 3a (`X-API-KEY`, `x-chain: solana`, `accept: application/json`)

**Query parameters:**
| Param | Value |
|---|---|
| `address` | `JUP_PERP_MINTS[symbol]` |

**Response shape:**
```json
{ "data": { "value": 240.18, "updateUnixTime": 1731000000, ... } }
```

**Return:** `float(response_json["data"]["value"])` on success, or `None` on any failure (matches the Phemex version's `Optional[float]` contract — it must not raise).

Use a single 10-second timeout with one retry on transient errors. Log a single warning line on failure, mirroring the original.

#### 3c. `fetch_symbol_frames(exchange, symbol, target_bars)` rewrite

Preserve the **resample-from-1h** pattern (keeps API calls low) and fetch 15m natively (resampling 1h to 15m would lose granularity):

```python
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
```

---

## File layout (top to bottom)

```
1. Shebang + comment block
2. Imports (time, random, ssl, os, requests, pandas, etc. — NO ccxt)
3. Timezone configuration block
4. CONFIGURATION section:
   - BIRDEYE_API_KEY, BIRDEYE_BASE
   - SYMBOLS, JUP_PERP_MINTS, TIMEFRAMES
   - LOOKBACK, MIN_GAP, retry/jitter constants
   - SCAN_DELAY_BETWEEN_SYMBOLS_SECONDS = 15
   - TF_CLOSE_SCHEDULE (full)
   - Email constants + recipients
5. UTILS (log, norm_symbol, normalize_for_sms, ALERTS_FILE, append_to_alerts_file)
6. init_exchange() stub
7. Birdeye client:
   - _BIRDEYE_TF, _TF_SECS
   - birdeye_fetch_ohlcv()
8. fetch_symbol_frames() — uses birdeye_fetch_ohlcv
9. check_tf_close_confirmations() — copied verbatim, exchange arg unused
10. Email subsystem — copied verbatim
11. get_price_formatter — copied verbatim (substring match handles WBTC correctly)
12. OHLCV helpers (ohlcv_to_df, resample_ohlcv) — copied verbatim
13. safe_fetch_ticker_last() — rewritten with Birdeye /defi/price
14. Signal detection (detect_fvgs_with_zones, mark_mitigations, only_unmitigated)
15. ATR + targets (compute_atr_last, compute_sma, compute_zone_targets)
16. Runtime state initialization (last_candle_ts, watchlists)
17. Scheduling helpers (seconds_until_next_interval, seconds_until_next_tf_close)
18. actionable_line() — copied verbatim including SMA filter
19. check_entry_zone_price_hits() — copied verbatim
20. process_symbol() — copied verbatim
21. main() — copied verbatim
22. if __name__ == '__main__' block — copied verbatim
```

---

## Validation steps

After writing the file, execute these in order. Do not proceed to the next step until the current one passes.

### Step 1 — Syntax check
```
python -m py_compile jup_perps_alerts.py
```
Expected: exit code 0, no output.

### Step 2 — Birdeye smoke test
Add a temporary block at the very bottom of the file (above `if __name__ == '__main__'`):

```python
def _smoke_test():
    candles = birdeye_fetch_ohlcv("SOL", "15m", limit=10)
    print(f"Got {len(candles)} candles")
    for c in candles[-3:]:
        print(c)
    print("Live price:", safe_fetch_ticker_last(None, "SOL"))
```

Run:
```
BIRDEYE_API_KEY=<your_key> python -c "from jup_perps_alerts import _smoke_test; _smoke_test()"
```

Expected: 10 rows, sensible SOL price values (roughly $50–$500 range depending on market), and a non-`None` live price. **Remove the `_smoke_test` block before final submission.**

### Step 3 — Single-symbol dry run
Temporarily edit:
```python
SYMBOLS = ["SOL"]
ALERT_RECIPIENTS = ['your_email@gmail.com']   # drop SMS alias for testing
```
Run `python jup_perps_alerts.py`. Watch logs for one full periodic-scan cycle (≤5 minutes). Expect:

- "Connected" / startup log
- "Scanning SOL across 4h, 6h, 8h, 12h, 15m"
- Zero exceptions
- `alerts.txt` created and written to

Revert SYMBOLS and ALERT_RECIPIENTS after passing.

### Step 4 — Three-symbol run
Set `SYMBOLS = ["SOL", "ETH", "WBTC"]`. Run for ≥30 minutes. Verify:

- All three symbols scanned each cycle
- No `429` errors in stdout
- `alerts.txt` accumulating lines

---

## Gotchas — DO NOT do these

- ❌ Do NOT label Birdeye volume as "Jupiter Perps volume" anywhere. It's on-chain DEX volume for the token mint. The signal logic doesn't use volume, so don't introduce it into alert text either.
- ❌ Do NOT strip slashes or colons in `norm_symbol`. Make it a pass-through.
- ❌ Do NOT add wallet code, Solana RPC, transaction signing, or any trading logic. This is read-only.
- ❌ Do NOT change SMTP code, recipients, or alert message formatting. The user has Gmail filters tuned to the exact format.
- ❌ Do NOT add threading, asyncio, multiprocessing, or job queues. The original is intentionally synchronous and single-threaded.
- ❌ Do NOT add a `.env` loader, config file parser, or secrets vault. Config is inline, matching the original.
- ❌ Do NOT add type-checking, dataclass migration, or pydantic models. Match the original's dict-based style.
- ❌ Do NOT import any AI/LLM library (anthropic, openai, etc.).
- ❌ Do NOT split the script across multiple files. One file.
- ❌ Do NOT delete `check_tf_close_confirmations()` — even though much of its body uses no Birdeye-specific code, it must remain because `main()` calls it on TF boundaries.

---

## Symbol display in alerts

The alert text format from the original is:
```
{TF} {SYM_NORM} → {ZONE} ZONE CONFIRMED → {SIDE} NOW @ {PRICE} [{LOW}–{HIGH}] TP2 {TP2} | {LIVE_PRICE}
```

With the new symbols, this becomes:
```
4H SOL → DISCOUNT ZONE CONFIRMED → LONG NOW @ 245.32 [244.10–246.50] TP2 252.80 | 245.32
4H WBTC → PREMIUM ZONE CONFIRMED → SHORT NOW @ 95430.5 [95800.0–96200.0] TP2 94100.0 | 95430.5
```

This is achieved automatically by the pass-through `norm_symbol` plus the existing `get_price_formatter` logic. No changes to alert text formatting are needed.

---

## When complete

Deliver exactly one file: `jup_perps_alerts.py`. No extras. The user will copy it to their Alberta-timezone server and run it directly with their Birdeye API key set via environment variable or pasted into the constant.

If anything in this spec is ambiguous, **default to the Phemex script's behavior** — that script is in production and working.