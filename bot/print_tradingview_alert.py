#!/usr/bin/env python3
"""bot/.env yuklenir; TradingView alarmina yapistirmalik JSON uretir."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_load import load_bot_env


def main() -> None:
    load_bot_env()
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        raise SystemExit("WEBHOOK_SECRET bos; bot/.env dosyasini kontrol et.")

    symbol = os.getenv("SYMBOL", "BTCUSDT").upper()
    qty = float(os.getenv("FUTURES_QTY", "0.002"))

    def payload(action: str) -> dict:
        return {
            "secret": secret,
            "action": action,
            "symbol": symbol,
            "quantity": qty,
        }

    print("TradingView > Create Alert > Notifications > Webhook URL")
    print("  Ornek: https://<tunel-adresin>/tradingview/webhook")
    print()
    print("Message (JSON) — BUY:")
    print(json.dumps(payload("BUY"), ensure_ascii=False))
    print()
    print("Message (JSON) — SELL:")
    print(json.dumps(payload("SELL"), ensure_ascii=False))


if __name__ == "__main__":
    main()
