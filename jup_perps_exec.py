#!/usr/bin/env python3
"""
Jupiter Perps on-chain trade execution via Helius RPC.

Protocol flow (current Jupiter Perps v2):
  open  → createIncreasePositionMarketRequest  (we sign + send)
            → keeper executes it within ~2s → position opens
  TP/SL → createDecreasePositionRequest2       (trigger requests)
            → keeper executes when oracle crosses trigger price
  close → createDecreasePositionMarketRequest
            → keeper executes it           → position closes

All addresses verified on-chain 2026-05-16.
Collateral: longs use the market token; shorts use USDC.
USD amounts: scaled × 10^6 (same as USDC)
"""

import os, asyncio, hashlib, struct, time
from typing import Optional

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import AccountMeta, Instruction
from solders.transaction import Transaction
from solders.message import Message
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from spl.token.instructions import get_associated_token_address
from solana.rpc.commitment import Confirmed
import base58

# ── Program & static addresses ────────────────────────────────────────────────
PERPS_PROGRAM   = Pubkey.from_string("PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu")
POOL            = Pubkey.from_string("5BUwFW4nRbftYTDMbgxykoFWqWHPzahFSNAaaaJtVKsq")
USDC_MINT       = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
TOKEN_PROGRAM   = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOC_TOKEN_PGM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM_PROGRAM  = Pubkey.from_string("11111111111111111111111111111111")

# Pre-computed static PDAs (verified on-chain)
PERPETUALS, _   = Pubkey.find_program_address([b"perpetuals"], PERPS_PROGRAM)
EVENT_AUTH, _   = Pubkey.find_program_address([b"__event_authority"], PERPS_PROGRAM)

# Custodies verified on-chain: PDA = ["custody", POOL, mint]
CUSTODIES = {
    "SOL":  Pubkey.from_string("7xS2gz2bTp3fwCC7knJvUWTEU9Tycczu6VhJYKgi1wdz"),
    "ETH":  Pubkey.from_string("AQCGyheWPLeo6Qp9WpYS9m3Qj479t7R636N9ey1rEjEn"),
    "WBTC": Pubkey.from_string("5Pv3gM9JrFFH883SWAhvJC9RPYmo8UNxuFtv5bMMALkm"),
}
USDC_CUSTODY = Pubkey.from_string("G18jKKXQwBbrHeiK3C9MRXhkHsLHf7XgCSisykV46EZa")

TOKEN_MINTS = {
    "SOL":  Pubkey.from_string("So11111111111111111111111111111111111111112"),
    "ETH":  Pubkey.from_string("7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs"),
    "WBTC": Pubkey.from_string("3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh"),
}

ORACLES = {
    "SOL": {
        "doves_ag": Pubkey.from_string("FYq2BWQ1V5P1WFBqr3qB2Kb5yHVvSv7upzKodgQE5zXh"),
        "pythnet": Pubkey.from_string("7UVimffxr9ow1uXYxsr4LHAcV58mLzhmwaeKvJ1pjLiE"),
    },
    "ETH": {
        "doves_ag": Pubkey.from_string("AFZnHPzy4mvVCffrVwhewHbFc93uTHvDSFrVH7GtfXF1"),
        "pythnet": Pubkey.from_string("42amVS4KgzR9rA28tkVYqVXjq9Qa8dcZQMbH5EYFX6XC"),
    },
    "WBTC": {
        "doves_ag": Pubkey.from_string("hUqAT1KQ7eW1i6Csp9CXYtpPfSAvi835V7wKi5fRfmC"),
        "pythnet": Pubkey.from_string("4cSM2e6rvbGQUFiJbqytoVMi5GgghSMr8LwVrT9VPSPo"),
    },
}

# Anchor instruction discriminators: sha256("global:<name>")[:8]
def _disc(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]

DISC_OPEN  = _disc("create_increase_position_market_request")
DISC_CLOSE = _disc("create_decrease_position_market_request")
DISC_TRIGGER_CLOSE = _disc("create_decrease_position_request2")

# USD scaling: protocol uses 10^6 for USD amounts
USD_SCALE  = 1_000_000
# Slippage tolerance: 1.5% on open, 1.5% on close
SLIP_PCT   = 0.015


# ── Key helpers ───────────────────────────────────────────────────────────────

