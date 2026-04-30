import os
import json
from typing import Any, Dict

from flask import Flask, jsonify, request

from env_load import load_bot_env
from futures_client import FuturesClient
from logger import setup_logger


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def create_app() -> Flask:
    load_bot_env()
    log = setup_logger()

    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    base_url = os.getenv("BASE_URL", "https://demo-fapi.binance.com")
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    default_symbol = os.getenv("SYMBOL", "BTCUSDT").upper()
    default_futures_qty = env_float("FUTURES_QTY", 0.002)
    symbol_qty_map_raw = os.getenv("SYMBOL_QTY_MAP", "{}")
    try:
        symbol_qty_map = {
            str(k).upper(): float(v)
            for k, v in json.loads(symbol_qty_map_raw).items()
        }
    except Exception:
        symbol_qty_map = {}
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    allow_live_orders = os.getenv("ALLOW_LIVE_ORDERS", "false").lower() == "true"

    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_API_KEY and BINANCE_API_SECRET are required")
    if not webhook_secret:
        raise RuntimeError("WEBHOOK_SECRET is required")

    client = FuturesClient(api_key=api_key, api_secret=api_secret, base_url=base_url)
    app = Flask(__name__)

    @app.get("/health")
    def health() -> Any:
        return jsonify(
            {
                "ok": True,
                "dry_run": dry_run,
                "allow_live_orders": allow_live_orders,
                "base_url": base_url,
                "mode": "futures_reverse",
                "symbol_qty_map": symbol_qty_map,
            }
        )

    @app.post("/tradingview/webhook")
    def tradingview_webhook() -> Any:
        payload: Dict[str, Any] = request.get_json(silent=True) or {}
        secret = payload.get("secret", "")
        action = str(payload.get("action", "")).upper()
        symbol = str(payload.get("symbol", default_symbol)).upper()
        quantity_raw = payload.get("quantity")

        if secret != webhook_secret:
            return jsonify({"ok": False, "error": "invalid secret"}), 401

        if action not in {"BUY", "SELL"}:
            return jsonify({"ok": False, "error": "action must be BUY or SELL"}), 400
        if not dry_run and not allow_live_orders:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "live orders blocked: set ALLOW_LIVE_ORDERS=true in bot/.env",
                    }
                ),
                400,
            )

        try:
            target_qty = (
                float(quantity_raw)
                if quantity_raw is not None
                else symbol_qty_map.get(symbol, default_futures_qty)
            )
            current_amt = client.get_position_amt(symbol)
            orders = []

            # Futures reverse:
            # BUY => short kapat + long ac
            # SELL => long kapat + short ac
            if action == "BUY":
                if current_amt > 0:
                    return jsonify(
                        {
                            "ok": True,
                            "skipped": True,
                            "reason": f"already long ({current_amt})",
                        }
                    )
                if current_amt < 0:
                    close_qty = abs(current_amt)
                    close_result = client.create_market_order(
                        symbol=symbol,
                        side="BUY",
                        quantity=close_qty,
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
            elif action == "SELL":
                if current_amt < 0:
                    return jsonify(
                        {
                            "ok": True,
                            "skipped": True,
                            "reason": f"already short ({current_amt})",
                        }
                    )
                if current_amt > 0:
                    close_qty = abs(current_amt)
                    close_result = client.create_market_order(
                        symbol=symbol,
                        side="SELL",
                        quantity=close_qty,
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

            log.info(
                "TradingView reverse | action=%s symbol=%s current_amt=%s target_qty=%s dry_run=%s",
                action,
                symbol,
                current_amt,
                target_qty,
                dry_run,
            )
            return jsonify({"ok": True, "orders": orders})
        except Exception as exc:
            log.exception("Webhook order error: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("WEBHOOK_PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
