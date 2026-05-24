#!/usr/bin/env python3
"""
Risk engine + Jupiter Perps execution stub.

Risk rules (per poll cycle):
  - Per-pair daily loss must not exceed config["pairs"][sym]["max_daily_loss_usd"]
  - Total daily loss must not exceed config["risk"]["max_daily_loss_total_usd"]
  - Concurrent open positions must not exceed config["risk"]["max_concurrent_positions"]
  - Pending open requests must not exceed config["risk"]["max_pending_orders"]
  - Per-pair concurrent positions must not exceed config["pairs"][sym]["max_positions"]
  - Optional one-trade-per-symbol and opposite-side blocking are enforced in config["risk"]

SOLANA_PRIVATE_KEY is read only from environment — never from config.json or logs.
"""

import os, json, time, threading
from datetime import date
from typing import Optional
import jup_perps_exec

CONFIG_FILE = "config.json"
STATE_FILE  = "trade_state.json"

_today           = date.today()
_daily_loss: dict  = {}   # {sym: usd_lost_today}
_total_loss: float = 0.0
_open_positions: dict = {}  # {sym: count}
_state_lock = threading.RLock()
_trade_state: dict = {
    "open_positions": [],
    "pending_orders": [],
    "last_trade_by_symbol": {},
    "ignored_signals": [],
}


def _reset_if_new_day():
    global _today, _daily_loss, _total_loss, _open_positions
    today = date.today()
    if today != _today:
        _today           = today
        _daily_loss      = {}
        _total_loss      = 0.0
        _open_positions  = {}
        print(f"[risk] Daily counters reset for {today}")


def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def _load_trade_state() -> dict:
    global _trade_state, _open_positions
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
    _trade_state = {
        "open_positions": data.get("open_positions", []),
        "pending_orders": data.get("pending_orders", []),
        "last_trade_by_symbol": data.get("last_trade_by_symbol", {}),
        "ignored_signals": data.get("ignored_signals", [])[-100:],
    }
    _open_positions = _count_by_symbol(_trade_state["open_positions"])
    return _trade_state


def _save_trade_state():
    with open(STATE_FILE, "w") as f:
        json.dump(_trade_state, f, indent=2)


def _count_by_symbol(rows: list[dict]) -> dict:
    counts = {}
    for row in rows:
        sym = row.get("symbol")
        if sym:
            counts[sym] = counts.get(sym, 0) + 1
    return counts


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _cooldown_left(last_ts: float, minutes: float) -> int:
    if not last_ts or minutes <= 0:
        return 0
    elapsed = time.time() - float(last_ts)
    remaining = int(minutes * 60 - elapsed)
    return max(0, remaining)


def _active_positions() -> list[dict]:
    return list(_trade_state.get("open_positions", []))


def _pending_orders() -> list[dict]:
    return list(_trade_state.get("pending_orders", []))


def _position_key(row: dict) -> tuple[str, str]:
    return row.get("symbol", ""), row.get("side", "")


def _wallet_public_key(cfg: dict) -> Optional[str]:
    pub = cfg.get("wallet", {}).get("public_key") or ""
    if pub:
        return pub
    return jup_perps_exec.public_key_from_env()


