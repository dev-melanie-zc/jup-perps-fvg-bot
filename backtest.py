#!/usr/bin/env python3
"""
Backtest of the FVG strategy from agent.py / FVG_zone_tracker-main/server.py.

Strategy:
  - Detect bull/bear FVGs using the 3-candle pattern (candle[i].low > candle[i-2].high, etc.)
  - Min gap size: 0.05% of price (MIN_GAP from server.py)
  - Enter LONG on bull FVG / SHORT on bear FVG when price retraces to zone midpoint
  - Entry = zone midpoint, Stop = 1% from entry, Target = 2% from entry (from agent.py)
  - One trade at a time; zone marked filled on first touch

CSV columns expected: datetime, open, high, low, close, volume
"""

import pandas as pd

CSV_FILE        = "SOL-15m-Feb2025.csv"
MIN_GAP_PCT     = 0.05   # % — matches server.py MIN_GAP
STOP_LOSS_PCT   = 0.01   # 1% — matches agent.py STOP_LOSS_PCT
TAKE_PROFIT_PCT = 0.02   # 2% — matches agent.py TAKE_PROFIT_PCT
COLLATERAL_USD  = 100    # $ per trade
LEVERAGE        = 2      # matches config.json default


def load_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df


def detect_fvg(df: pd.DataFrame, i: int) -> list:
    """Return new FVG zones formed at candle i (3-candle pattern: i-2, i-1, i)."""
    if i < 2:
        return []
    prev2  = df.iloc[i - 2]
    curr   = df.iloc[i]
    price  = curr["close"]
    fvgs   = []

    # Bullish FVG: candle[i].low > candle[i-2].high
    if curr["low"] > prev2["high"]:
        gl, gh = float(prev2["high"]), float(curr["low"])
        if (gh - gl) / max(price, 1e-12) >= MIN_GAP_PCT / 100:
            fvgs.append({"type": "bull", "low": gl, "high": gh, "mid": (gl + gh) / 2,
                         "formed_i": i, "filled": False})

    # Bearish FVG: candle[i].high < candle[i-2].low
    if curr["high"] < prev2["low"]:
        gh, gl = float(prev2["low"]), float(curr["high"])
        if (gh - gl) / max(price, 1e-12) >= MIN_GAP_PCT / 100:
            fvgs.append({"type": "bear", "low": gl, "high": gh, "mid": (gl + gh) / 2,
                         "formed_i": i, "filled": False})

    return fvgs


def run_backtest(df: pd.DataFrame) -> list:
    open_zones: list = []
    trade:      dict | None = None
    trades:     list = []

    for i in range(len(df)):
        row = df.iloc[i]
        h, l, ts = float(row["high"]), float(row["low"]), row["datetime"]

        # ── 1. Manage open trade (SL / TP) ───────────────────────────────────
        if trade is not None:
            side, entry, stop, target = (
                trade["side"], trade["entry"], trade["stop"], trade["target"]
            )

            if side == "LONG":
                hit_sl = l <= stop
                hit_tp = h >= target
            else:
                hit_sl = h >= stop
                hit_tp = l <= target

            if hit_sl or hit_tp:
                if hit_sl and hit_tp:
                    result, exit_price = "STOP", stop   # conservative: assume SL hit first
                elif hit_sl:
                    result, exit_price = "STOP", stop
                else:
                    result, exit_price = "TARGET", target

                pnl_pct = (
                    (exit_price - entry) / entry
                    if side == "LONG"
                    else (entry - exit_price) / entry
                )
                trade.update({"exit_price": exit_price, "exit_ts": ts,
                               "pnl_pct": pnl_pct, "result": result})
                trades.append(trade)
                trade = None

        # ── 2. Detect new FVGs formed at this candle ──────────────────────────
        open_zones.extend(detect_fvg(df, i))

        # ── 3. Entry check — newest zones first, before marking filled ────────
        if trade is None:
            candidates = sorted(
                [z for z in open_zones if not z["filled"] and i > z["formed_i"]],
                key=lambda z: z["formed_i"],
                reverse=True,   # prefer most recently formed zone
            )
            for z in candidates:
                mid = z["mid"]
                entering = False

                if z["type"] == "bull" and l <= mid:
                    # Price pulled back to at least the zone midpoint → LONG
                    side   = "LONG"
                    stop   = mid * (1 - STOP_LOSS_PCT)
                    target = mid * (1 + TAKE_PROFIT_PCT)
                    entering = True

                elif z["type"] == "bear" and h >= mid:
                    # Price rallied back to at least the zone midpoint → SHORT
                    side   = "SHORT"
                    stop   = mid * (1 + STOP_LOSS_PCT)
                    target = mid * (1 - TAKE_PROFIT_PCT)
                    entering = True

                if entering:
                    trade = {
                        "side": side, "entry": mid, "stop": stop, "target": target,
                        "entry_ts": ts,
                        "zone_type": z["type"], "zone_low": z["low"],
                        "zone_high": z["high"], "zone_formed_i": z["formed_i"],
                    }
                    z["filled"] = True
                    break

        # ── 4. Mark remaining zones filled if this candle touches them ────────
        # Skip zones formed at this exact candle — they can't self-fill at birth
        # (bull FVG: candle[i].low == zone_high, would always trigger otherwise)
        for z in open_zones:
            if not z["filled"] and z["formed_i"] < i and l <= z["high"] and h >= z["low"]:
                z["filled"] = True

        # ── 5. Prune filled / old zones ───────────────────────────────────────
        open_zones = [z for z in open_zones if not z["filled"]]

    # Force-close any trade open at end of data
    if trade is not None:
        last       = df.iloc[-1]
        exit_price = float(last["close"])
        entry      = trade["entry"]
        pnl_pct    = (
            (exit_price - entry) / entry
            if trade["side"] == "LONG"
            else (entry - exit_price) / entry
        )
        trade.update({"exit_price": exit_price, "exit_ts": last["datetime"],
                       "pnl_pct": pnl_pct, "result": "OPEN_AT_END"})
        trades.append(trade)

    return trades