def _load_keypair() -> Optional[Keypair]:
    raw = os.environ.get("SOLANA_PRIVATE_KEY", "").strip()
    if not raw:
        return None
    try:
        return Keypair.from_bytes(base58.b58decode(raw))
    except Exception as e:
        print(f"[exec] Keypair load failed: {e}")
        return None


def public_key_from_env() -> Optional[str]:
    """Return the configured signer public key without logging private material."""
    kp = _load_keypair()
    return str(kp.pubkey()) if kp else None


def _ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """Derive the associated token address for owner + mint."""
    return get_associated_token_address(owner, mint)


def _collateral_custody(sym: str, side: str) -> Pubkey:
    return CUSTODIES[sym] if side == "LONG" else USDC_CUSTODY


def _collateral_mint(sym: str, side: str) -> Pubkey:
    return TOKEN_MINTS[sym] if side == "LONG" else USDC_MINT


def _jup_min_out(side: str) -> Optional[int]:
    return 1 if side == "LONG" else None


def _position_pda(owner: Pubkey, custody: Pubkey, collateral_custody: Pubkey, side: str) -> Pubkey:
    side_byte = bytes([1 if side == "LONG" else 2])
    pda, _ = Pubkey.find_program_address(
        [b"position", bytes(owner), bytes(POOL), bytes(custody), bytes(collateral_custody), side_byte],
        PERPS_PROGRAM,
    )
    return pda


def _position_request_pda(position: Pubkey, counter: int, request_change: str) -> Pubkey:
    change_byte = bytes([1 if request_change == "increase" else 2])
    pda, _ = Pubkey.find_program_address(
        [b"position_request", bytes(position), struct.pack("<Q", counter), change_byte],
        PERPS_PROGRAM,
    )
    return pda


# ── Borsh encoding helpers ────────────────────────────────────────────────────

def _pack_u64(v: int) -> bytes:
    return struct.pack("<Q", v)

def _pack_option_u64(v: Optional[int]) -> bytes:
    return b"\x00" if v is None else b"\x01" + struct.pack("<Q", v)

def _pack_option_bool(v: Optional[bool]) -> bytes:
    return b"\x00" if v is None else b"\x01" + (b"\x01" if v else b"\x00")

def _pack_request_type(v: str) -> bytes:
    return bytes([1 if v == "Trigger" else 0])


def _encode_open_params(size_usd: int, collateral_tokens: int,
                        side: str, price_slippage: int, counter: int,
                        jup_min_out: Optional[int] = 1) -> bytes:
    side_byte = bytes([1 if side == "LONG" else 2])
    return (
        DISC_OPEN
        + _pack_u64(size_usd)
        + _pack_u64(collateral_tokens)
        + side_byte
        + _pack_u64(price_slippage)
        + _pack_option_u64(jup_min_out)
        + _pack_u64(counter)
    )


def _encode_close_params(size_usd: int, collateral_usd: int,
                         price_slippage: int, counter: int,
                         entire: bool = True,
                         jup_min_out: Optional[int] = 1) -> bytes:
    return (
        DISC_CLOSE
        + _pack_u64(collateral_usd)
        + _pack_u64(size_usd)
        + _pack_u64(price_slippage)
        + _pack_option_u64(jup_min_out)    # Some(1) → swap native→USDC; None → USDC collateral
        + _pack_option_bool(entire)        # entirePosition=True
        + _pack_u64(counter)
    )


def _encode_trigger_close_params(size_usd: int, trigger_price: int,
                                 trigger_above: bool, counter: int,
                                 entire: bool = True) -> bytes:
    return (
        DISC_TRIGGER_CLOSE
        + _pack_u64(0)                 # collateralUsdDelta
        + _pack_u64(size_usd)          # sizeUsdDelta
        + _pack_request_type("Trigger")
        + _pack_option_u64(None)       # priceSlippage
        + _pack_option_u64(None)       # jupiterMinimumOut
        + _pack_option_u64(trigger_price)
        + _pack_option_bool(trigger_above)
        + _pack_option_bool(entire)
        + _pack_u64(counter)
    )


# ── Instruction builders ──────────────────────────────────────────────────────

