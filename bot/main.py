import os
import time
from typing import Dict

from binance_client import BinanceClient
from env_load import load_bot_env
from logger import setup_logger
from risk import can_trade
from state import BotState
from strategy import signal_from_closes


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def get_usdt_equity(account: Dict) -> float:
    for balance in account.get("balances", []):
        if balance.get("asset") == "USDT":
            free_amt = float(balance.get("free", "0"))
            locked_amt = float(balance.get("locked", "0"))
            return free_amt + locked_amt
    return 0.0


def main() -> None:
    load_bot_env()
    log = setup_logger()

    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    base_url = os.getenv("BASE_URL", "https://demo-api.binance.com")
    symbol = os.getenv("SYMBOL", "BTCUSDT").upper()
    interval = os.getenv("KLINE_INTERVAL", "1m")
    short_window = env_int("SHORT_WINDOW", 7)
    long_window = env_int("LONG_WINDOW", 25)
    quote_amount = env_float("TRADE_QUOTE_AMOUNT", 25.0)
    max_daily_loss_pct = env_float("MAX_DAILY_LOSS_PCT", 2.0)
    loop_seconds = env_int("LOOP_SECONDS", 20)
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_API_KEY and BINANCE_API_SECRET are required")

    client = BinanceClient(api_key=api_key, api_secret=api_secret, base_url=base_url)
    state = BotState()

    log.info("Bot started | symbol=%s interval=%s dry_run=%s", symbol, interval, dry_run)

    while True:
        try:
            account = client.get_account()
            equity = get_usdt_equity(account)
            if not can_trade(equity, state, max_daily_loss_pct):
                log.warning("Daily loss threshold reached. Waiting...")
                time.sleep(loop_seconds)
                continue

            closes = client.get_closes(symbol, interval, long_window + 5)
            signal = signal_from_closes(closes, short_window, long_window)
            price = client.get_price(symbol)

            log.info(
                "Signal=%s | price=%.4f | has_position=%s | usdt_equity=%.2f",
                signal,
                price,
                state.has_position,
                equity,
            )

            if signal == "BUY" and not state.has_position:
                result = client.create_market_order(
                    symbol=symbol,
                    side="BUY",
                    quote_order_qty=quote_amount,
                    dry_run=dry_run,
                )
                state.has_position = True
                state.entry_price = price
                log.info("BUY executed: %s", result)
            elif signal == "SELL" and state.has_position:
                result = client.create_market_order(
                    symbol=symbol,
                    side="SELL",
                    quote_order_qty=quote_amount,
                    dry_run=dry_run,
                )
                state.has_position = False
                state.entry_price = 0.0
                log.info("SELL executed: %s", result)

            time.sleep(loop_seconds)
        except Exception as exc:
            log.exception("Loop error: %s", exc)
            time.sleep(max(loop_seconds, 10))


if __name__ == "__main__":
    main()