def reconcile_positions(config: Optional[dict] = None) -> dict:
    """
    Rebuild local open-position state from confirmed Jupiter position accounts.
    Recent pending requests are preserved briefly because keeper execution is async.
    """
    global _open_positions
    with _state_lock:
        _reset_if_new_day()
        _load_trade_state()
        cfg = config or load_config()
        owner = _wallet_public_key(cfg)
        risk_cfg = cfg.get("risk", {})
        timeout = float(risk_cfg.get("pending_order_timeout_seconds", 180))

        if not owner:
            return {"ok": False, "msg": "wallet public key not configured", "synced": False}

        old_open = {_position_key(row): row for row in _trade_state.get("open_positions", [])}
        old_pending = {_position_key(row): row for row in _trade_state.get("pending_orders", [])}
        chain_open = []
        errors = []

        for sym in cfg.get("pairs", {}).keys() or ["SOL", "ETH", "WBTC"]:
            for side in ("LONG", "SHORT"):
                pos = jup_perps_exec.get_position(sym, side, owner)
                if pos.get("exists"):
                    prior = old_open.get((sym, side), old_pending.get((sym, side), {}))
                    chain_open.append({
                        **prior,
                        "symbol": sym,
                        "side": side,
                        "address": pos.get("address"),
                        "data_len": pos.get("data_len"),
                        "size_usd": pos.get("size_usd"),
                        "opened_at": prior.get("opened_at") or _now(),
                        "opened_ts": prior.get("opened_ts") or time.time(),
                        "status": "open" if prior.get("status") != "pending_tpsl" else "pending_tpsl",
                        "source": prior.get("source") or "chain_reconcile",
                        "last_seen_at": _now(),
                    })
                elif pos.get("msg"):
                    errors.append(f"{sym} {side}: {pos.get('msg')}")

        if errors:
            return {
                "ok": False,
                "msg": f"skipped reconcile due to {len(errors)} lookup errors",
                "synced": False,
                "errors": errors,
            }

        chain_keys = {_position_key(row) for row in chain_open}
        now_ts = time.time()
        pending_kept = []
        expired_pending = []
        for row in _trade_state.get("pending_orders", []):
            key = _position_key(row)
            if key in chain_keys:
                continue
            age = now_ts - float(row.get("requested_ts") or now_ts)
            if age <= timeout:
                pending_kept.append(row)
            else:
                row = {**row, "status": "expired_pending", "expired_at": _now()}
                expired_pending.append(row)

        for row in old_open.values():
            key = _position_key(row)
            if key not in chain_keys:
                closed = {
                    **row,
                    "status": "closed_or_missing_on_chain",
                    "closed_at": _now(),
                    "closed_ts": now_ts,
                }
                if row.get("symbol"):
                    _trade_state["last_trade_by_symbol"][row["symbol"]] = closed

        for row in expired_pending:
            sym = row.get("symbol")
            if sym:
                _trade_state["last_trade_by_symbol"][sym] = row

        for row in chain_open:
            _trade_state["last_trade_by_symbol"][row["symbol"]] = row

        _trade_state["open_positions"] = chain_open
        _trade_state["pending_orders"] = pending_kept
        _save_trade_state()

        _open_positions = _count_by_symbol(chain_open)

        msg = f"synced {len(chain_open)} open, kept {len(pending_kept)} pending"
        if expired_pending:
            msg += f", expired {len(expired_pending)} pending"
        if errors:
            msg += f", {len(errors)} lookup errors"
        return {
            "ok": not errors,
            "msg": msg,
            "synced": True,
            "open_count": len(chain_open),
            "pending_count": len(pending_kept),
            "expired_pending_count": len(expired_pending),
            "errors": errors,
        }


def trade_slot_status(config: Optional[dict] = None) -> dict:
    """
    Returns the current trade-capacity state used by risk checks and AI prompting.
    """
    _reset_if_new_day()
    _load_trade_state()
    cfg = config or load_config()
    risk_cfg = cfg.get("risk", {})
    open_rows = _active_positions()
    pending_rows = _pending_orders()
    open_count = len(open_rows)
    pending_count = len(pending_rows)
    max_open = int(risk_cfg.get("max_concurrent_positions", 1))
    max_pending = int(risk_cfg.get("max_pending_orders", 1))
    slots_used = open_count + pending_count
    slots_available = max(0, max_open - slots_used)
    pending_available = max(0, max_pending - pending_count)
    full = slots_available <= 0 or pending_available <= 0
    reason = "trade slot available"
    if slots_available <= 0:
        reason = f"max concurrent positions reached ({slots_used}/{max_open})"
    elif pending_available <= 0:
        reason = f"max pending orders reached ({pending_count}/{max_pending})"
    return {
        "open_count": open_count,
        "pending_count": pending_count,
        "max_concurrent_positions": max_open,
        "max_pending_orders": max_pending,
        "slots_available": slots_available,
        "pending_available": pending_available,
        "full": full,
        "reason": reason,
        "open_positions": open_rows,
        "pending_orders": pending_rows,
        "ignore_signals_when_full": bool(risk_cfg.get("ignore_signals_when_full", True)),
    }


