import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import Flask, Response, jsonify, request

from env_load import load_bot_env
from futures_client import FuturesClient
from logger import setup_logger


BOT_DIR = Path(__file__).resolve().parent
RUNTIME_OVERRIDES_PATH = BOT_DIR / "runtime_overrides.json"


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _load_runtime_symbol_qty_map() -> Dict[str, float]:
    if not RUNTIME_OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(RUNTIME_OVERRIDES_PATH.read_text(encoding="utf-8"))
        raw = data.get("symbol_qty_map")
        if not isinstance(raw, dict):
            return {}
        return {str(k).upper(): float(v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_runtime_symbol_qty_map(symbol_qty_map: Dict[str, float]) -> None:
    payload = {"symbol_qty_map": symbol_qty_map}
    RUNTIME_OVERRIDES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _merged_symbol_qty_map(base: Dict[str, float]) -> Dict[str, float]:
    merged = dict(base)
    merged.update(_load_runtime_symbol_qty_map())
    return merged


def _dashboard_token() -> str:
    return os.getenv("DASHBOARD_TOKEN", "").strip()


def _dashboard_auth_error() -> Optional[Tuple[Any, int]]:
    token = _dashboard_token()
    if not token:
        return jsonify({"ok": False, "error": "dashboard disabled (set DASHBOARD_TOKEN)"}), 503
    auth = request.headers.get("Authorization", "")
    got = ""
    if auth.lower().startswith("bearer "):
        got = auth[7:].strip()
    if not got:
        got = request.headers.get("X-Dashboard-Token", "").strip()
    if not got:
        got = str(request.args.get("token", "")).strip()
    if got != token:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


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
    tradingview_strategy_url = os.getenv("TRADINGVIEW_STRATEGY_URL", "").strip()

    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_API_KEY and BINANCE_API_SECRET are required")
    if not webhook_secret:
        raise RuntimeError("WEBHOOK_SECRET is required")

    client = FuturesClient(api_key=api_key, api_secret=api_secret, base_url=base_url)
    app = Flask(__name__)

    @app.get("/health")
    def health() -> Any:
        qty_map = _merged_symbol_qty_map(symbol_qty_map)
        return jsonify(
            {
                "ok": True,
                "dry_run": dry_run,
                "allow_live_orders": allow_live_orders,
                "base_url": base_url,
                "mode": "futures_reverse",
                "symbol_qty_map": qty_map,
                "symbol_qty_runtime_overrides": bool(_load_runtime_symbol_qty_map()),
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
            qty_map = _merged_symbol_qty_map(symbol_qty_map)
            target_qty = (
                float(quantity_raw)
                if quantity_raw is not None
                else qty_map.get(symbol, default_futures_qty)
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

    @app.get("/dashboard")
    def dashboard_page() -> Any:
        if not _dashboard_token():
            return Response(
                "<p>Panel kapalı: <code>bot/.env</code> içine <code>DASHBOARD_TOKEN</code> ekleyip "
                "<code>systemctl restart tradebot</code> çalıştırın.</p>",
                status=503,
                mimetype="text/html; charset=utf-8",
            )
        html_path = BOT_DIR / "dashboard_static.html"
        if not html_path.exists():
            return Response("dashboard_static.html bulunamadı", status=500, mimetype="text/plain")
        host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host", "")
        proto = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
        public_base = ""
        if host:
            public_base = f"{proto}://{host}".rstrip("/")
        boot = json.dumps(
            {
                "tradingview_strategy_url": tradingview_strategy_url,
                "public_base_url": public_base,
            },
            ensure_ascii=False,
        )
        html = html_path.read_text(encoding="utf-8").replace("__BOOTSTRAP__", boot)
        return Response(html, mimetype="text/html; charset=utf-8")

    @app.get("/api/dashboard/status")
    def dashboard_status() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        rt = _load_runtime_symbol_qty_map()
        merged = _merged_symbol_qty_map(symbol_qty_map)
        return jsonify(
            {
                "ok": True,
                "server_time_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "dry_run": dry_run,
                "allow_live_orders": allow_live_orders,
                "base_url": base_url,
                "mode": "futures_reverse",
                "symbol_qty_env": symbol_qty_map,
                "symbol_qty_runtime": rt,
                "symbol_qty_merged": merged,
                "tradingview_strategy_url": tradingview_strategy_url or "",
            }
        )

    @app.get("/api/dashboard/overrides")
    def dashboard_overrides_get() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        return jsonify({"ok": True, "symbol_qty_map": _load_runtime_symbol_qty_map()})

    @app.put("/api/dashboard/overrides")
    def dashboard_overrides_put() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        raw = body.get("symbol_qty_map")
        if not isinstance(raw, dict):
            return jsonify({"ok": False, "error": "symbol_qty_map object required"}), 400
        try:
            cleaned = {str(k).upper(): float(v) for k, v in raw.items()}
        except Exception:
            return jsonify({"ok": False, "error": "invalid map"}), 400
        _save_runtime_symbol_qty_map(cleaned)
        log.info("Dashboard updated runtime symbol_qty_map (%d symbols)", len(cleaned))
        return jsonify({"ok": True, "symbol_qty_map": cleaned})

    @app.delete("/api/dashboard/overrides")
    def dashboard_overrides_delete() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        if RUNTIME_OVERRIDES_PATH.exists():
            RUNTIME_OVERRIDES_PATH.unlink()
        log.info("Dashboard cleared runtime symbol_qty_map overrides")
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("WEBHOOK_PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