def _build_open_ix(owner: Pubkey, sym: str, side: str,
                   collateral_usdc: int, size_usd: int,
                   price_usd_scaled: int, counter: int,
                   pos_req_override: Optional[Pubkey] = None) -> Instruction:
    """Build createIncreasePositionMarketRequest instruction."""
    custody        = CUSTODIES[sym]
    coll_custody   = _collateral_custody(sym, side)
    jup_min        = _jup_min_out(side)
    owner_usdc_ata = _ata(owner, USDC_MINT)
    position       = _position_pda(owner, custody, coll_custody, side)
    pos_req        = pos_req_override if pos_req_override else _position_request_pda(position, counter, "increase")
    pos_req_ata    = _ata(pos_req, USDC_MINT)

    if side == "LONG":
        slippage = int(price_usd_scaled * (1 + SLIP_PCT))
    else:
        slippage = int(price_usd_scaled * (1 - SLIP_PCT))

    data = _encode_open_params(size_usd, collateral_usdc, side, slippage, counter, jup_min_out=jup_min)

    # Account order must match IDL exactly
    metas = [
        AccountMeta(owner,           True,  True),   # owner
        AccountMeta(owner_usdc_ata,  False, True),   # fundingAccount
        AccountMeta(PERPETUALS,      False, False),  # perpetuals
        AccountMeta(POOL,            False, False),  # pool
        AccountMeta(position,        False, True),   # position
        AccountMeta(pos_req,         False, True),   # positionRequest
        AccountMeta(pos_req_ata,     False, True),   # positionRequestAta
        AccountMeta(custody,         False, False),  # custody (market token)
        AccountMeta(coll_custody,    False, False),  # collateralCustody
        AccountMeta(USDC_MINT,       False, False),  # inputMint
        AccountMeta(SYSTEM_PROGRAM,  False, False),  # referral (null → system program)
        AccountMeta(TOKEN_PROGRAM,   False, False),  # tokenProgram
        AccountMeta(ASSOC_TOKEN_PGM, False, False),  # associatedTokenProgram
        AccountMeta(SYSTEM_PROGRAM,  False, False),  # systemProgram
        AccountMeta(EVENT_AUTH,      False, False),  # eventAuthority
        AccountMeta(PERPS_PROGRAM,   False, False),  # program
    ]
    return Instruction(PERPS_PROGRAM, data, metas)


def _build_close_ix(owner: Pubkey, sym: str, side: str,
                    price_usd_scaled: int, counter: int) -> Instruction:
    """Build createDecreasePositionMarketRequest (close entire position)."""
    custody        = CUSTODIES[sym]
    coll_custody   = _collateral_custody(sym, side)
    desired_mint   = _collateral_mint(sym, side)
    jup_min        = None
    receiving_ata  = _ata(owner, desired_mint)
    position       = _position_pda(owner, custody, coll_custody, side)
    pos_req        = _position_request_pda(position, counter, "decrease")
    pos_req_ata    = _ata(pos_req, desired_mint)

    if side == "LONG":
        slippage = int(price_usd_scaled * (1 - SLIP_PCT))
    else:
        slippage = int(price_usd_scaled * (1 + SLIP_PCT))

    # For close-entire: size and collateral deltas can be 0 when entirePosition=True
    data = _encode_close_params(0, 0, slippage, counter, entire=True, jup_min_out=jup_min)

    metas = [
        AccountMeta(owner,           True,  True),
        AccountMeta(receiving_ata,   False, True),   # receivingAccount
        AccountMeta(PERPETUALS,      False, False),
        AccountMeta(POOL,            False, False),
        AccountMeta(position,        False, True),
        AccountMeta(pos_req,         False, True),
        AccountMeta(pos_req_ata,     False, True),
        AccountMeta(custody,         False, False),  # custody (market token)
        AccountMeta(coll_custody,    False, False),  # collateralCustody
        AccountMeta(desired_mint,    False, False),  # desiredMint
        AccountMeta(SYSTEM_PROGRAM,  False, False),  # referral
        AccountMeta(TOKEN_PROGRAM,   False, False),
        AccountMeta(ASSOC_TOKEN_PGM, False, False),
        AccountMeta(SYSTEM_PROGRAM,  False, False),
        AccountMeta(EVENT_AUTH,      False, False),
        AccountMeta(PERPS_PROGRAM,   False, False),
    ]
    return Instruction(PERPS_PROGRAM, data, metas)