def trade_slot_available(config: Optional[dict] = None) -> tuple[bool, str]:
    status = trade_slot_status(config)
    return not status["full"], status["reason"]


def record_ignored_signal(reason: str, signal: Optional[dict] = None):
    _load_trade_state()
    _trade_state["ignored_signals"].append({
        "ts": _now(),
        "reason": reason,
        "signal": signal or {},
    })
    _trade_state["ignored_signals"] = _trade_state["ignored_signals"][-100:]
    _save_trade_state()


def check_risk(sym: str, config: Optional[dict] = None,
               action: str = "") -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    True = trade is permitted under current risk limits.
    """
    _reset_if_new_day()
    _load_trade_state()
    cfg = config or load_config()

    pair_cfg  = cfg.get("pairs", {}).get(sym, {})
    risk_cfg  = cfg.get("risk", {})

    if not pair_cfg.get("enabled", True):
        return False, f"{sym} trading disabled in config"

    if not cfg.get("wallet", {}).get("auto_trade", False):
        return False, "auto_trade is off"

    pair_loss   = _daily_loss.get(sym, 0.0)
    pair_limit  = pair_cfg.get("max_daily_loss_usd", 100.0)
    if pair_loss >= pair_limit:
        return False, f"{sym} daily loss limit hit (${pair_loss:.2f} / ${pair_limit:.2f})"

    if _total_loss >= risk_cfg.get("max_daily_loss_total_usd", 250.0):
        return False, f"Total daily loss limit hit (${_total_loss:.2f})"

    status = trade_slot_status(cfg)
    if status["full"]:
        return False, status["reason"]

    if risk_cfg.get("one_trade_per_symbol", True):
        symbol_active = any(p.get("symbol") == sym for p in _active_positions() + _pending_orders())
        if symbol_active:
            return False, f"{sym} already has an open or pending trade"

    if action and not risk_cfg.get("allow_opposite_side_while_open", False):
        opposite = "SHORT" if action == "LONG" else "LONG"
        opposite_active = any(
            p.get("symbol") == sym and p.get("side") == opposite
            for p in _active_positions() + _pending_orders()
        )
        if opposite_active:
            return False, f"{sym} already has an opposite-side {opposite} trade"

    last_trade = _trade_state.get("last_trade_by_symbol", {}).get(sym, {})
    open_cd = _cooldown_left(last_trade.get("opened_ts", 0), risk_cfg.get("cooldown_after_open_minutes", 30))
    close_cd = _cooldown_left(last_trade.get("closed_ts", 0), risk_cfg.get("cooldown_after_close_minutes", 15))
    cd_left = max(open_cd, close_cd)
    if cd_left > 0:
        return False, f"{sym} cooldown active ({cd_left // 60 + 1} min remaining)"

    pair_pos    = _open_positions.get(sym, 0)
    max_pos     = pair_cfg.get("max_positions", 1)
    if pair_pos >= max_pos:
        return False, f"{sym} max concurrent positions reached ({pair_pos})"

    return True, "ok"


def record_trade_pending(action: str, sym: str, entry: float, stop: float, target: float):
    _reset_if_new_day()
    _load_trade_state()
    row = {
        "symbol": sym,
        "side": action,
        "entry_price": entry,
        "stop_loss": stop,
        "target": target,
        "requested_at": _now(),
        "requested_ts": time.time(),
        "status": "pending_open",
    }
    _trade_state["pending_orders"].append(row)
    _save_trade_state()
    return row


def clear_pending_trade(sym: str, action: str):
    _load_trade_state()
    pending = _trade_state.get("pending_orders", [])
    for i, row in enumerate(pending):
        if row.get("symbol") == sym and row.get("side") == action:
            pending.pop(i)
            break
    _save_trade_state()


def mark_pending_trade_submitted(sym: str, action: str, tx: str):
    _load_trade_state()
    for row in _trade_state.get("pending_orders", []):
        if row.get("symbol") == sym and row.get("side") == action:
            row["tx"] = tx
            row["submitted_at"] = _now()
            row["status"] = "pending_open"
            break
    _save_trade_state()


def _wait_for_position(sym: str, action: str, owner: str,
                       timeout_seconds: float = 30,
                       poll_seconds: float = 2) -> Optional[dict]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        pos = jup_perps_exec.get_position(sym, action, owner)
        if pos.get("exists") and float(pos.get("size_usd") or 0) > 0:
            return pos
        time.sleep(poll_seconds)
    return None


def record_trade_open(sym: str, action: str = "", entry: float = 0,
                      stop: float = 0, target: float = 0, tx: str = ""):
    _reset_if_new_day()
    _load_trade_state()
    clear_pending_trade(sym, action)
    row = {
        "symbol": sym,
        "side": action,
        "entry_price": entry,
        "stop_loss": stop,
        "target": target,
        "opened_at": _now(),
        "opened_ts": time.time(),
        "tx": tx,
        "status": "open",
    }
    _trade_state["open_positions"].append(row)
    _trade_state["last_trade_by_symbol"][sym] = row
    _save_trade_state()
    _open_positions[sym] = _open_positions.get(sym, 0) + 1


def ensure_exit_orders(config: Optional[dict] = None) -> dict:
    """
    Submit missing on-chain TP/SL trigger requests for reconciled open positions.
    Jupiter TP/SL requests can only be created against an existing position, so
    this runs after chain reconciliation rather than inside the open transaction.
    """
    with _state_lock:
        _load_trade_state()
        cfg = config or load_config()
        if not cfg.get("wallet", {}).get("auto_trade", False):
            return {"ok": True, "submitted": 0, "errors": [], "msg": "auto_trade is off"}

        submitted = 0
        errors = []
        changed = False
        for row in _trade_state.get("open_positions", []):
            if (row.get("take_profit_tx") and row.get("stop_loss_tx")) or row.get("status") == "pending_tpsl":
                continue
            sym = row.get("symbol")
            side = row.get("side")
            stop = float(row.get("stop_loss") or 0)
            target = float(row.get("target") or 0)
            if not sym or side not in ("LONG", "SHORT") or stop <= 0 or target <= 0:
                continue

            row["status"] = "pending_tpsl"
            row["tpsl_requested_at"] = _now()
            changed = True
            _save_trade_state()

            result = jup_perps_exec.create_tpsl_requests(sym, side, stop, target)
            if result.get("ok") or result.get("partial"):
                row["status"] = "open"
                row["take_profit_tx"] = result.get("take_profit_tx")
                row["stop_loss_tx"] = result.get("stop_loss_tx")
                if result.get("take_profit_tx") and result.get("stop_loss_tx"):
                    row["tpsl_tx"] = result.get("tx")
                row["tpsl_set_at"] = _now()
                submitted += 1
                if result.get("errors"):
                    row["tpsl_error"] = result.get("errors")
                    row["tpsl_error_at"] = _now()
                print(f"[trade] {result.get('msg')}: {result.get('tx')}")
            else:
                row["status"] = "open"
                row["tpsl_error"] = result.get("errors") or result.get("msg")
                row["tpsl_error_at"] = _now()
                errors.append(f"{sym} {side}: {result.get('msg')}")
            changed = True

        if changed:
            _save_trade_state()
        return {
            "ok": not errors,
            "submitted": submitted,
            "errors": errors,
            "msg": f"submitted {submitted} TP/SL request set(s), errors {len(errors)}",
        }


def record_trade_close(sym: str, pnl_usd: float):
    """Call when a position closes. pnl_usd is negative for a loss."""
    _reset_if_new_day()
    _load_trade_state()
    global _total_loss
    closed = None
    for i, row in enumerate(_trade_state.get("open_positions", [])):
        if row.get("symbol") == sym:
            closed = _trade_state["open_positions"].pop(i)
            break
    if closed:
        closed["closed_at"] = _now()
        closed["closed_ts"] = time.time()
        closed["pnl_usd"] = pnl_usd
        closed["status"] = "closed"
        _trade_state["last_trade_by_symbol"][sym] = closed
    _save_trade_state()
    _open_positions.update(_count_by_symbol(_trade_state["open_positions"]))
    if sym not in _open_positions:
        _open_positions[sym] = 0
    if pnl_usd < 0:
        loss = abs(pnl_usd)
        _daily_loss[sym]  = _daily_loss.get(sym, 0.0) + loss
        _total_loss      += loss
        print(f"[risk] {sym} loss recorded: ${loss:.2f}  |  pair today: ${_daily_loss[sym]:.2f}  |  total: ${_total_loss:.2f}")


def daily_stats() -> dict:
    _reset_if_new_day()
    _load_trade_state()
    cfg           = load_config()
    pair_limits   = {sym: cfg.get("pairs", {}).get(sym, {}).get("max_daily_loss_usd", 100)
                     for sym in ["SOL", "ETH", "WBTC"]}
    total_limit   = cfg.get("risk", {}).get("max_daily_loss_total_usd", 250)
    slot_status   = trade_slot_status(cfg)
    return {
        "date":           str(_today),
        "pair_loss":      {sym: round(_daily_loss.get(sym, 0.0), 2) for sym in ["SOL", "ETH", "WBTC"]},
        "pair_limits":    pair_limits,
        "total_loss":     round(_total_loss, 2),
        "total_limit":    total_limit,
        "open_positions": dict(_open_positions),
        "trade_slots":    slot_status,
    }


def execute_trade(action: str, sym: str, entry: float, stop: float,
                  target: float, config: Optional[dict] = None) -> dict:
    """
    Open a position on Jupiter Perps via on-chain transaction.
    Returns {"ok": bool, "msg": str, "tx": str|None}
    """
    cfg      = config or load_config()
    pk       = os.environ.get("SOLANA_PRIVATE_KEY", "")

    if not pk:
        return {"ok": False, "msg": "SOLANA_PRIVATE_KEY not set in environment", "tx": None}

    pair_cfg = cfg.get("pairs", {}).get(sym, {})
    print(f"[trade] Opening {action} {sym}  collateral=${pair_cfg.get('entry_usd', 50)}  lev={pair_cfg.get('leverage', 2)}x  entry≈{entry}")
    with _state_lock:
        allowed, reason = check_risk(sym, cfg, action)
        if not allowed:
            return {"ok": False, "msg": f"Risk check blocked: {reason}", "tx": None}
        record_trade_pending(action, sym, entry, stop, target)

    result = jup_perps_exec.open_position(
        sym           = sym,
        side          = action,
        collateral_usd = float(pair_cfg.get("entry_usd",  50)),
        leverage       = float(pair_cfg.get("leverage",    2)),
        current_price  = entry,
    )
    if result["ok"]:
        mark_pending_trade_submitted(sym, action, result.get("tx") or "")
        owner = _wallet_public_key(cfg)
        pos = _wait_for_position(sym, action, owner) if owner else None
        if not pos:
            return {
                "ok": False,
                "pending": True,
                "tx": result.get("tx"),
                "msg": f"Open request submitted but {action} {sym} is not open yet; TP/SL not locked",
            }
        record_trade_open(sym, action, entry, stop, target, result.get("tx") or "")
        exit_result = ensure_exit_orders(cfg)
        slots = trade_slot_status(cfg)
        row = next(
            (p for p in slots.get("open_positions", [])
             if p.get("symbol") == sym and p.get("side") == action),
            {},
        )
        if row.get("take_profit_tx") and row.get("stop_loss_tx"):
            return {
                "ok": True,
                "tx": result.get("tx"),
                "take_profit_tx": row.get("take_profit_tx"),
                "stop_loss_tx": row.get("stop_loss_tx"),
                "msg": f"Position opened and TP/SL locked ({action} {sym})",
            }
        return {
            "ok": False,
            "unprotected": True,
            "tx": result.get("tx"),
            "take_profit_tx": row.get("take_profit_tx"),
            "stop_loss_tx": row.get("stop_loss_tx"),
            "msg": f"Position opened but TP/SL not fully locked: {exit_result.get('msg')}",
        }
    else:
        clear_pending_trade(sym, action)
    return result


_load_trade_state()
