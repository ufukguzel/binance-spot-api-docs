#!/usr/bin/env python3
"""Binance mum verisinden MA kesişimi sinyali üretir ve reverse futures emir gönderir."""
from __future__ import annotations

import json
import os
import time
from typing import Dict

from env_load import load_bot_env
from futures_client import FuturesClient
from logger import setup_logger
from strategy import signal_from_closes
from activity_log import append_activity, summarize_order_result
from trade_logic import (
    effective_strategy,
    execute_futures_reverse,
    kline_closes,
    merged_symbol_qty_map,
    parse_signal_symbols,
)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def main() -> None:
    load_bot_env()
    log = setup_logger()

    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    base_url = os.getenv("BASE_URL", "https://demo-fapi.binance.com")
    default_symbol = os.getenv("SYMBOL", "BTCUSDT").upper()
    interval = os.getenv("KLINE_INTERVAL", "1m")
    short_window = env_int("SHORT_WINDOW", 7)
    long_window = env_int("LONG_WINDOW", 25)
    default_qty = env_float("FUTURES_QTY", 0.002)
    loop_seconds = env_int("LOOP_SECONDS", 20)
    dry_run_env = os.getenv("DRY_RUN", "true").lower() == "true"
    allow_live_env = os.getenv("ALLOW_LIVE_ORDERS", "false").lower() == "true"

    symbol_qty_env: Dict[str, float] = {}
    try:
        symbol_qty_env = {
            str(k).upper(): float(v)
            for k, v in json.loads(os.getenv("SYMBOL_QTY_MAP", "{}")).items()
        }
    except Exception:
        symbol_qty_env = {}

    symbols = parse_signal_symbols(symbol_qty_env, os.getenv("SIGNAL_SYMBOLS", ""))
    if not symbols:
        symbols = [default_symbol]

    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_API_KEY and BINANCE_API_SECRET are required")

    client = FuturesClient(api_key=api_key, api_secret=api_secret, base_url=base_url)
    last_signal: Dict[str, str] = {s: "HOLD" for s in symbols}
    kline_limit = long_window + 10

    log.info(
        "Signal runner started (FREE path) | symbols=%s interval=%s MA %d/%d loop=%ds",
        ",".join(symbols),
        interval,
        short_window,
        long_window,
        loop_seconds,
    )
    log.info("Emirler bu süreçten gider; Mac'te ikinci kopya calistirmayin (cift emir)")

    while True:
        try:
            cfg = effective_strategy(
                env_dry_run=dry_run_env,
                env_allow_live=allow_live_env,
                env_default_symbol=default_symbol,
            )
            qty_map = merged_symbol_qty_map(symbol_qty_env)

            if cfg["trading_paused"]:
                log.info("Trading paused (dashboard) — sinyal atlanıyor")
                time.sleep(loop_seconds)
                continue

            if not cfg["dry_run"] and not cfg["allow_live_orders"]:
                log.warning("Live orders blocked (ALLOW_LIVE_ORDERS=false)")
                time.sleep(loop_seconds)
                continue

            for symbol in symbols:
                try:
                    closes = kline_closes(client, symbol, interval, kline_limit)
                    signal = signal_from_closes(closes, short_window, long_window)
                    prev = last_signal.get(symbol, "HOLD")

                    # HOLD iken last_signal sıfırlanmaz; yoksa aynı yönde tekrar BUY/SELL tetiklenir
                    if signal == "HOLD":
                        continue
                    if signal == prev:
                        continue

                    target_qty = qty_map.get(symbol, default_qty)
                    log.info("Signal change %s | %s -> %s | qty=%s", symbol, prev, signal, target_qty)
                    last_close = closes[-1] if closes else None
                    append_activity(
                        source="signal",
                        event="signal_change",
                        symbol=symbol,
                        action=signal,
                        status="trigger",
                        message=f"{prev} -> {signal} qty={target_qty}",
                        ok=True,
                        extra={"price": last_close} if last_close is not None else None,
                    )

                    result = execute_futures_reverse(
                        client,
                        action=signal,
                        symbol=symbol,
                        target_qty=target_qty,
                        dry_run=cfg["dry_run"],
                    )
                    last_signal[symbol] = signal
                    log.info("Order result %s: %s", symbol, result)
                    st, msg, ok = summarize_order_result(result)
                    append_activity(
                        source="signal",
                        event="order",
                        symbol=symbol,
                        action=signal,
                        status=st,
                        message=msg,
                        ok=ok,
                    )
                except Exception as exc:
                    log.exception("Symbol loop error %s: %s", symbol, exc)

            time.sleep(loop_seconds)
        except Exception as exc:
            log.exception("Signal runner error: %s", exc)
            time.sleep(max(loop_seconds, 10))


if __name__ == "__main__":
    main()