def _build_trigger_close_ix(owner: Pubkey, sym: str, side: str,
                            trigger_price_scaled: int, trigger_above: bool,
                            counter: int, size_usd: int) -> Instruction:
    """Build createDecreasePositionRequest2 trigger close for TP/SL."""
    custody        = CUSTODIES[sym]
    coll_custody   = _collateral_custody(sym, side)
    desired_mint   = _collateral_mint(sym, side)
    receiving_ata  = _ata(owner, desired_mint)
    position       = _position_pda(owner, custody, coll_custody, side)
    pos_req        = _position_request_pda(position, counter, "decrease")
    pos_req_ata    = _ata(pos_req, desired_mint)
    data           = _encode_trigger_close_params(size_usd, trigger_price_scaled, trigger_above, counter)
    oracle         = ORACLES[sym]

    metas = [
        AccountMeta(owner,             True,  True),
        AccountMeta(receiving_ata,     False, True),
        AccountMeta(PERPETUALS,        False, False),
        AccountMeta(POOL,              False, False),
        AccountMeta(position,          False, False),
        AccountMeta(pos_req,           False, True),
        AccountMeta(pos_req_ata,       False, True),
        AccountMeta(custody,           False, False),
        AccountMeta(oracle["doves_ag"], False, False),
        AccountMeta(oracle["pythnet"], False, False),
        AccountMeta(coll_custody,      False, False),
        AccountMeta(desired_mint,      False, False),
        AccountMeta(SYSTEM_PROGRAM,    False, False),
        AccountMeta(TOKEN_PROGRAM,     False, False),
        AccountMeta(ASSOC_TOKEN_PGM,   False, False),
        AccountMeta(SYSTEM_PROGRAM,    False, False),
        AccountMeta(EVENT_AUTH,        False, False),
        AccountMeta(PERPS_PROGRAM,     False, False),
    ]
    return Instruction(PERPS_PROGRAM, data, metas)


# ── Transaction sender ─────────────────────────────────────────────────────────

async def _extract_right_pda_from_sim(client: AsyncClient, keypair: Keypair,
                                       ix: Instruction) -> Optional[Pubkey]:
    """
    Simulate a transaction and extract the 'Right:' PDA address from a
    ConstraintSeeds error log. Returns None if simulation succeeded or if
    the error is unrelated to ConstraintSeeds.
    """
    bh      = await client.get_latest_blockhash()
    msg     = Message.new_with_blockhash([ix], keypair.pubkey(), bh.value.blockhash)
    tx      = Transaction([keypair], msg, bh.value.blockhash)
    sim     = await client.simulate_transaction(tx, sig_verify=False)
    logs    = sim.value.logs or []
    for i, log in enumerate(logs):
        if "Right:" in log and i + 1 < len(logs):
            candidate = logs[i + 1].replace("Program log: ", "").strip()
            try:
                return Pubkey.from_string(candidate)
            except Exception:
                pass
    return None


async def _send(rpc_url: str, keypair: Keypair, ix: Instruction) -> str:
    """Sign and send one instruction, return tx signature."""
    return await _send_many(rpc_url, keypair, [ix])


async def _send_many(rpc_url: str, keypair: Keypair, ixs: list[Instruction]) -> str:
    """Sign and send instructions in one transaction, return tx signature."""
    client = AsyncClient(rpc_url, commitment=Confirmed)
    try:
        bh_resp   = await client.get_latest_blockhash()
        blockhash = bh_resp.value.blockhash

        msg = Message.new_with_blockhash(ixs, keypair.pubkey(), blockhash)
        tx  = Transaction([keypair], msg, blockhash)

        resp = await client.send_transaction(
            tx,
            opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed),
        )
        sig = str(resp.value)
        print(f"[exec] TX submitted: {sig}")
        return sig
    finally:
        await client.close()


def _decode_position_size_usd(data: bytes) -> int:
    # Anchor discriminator + owner/pool/custody/collateralCustody + open/update time
    # + side enum + price, then sizeUsd.
    size_offset = 8 + (32 * 4) + 8 + 8 + 1 + 8
    if len(data) < size_offset + 8:
        return 0
    return struct.unpack_from("<Q", data, size_offset)[0]


