"""Paylaşılan futures reverse emir mantığı (webhook + ücretsiz sinyal döngüsü)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from futures_client import FuturesClient


BOT_DIR = Path(__file__).resolve().parent
RUNTIME_OVERRIDES_PATH = BOT_DIR / "runtime_overrides.json"


def load_runtime_overrides() -> Dict[str, Any]:
    if not RUNTIME_OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(RUNTIME_OVERRIDES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def effective_strategy(
    *,
    env_dry_run: bool,
    env_allow_live: bool,
    env_default_symbol: str,
) -> Dict[str, Any]:
    rt = load_runtime_overrides()
    dry_run = bool(rt["dry_run"]) if "dry_run" in rt else env_dry_run
    allow_live = bool(rt["allow_live_orders"]) if "allow_live_orders" in rt else env_allow_live
    default_symbol = str(rt.get("default_symbol") or env_default_symbol).upper()
    return {
        "trading_paused": bool(rt.get("trading_paused", False)),
        "dry_run": dry_run,
        "allow_live_orders": allow_live,
        "default_symbol": default_symbol,
        "mode": "futures_reverse",
    }


def merged_symbol_qty_map(base: Dict[str, float]) -> Dict[str, float]:
    merged = dict(base)
    raw = load_runtime_overrides().get("symbol_qty_map")
    if isinstance(raw, dict):
        try:
            merged.update({str(k).upper(): float(v) for k, v in raw.items()})
        except Exception:
            pass
    return merged


def execute_futures_reverse(
    client: FuturesClient,
    *,
    action: str,
    symbol: str,
    target_qty: float,
    dry_run: bool,
) -> Dict[str, Any]:
    """BUY: short kapat + long aç. SELL: long kapat + short aç."""
    action = action.upper()
    if action not in {"BUY", "SELL"}:
        raise ValueError("action must be BUY or SELL")

    current_amt = client.get_position_amt(symbol)
    orders: List[Dict[str, Any]] = []

    if action == "BUY":
        if current_amt > 0:
            return {"ok": True, "skipped": True, "reason": f"already long ({current_amt})"}
        if current_amt < 0:
            close_result = client.create_market_order(
                symbol=symbol,
                side="BUY",
                quantity=abs(current_amt),
                dry_run=dry_run,
                reduce_only=True,
            )
            orders.append({"type": "close_short", "result": close_result})
        open_result = client.create_market_order(
            symbol=symbol,
            side="BUY",
            quantity=target_qty,
            dry_run=dry_run,
            reduce_only=False,
        )
        orders.append({"type": "open_long", "result": open_result})
    else:
        if current_amt < 0:
            return {"ok": True, "skipped": True, "reason": f"already short ({current_amt})"}
        if current_amt > 0:
            close_result = client.create_market_order(
                symbol=symbol,
                side="SELL",
                quantity=abs(current_amt),
                dry_run=dry_run,
                reduce_only=True,
            )
            orders.append({"type": "close_long", "result": close_result})
        open_result = client.create_market_order(
            symbol=symbol,
            side="SELL",
            quantity=target_qty,
            dry_run=dry_run,
            reduce_only=False,
        )
        orders.append({"type": "open_short", "result": open_result})

    return {"ok": True, "orders": orders}


def kline_closes(client: FuturesClient, symbol: str, interval: str, limit: int) -> List[float]:
    candles = client.get_klines(symbol, interval, limit)
    return [float(c["close"]) for c in candles]


def parse_signal_symbols(env_map: Dict[str, float], extra: str) -> List[str]:
    raw = (extra or "").strip()
    if raw:
        parts = [p.strip().upper() for p in raw.replace(";", ",").split(",") if p.strip()]
        return parts
    return sorted(env_map.keys())