def print_results(trades: list):
    if not trades:
        print("No trades taken.")
        return

    df_t = pd.DataFrame(trades)
    position_usd = COLLATERAL_USD * LEVERAGE
    df_t["pnl_usd"] = df_t["pnl_pct"] * position_usd

    wins   = df_t[df_t["result"] == "TARGET"]
    stops  = df_t[df_t["result"] == "STOP"]
    open_  = df_t[df_t["result"] == "OPEN_AT_END"]
    total  = len(df_t)
    closed = total - len(open_)
    win_rate = len(wins) / closed * 100 if closed else 0
    total_pnl = df_t["pnl_usd"].sum()

    # Equity curve (cumulative PnL)
    df_t["cum_pnl"] = df_t["pnl_usd"].cumsum()
    max_dd = (df_t["cum_pnl"].cummax() - df_t["cum_pnl"]).max()

    print(f"\n{'='*60}")
    print(f"  SOL 15m FVG Strategy — Feb 2025 Backtest")
    print(f"  Collateral: ${COLLATERAL_USD}  Leverage: {LEVERAGE}x  "
          f"Position: ${position_usd}")
    print(f"{'='*60}")
    print(f"  Total trades     : {total}")
    print(f"  Wins  (TP hit)   : {len(wins)}")
    print(f"  Losses (SL hit)  : {len(stops)}")
    print(f"  Open at end      : {len(open_)}")
    print(f"  Win rate         : {win_rate:.1f}%  (closed trades only)")
    print(f"  Total PnL        : ${total_pnl:+.2f}")
    print(f"  Avg PnL / trade  : ${df_t['pnl_usd'].mean():+.2f}")
    print(f"  Best trade       : ${df_t['pnl_usd'].max():+.2f}")
    print(f"  Worst trade      : ${df_t['pnl_usd'].min():+.2f}")
    print(f"  Max drawdown     : ${max_dd:.2f}")
    print(f"\n  {'Entry Time':<20} {'Side':<6} {'Entry':>8} {'Exit':>8} "
          f"{'PnL%':>7} {'PnL$':>8}  Result")
    print(f"  {'-'*72}")
    for _, t in df_t.iterrows():
        print(
            f"  {str(t['entry_ts'])[:19]:<20} {t['side']:<6} "
            f"{t['entry']:>8.3f} {t['exit_price']:>8.3f} "
            f"{t['pnl_pct']*100:>+6.2f}% {t['pnl_usd']:>+8.2f}  {t['result']}"
        )
    print(f"{'='*60}\n")


if __name__ == "__main__":
    print(f"Loading {CSV_FILE}…")
    df = load_data(CSV_FILE)
    print(f"  {len(df)} candles  {df['datetime'].iloc[0]}  →  {df['datetime'].iloc[-1]}")

    trades = run_backtest(df)
    print_results(trades)