async def _fetch_position_size_usd(rpc_url: str, position: Pubkey) -> int:
    client = AsyncClient(rpc_url, commitment=Confirmed)
    try:
        resp = await client.get_account_info(position)
        if resp.value is None:
            return 0
        return _decode_position_size_usd(bytes(resp.value.data))
    finally:
        await client.close()


# ── Public API (sync wrappers called from trading.py) ────────────────────────

def open_position(sym: str, side: str, collateral_usd: float,
                  leverage: float, current_price: float) -> dict:
    """
    Open a position on Jupiter Perps.
    collateral_usd: how much USDC to post (e.g. 50.0)
    leverage: multiplier (e.g. 2.0 → position size = collateral * leverage)
    current_price: current market price (USD)
    Returns {"ok": bool, "tx": str|None, "msg": str}
    """
    rpc = os.environ.get("RPC_URL", "")
    if not rpc:
        return {"ok": False, "tx": None, "msg": "RPC_URL not set in .env"}

    kp = _load_keypair()
    if not kp:
        return {"ok": False, "tx": None, "msg": "SOLANA_PRIVATE_KEY not set in .env"}

    if sym not in CUSTODIES:
        return {"ok": False, "tx": None, "msg": f"Unknown symbol: {sym}"}

    collateral_tokens = int(collateral_usd * 1_000_000)       # USDC, 6 dec
    size_usd          = int(collateral_usd * leverage * USD_SCALE)
    price_scaled      = int(current_price * USD_SCALE)
    counter           = 0  # new position always starts at counter 0

    async def _open_async():
        client = AsyncClient(rpc, commitment=Confirmed)
        try:
            # First build with our best-guess pos_req
            ix = _build_open_ix(kp.pubkey(), sym, side,
                                 collateral_tokens, size_usd, price_scaled, counter)

            # Preflight: if pos_req seeds are wrong the program tells us the Right address
            correct_req = await _extract_right_pda_from_sim(client, kp, ix)
            if correct_req:
                print(f"[exec] pos_req PDA corrected to {correct_req}")
                ix = _build_open_ix(kp.pubkey(), sym, side,
                                     collateral_tokens, size_usd, price_scaled, counter,
                                     pos_req_override=correct_req)

            return await _send(rpc, kp, ix)
        finally:
            await client.close()

    try:
        tx = asyncio.run(_open_async())
        return {"ok": True, "tx": tx, "msg": f"Position request submitted ({side} {sym})"}
    except Exception as e:
        msg = str(e)
        print(f"[exec] open_position error: {msg}")
        return {"ok": False, "tx": None, "msg": msg}


def close_position(sym: str, side: str, current_price: float) -> dict:
    """
    Close an entire open position on Jupiter Perps.
    Returns {"ok": bool, "tx": str|None, "msg": str}
    """
    rpc = os.environ.get("RPC_URL", "")
    if not rpc:
        return {"ok": False, "tx": None, "msg": "RPC_URL not set in .env"}

    kp = _load_keypair()
    if not kp:
        return {"ok": False, "tx": None, "msg": "SOLANA_PRIVATE_KEY not set in .env"}

    if sym not in CUSTODIES:
        return {"ok": False, "tx": None, "msg": f"Unknown symbol: {sym}"}

    price_scaled = int(current_price * USD_SCALE)
    counter      = int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF

    ix = _build_close_ix(kp.pubkey(), sym, side, price_scaled, counter)

    try:
        tx = asyncio.run(_send(rpc, kp, ix))
        return {"ok": True, "tx": tx, "msg": f"Close request submitted ({side} {sym})"}
    except Exception as e:
        msg = str(e)
        print(f"[exec] close_position error: {msg}")
        return {"ok": False, "tx": None, "msg": msg}


def create_tpsl_requests(sym: str, side: str, stop_loss: float,
                         take_profit: float) -> dict:
    """
    Create on-chain trigger close requests for the full position.
    TP and SL are separate PositionRequest accounts; Jupiter keepers execute
    whichever trigger is reached.
    """
    rpc = os.environ.get("RPC_URL", "")
    if not rpc:
        return {"ok": False, "tx": None, "msg": "RPC_URL not set in .env"}

    kp = _load_keypair()
    if not kp:
        return {"ok": False, "tx": None, "msg": "SOLANA_PRIVATE_KEY not set in .env"}

    if sym not in CUSTODIES:
        return {"ok": False, "tx": None, "msg": f"Unknown symbol: {sym}"}
    if side not in ("LONG", "SHORT"):
        return {"ok": False, "tx": None, "msg": f"Unknown side: {side}"}
    if stop_loss <= 0 or take_profit <= 0:
        return {"ok": False, "tx": None, "msg": "stop_loss and take_profit must be positive"}

    base_counter = int(time.time() * 1000) & 0xFFFFFFFFFFFFFF00
    position = _position_pda(kp.pubkey(), CUSTODIES[sym], _collateral_custody(sym, side), side)
    size_usd = asyncio.run(_fetch_position_size_usd(rpc, position))
    if size_usd <= 0:
        return {"ok": False, "tx": None, "msg": "position not found or size is zero"}

    tp_above = side == "LONG"
    sl_above = side == "SHORT"
    requests = []
    requests.append(("take_profit", _build_trigger_close_ix(
        kp.pubkey(), sym, side, int(take_profit * USD_SCALE), tp_above, base_counter + 1, size_usd
    )))
    requests.append(("stop_loss", _build_trigger_close_ix(
        kp.pubkey(), sym, side, int(stop_loss * USD_SCALE), sl_above, base_counter + 2, size_usd
    )))

    txs = {}
    errors = {}
    for label, ix in requests:
        try:
            txs[label] = asyncio.run(_send(rpc, kp, ix))
        except Exception as e:
            errors[label] = str(e)
            print(f"[exec] create_tpsl_requests {label} error: {errors[label]}")

    ok = bool(txs) and not errors
    partial = bool(txs) and bool(errors)
    if ok:
        msg = f"TP/SL requests submitted ({side} {sym})"
    elif partial:
        msg = f"Partial TP/SL submitted ({side} {sym}): {', '.join(txs.keys())} ok, {', '.join(errors.keys())} failed"
    else:
        msg = f"TP/SL requests failed ({side} {sym})"
    return {
        "ok": ok,
        "partial": partial,
        "tx": txs.get("take_profit") or txs.get("stop_loss"),
        "take_profit_tx": txs.get("take_profit"),
        "stop_loss_tx": txs.get("stop_loss"),
        "errors": errors,
        "msg": msg,
    }


def get_position(sym: str, side: str, owner_pubkey_str: str) -> dict:
    """
    Fetch an open position's data from chain.
    Returns position info or {"exists": False}.
    """
    rpc = os.environ.get("RPC_URL", "")
    if not rpc:
        return {"exists": False, "msg": "RPC_URL not set"}

    try:
        owner        = Pubkey.from_string(owner_pubkey_str)
        custody      = CUSTODIES[sym]
        coll_custody = _collateral_custody(sym, side)
        pos_pk       = _position_pda(owner, custody, coll_custody, side)

        async def _fetch():
            c = AsyncClient(rpc, commitment=Confirmed)
            try:
                resp = await c.get_account_info(pos_pk)
                return resp.value
            finally:
                await c.close()

        acct = asyncio.run(_fetch())
        if acct is None:
            return {"exists": False, "address": str(pos_pk)}

        size_usd = _decode_position_size_usd(bytes(acct.data))
        if size_usd <= 0:
            return {"exists": False, "address": str(pos_pk), "size_usd": size_usd}

        return {
            "exists":  True,
            "address": str(pos_pk),
            "data_len": len(acct.data),
            "size_usd": size_usd,
        }
    except Exception as e:
        return {"exists": False, "msg": str(e)}


def usdc_balance(owner_pubkey_str: str) -> Optional[float]:
    """Return owner's USDC balance in USD (None on error)."""
    rpc = os.environ.get("RPC_URL", "")
    if not rpc:
        return None
    try:
        owner    = Pubkey.from_string(owner_pubkey_str)
        usdc_ata = _ata(owner, USDC_MINT)

        async def _fetch():
            c = AsyncClient(rpc, commitment=Confirmed)
            try:
                resp = await c.get_token_account_balance(usdc_ata)
                return resp.value
            finally:
                await c.close()

        bal = asyncio.run(_fetch())
        if bal is None:
            return None
        return float(bal.ui_amount or 0)
    except Exception:
        return None
