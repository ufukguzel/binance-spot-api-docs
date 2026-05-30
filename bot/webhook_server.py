import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, Response, jsonify, redirect, request

from env_load import load_bot_env
from futures_client import FuturesClient
from activity_log import (
    event_type_label,
    markers_from_activity,
    markers_from_ma_crosses,
    read_activity,
    status_label,
)
from logger import setup_logger
from strategy import historical_ma_signals, ma_line_series, signal_from_closes, sma


BOT_DIR = Path(__file__).resolve().parent
RUNTIME_OVERRIDES_PATH = BOT_DIR / "runtime_overrides.json"


def _ma_value_at(series: List[Dict[str, Any]], bar_time: int) -> Optional[float]:
    for point in series:
        if int(point.get("time", 0)) == bar_time:
            return float(point["value"])
    return None


def _enrich_signal_event(
    ev: Dict[str, Any],
    *,
    symbol: str,
    interval: str,
    short_window: int,
    long_window: int,
    ma_short_series: List[Dict[str, Any]],
    ma_long_series: List[Dict[str, Any]],
) -> Dict[str, Any]:
    bar_time = int(ev.get("time", 0))
    action = (ev.get("action") or "").upper()
    price = ev.get("price")
    ma_s = _ma_value_at(ma_short_series, bar_time)
    ma_l = _ma_value_at(ma_long_series, bar_time)
    qty = ev.get("quantity")
    act_tr = "AL" if action == "BUY" else "SAT" if action == "SELL" else action
    parts = [f"{symbol} · {act_tr}"]
    if price is not None:
        parts.append(f"@ {float(price):.6g}")
    if ma_s is not None and ma_l is not None:
        parts.append(f"MA{short_window}={float(ma_s):.6g}")
        parts.append(f"MA{long_window}={float(ma_l):.6g}")
    if qty is not None:
        parts.append(f"miktar={qty}")
    row = {
        **ev,
        "symbol": symbol,
        "interval": interval,
        "ma_short": ma_s,
        "ma_long": ma_l,
        "short_window": short_window,
        "long_window": long_window,
        "summary": " · ".join(parts),
        "event_label": ev.get("event_label") or event_type_label(str(ev.get("event", ""))),
        "status_label": ev.get("status_label")
        or status_label(str(ev.get("status", "")), str(ev.get("event", ""))),
    }
    if not row.get("id"):
        row["id"] = f"{ev.get('kind', 'sig')}-{bar_time}-{action}"
    return row


def _load_runtime_overrides() -> Dict[str, Any]:
    if not RUNTIME_OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(RUNTIME_OVERRIDES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_runtime_overrides(data: Dict[str, Any]) -> None:
    RUNTIME_OVERRIDES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_runtime_symbol_qty_map() -> Dict[str, float]:
    raw = _load_runtime_overrides().get("symbol_qty_map")
    if not isinstance(raw, dict):
        return {}
    try:
        return {str(k).upper(): float(v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_runtime_symbol_qty_map(symbol_qty_map: Dict[str, float]) -> None:
    data = _load_runtime_overrides()
    data["symbol_qty_map"] = symbol_qty_map
    _save_runtime_overrides(data)


def _effective_strategy(
    *,
    env_dry_run: bool,
    env_allow_live: bool,
    env_default_symbol: str,
) -> Dict[str, Any]:
    rt = _load_runtime_overrides()
    dry_run = bool(rt["dry_run"]) if "dry_run" in rt else env_dry_run
    allow_live = bool(rt["allow_live_orders"]) if "allow_live_orders" in rt else env_allow_live
    default_symbol = str(rt.get("default_symbol") or env_default_symbol).upper()
    return {
        "trading_paused": bool(rt.get("trading_paused", False)),
        "dry_run": dry_run,
        "allow_live_orders": allow_live,
        "default_symbol": default_symbol,
        "mode": "futures_reverse",
        "has_runtime_overrides": bool(rt),
    }


def _merged_symbol_qty_map(base: Dict[str, float]) -> Dict[str, float]:
    merged = dict(base)
    merged.update(_load_runtime_symbol_qty_map())
    return merged


def _dashboard_token() -> str:
    return os.getenv("DASHBOARD_TOKEN", "").strip()


def _product_name() -> str:
    return os.getenv("PRODUCT_NAME", "SignalRail").strip() or "SignalRail"


def _public_base_url() -> str:
    host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host", "")
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    if host:
        return f"{proto}://{host}".rstrip("/")
    return ""


def _render_static_page(filename: str) -> Response:
    html_path = BOT_DIR / filename
    if not html_path.exists():
        return Response(f"{filename} bulunamadı", status=500, mimetype="text/plain")
    boot = json.dumps(
        {
            "product_name": _product_name(),
            "public_base_url": _public_base_url(),
            "api_base_url": os.getenv("PUBLIC_API_BASE_URL", "").strip() or _public_base_url(),
        },
        ensure_ascii=False,
    )
    html = html_path.read_text(encoding="utf-8")
    html = html.replace("__PRODUCT_NAME__", _product_name())
    # Tek placeholder: __BOOTSTRAP__ global replace window.__BOOTSTRAP__ JS'i bozar.
    html = html.replace("%%DASHBOARD_BOOT_JSON%%", boot)
    return Response(html, mimetype="text/html; charset=utf-8")


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
    default_symbol = os.getenv("SYMBOL", "BTCUSDT").upper()
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

    client = FuturesClient(api_key=api_key, api_secret=api_secret, base_url=base_url)
    app = Flask(__name__)
    cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

    def _apply_cors(resp: Response) -> Response:
        origin = request.headers.get("Origin")
        if not origin or not cors_origins:
            return resp
        if origin in cors_origins or "*" in cors_origins:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Dashboard-Token"
            )
            resp.headers["Access-Control-Allow-Methods"] = "GET, PUT, DELETE, OPTIONS"
            resp.headers["Vary"] = "Origin"
        return resp

    @app.before_request
    def cors_preflight() -> Optional[Response]:
        if request.method == "OPTIONS" and request.path.startswith("/api/dashboard"):
            return _apply_cors(Response("", status=204))
        return None

    @app.after_request
    def cors_after(resp: Response) -> Response:
        if request.path.startswith("/api/dashboard"):
            return _apply_cors(resp)
        return resp

    def strategy_cfg() -> Dict[str, Any]:
        return _effective_strategy(
            env_dry_run=dry_run,
            env_allow_live=allow_live_orders,
            env_default_symbol=default_symbol,
        )

    @app.get("/health")
    def health() -> Any:
        cfg = strategy_cfg()
        qty_map = _merged_symbol_qty_map(symbol_qty_map)
        return jsonify(
            {
                "ok": True,
                "dry_run": cfg["dry_run"],
                "allow_live_orders": cfg["allow_live_orders"],
                "trading_paused": cfg["trading_paused"],
                "base_url": base_url,
                "mode": cfg["mode"],
                "symbol_qty_map": qty_map,
                "symbol_qty_runtime_overrides": bool(_load_runtime_symbol_qty_map()),
            }
        )

    @app.get("/")
    def root() -> Any:
        return redirect("/dashboard", code=302)

    @app.get("/dashboard")
    def dashboard_page() -> Any:
        if not _dashboard_token():
            return Response(
                "<!DOCTYPE html><html lang='tr'><head><meta charset='utf-8'>"
                "<title>Panel kapalı</title></head><body style='font-family:system-ui;"
                "max-width:520px;margin:48px auto;padding:0 20px'>"
                "<h1>Panel henüz etkin değil</h1>"
                "<p><code>/root/bot/.env</code> dosyasına <code>DASHBOARD_TOKEN</code> ekleyip "
                "<code>systemctl restart tradebot</code> çalıştırın.</p>"
                "</body></html>",
                status=503,
                mimetype="text/html; charset=utf-8",
            )
        return _render_static_page("dashboard_static.html")

    @app.get("/api/dashboard/status")
    def dashboard_status() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        cfg = strategy_cfg()
        rt = _load_runtime_symbol_qty_map()
        merged = _merged_symbol_qty_map(symbol_qty_map)
        return jsonify(
            {
                "ok": True,
                "server_time_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "dry_run": cfg["dry_run"],
                "allow_live_orders": cfg["allow_live_orders"],
                "trading_paused": cfg["trading_paused"],
                "default_symbol": cfg["default_symbol"],
                "base_url": base_url,
                "mode": cfg["mode"],
                "symbol_qty_env": symbol_qty_map,
                "symbol_qty_runtime": rt,
                "symbol_qty_merged": merged,
                "env_dry_run": dry_run,
                "env_allow_live_orders": allow_live_orders,
                "kline_interval": os.getenv("KLINE_INTERVAL", "5m").strip() or "5m",
                "signal_symbols": os.getenv("SIGNAL_SYMBOLS", "").strip(),
                "short_window": os.getenv("SHORT_WINDOW", "7").strip() or "7",
                "long_window": os.getenv("LONG_WINDOW", "25").strip() or "25",
            }
        )

    @app.get("/api/dashboard/account")
    def dashboard_account() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        try:
            summary = client.get_account_summary()
            return jsonify(
                {
                    "ok": True,
                    "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "base_url": base_url,
                    "is_demo": "demo" in base_url.lower(),
                    **summary,
                }
            )
        except Exception as exc:
            log.exception("Dashboard account error: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/dashboard/positions")
    def dashboard_positions() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        try:
            positions = client.get_open_positions()
            open_orders = client.get_open_orders()
            return jsonify(
                {
                    "ok": True,
                    "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "positions": positions,
                    "open_orders": [
                        {
                            "symbol": o.get("symbol"),
                            "side": o.get("side"),
                            "type": o.get("type"),
                            "orig_qty": o.get("origQty"),
                            "price": o.get("price"),
                            "status": o.get("status"),
                        }
                        for o in open_orders
                    ],
                }
            )
        except Exception as exc:
            log.exception("Dashboard positions error: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/dashboard/activity")
    def dashboard_activity() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        try:
            limit = int(request.args.get("limit", "80"))
        except ValueError:
            limit = 80
        items = read_activity(limit=limit)
        return jsonify(
            {
                "ok": True,
                "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "items": items,
            }
        )

    @app.get("/api/dashboard/klines")
    def dashboard_klines() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        raw_sym = str(request.args.get("symbol", "") or default_symbol).strip().upper()
        symbol = raw_sym or default_symbol
        interval = str(request.args.get("interval", "1m")).strip() or "1m"
        try:
            limit = int(request.args.get("limit", "300"))
        except ValueError:
            limit = 300
        limit = max(50, min(limit, 500))
        short_window = int(os.getenv("SHORT_WINDOW", "7") or "7")
        long_window = int(os.getenv("LONG_WINDOW", "25") or "25")
        include_ma = request.args.get("include_ma", "1").lower() not in {"0", "false", "no"}
        include_crosses = request.args.get("include_crosses", "1").lower() not in {
            "0",
            "false",
            "no",
        }
        include_bot = request.args.get("include_bot", "1").lower() not in {"0", "false", "no"}
        try:
            candles = client.get_klines(symbol, interval=interval, limit=limit)
            entry_price = None
            side = None
            mark_price = None
            for p in client.get_open_positions():
                if p.get("symbol") == symbol:
                    entry_price = p.get("entry_price")
                    side = p.get("side")
                    mark_price = p.get("mark_price")
                    break
            closes = [float(c["close"]) for c in candles]
            last_price = closes[-1] if closes else None
            ma_short_last = ma_long_last = None
            current_signal = "HOLD"
            if len(closes) >= long_window + 1:
                try:
                    ma_short_last = sma(closes, short_window)
                    ma_long_last = sma(closes, long_window)
                    current_signal = signal_from_closes(closes, short_window, long_window)
                except ValueError:
                    pass

            activities = read_activity(limit=400)
            bot_markers, bot_events = markers_from_activity(
                activities, symbol, candles, interval
            )
            ma_crosses = (
                historical_ma_signals(candles, short_window, long_window)
                if include_crosses
                else []
            )
            cross_markers = markers_from_ma_crosses(ma_crosses) if include_crosses else []

            markers: List[Dict[str, Any]] = []
            if include_crosses:
                markers.extend(cross_markers)
            if include_bot:
                markers.extend(bot_markers)
            markers.sort(key=lambda m: int(m.get("time", 0)))

            ma_short_series = ma_line_series(candles, short_window) if include_ma else []
            ma_long_series = ma_line_series(candles, long_window) if include_ma else []

            cross_events = [
                {
                    "id": f"ma-{int(r['time'])}-{r['action']}",
                    "time": int(r["time"]),
                    "ts": "",
                    "action": r["action"],
                    "event": "ma_cross",
                    "event_label": event_type_label("ma_cross"),
                    "status": "theoretical",
                    "status_label": status_label("theoretical", "ma_cross"),
                    "message": (
                        f"Kısa MA ({short_window}) uzun MA ({long_window}) "
                        f"kesişimi — {'yukarı (AL)' if r['action'] == 'BUY' else 'aşağı (SAT)'}"
                    ),
                    "price": r.get("price"),
                    "quantity": None,
                    "source": "strategy",
                    "kind": "ma_cross",
                }
                for r in ma_crosses
            ]
            merged_events = cross_events + bot_events
            signal_events = [
                _enrich_signal_event(
                    ev,
                    symbol=symbol,
                    interval=interval,
                    short_window=short_window,
                    long_window=long_window,
                    ma_short_series=ma_short_series,
                    ma_long_series=ma_long_series,
                )
                for ev in sorted(
                    merged_events,
                    key=lambda e: int(e.get("time", 0)),
                    reverse=True,
                )[:150]
            ]

            return jsonify(
                {
                    "ok": True,
                    "symbol": symbol,
                    "interval": interval,
                    "candles": candles,
                    "markers": markers,
                    "marker_count": len(markers),
                    "bot_marker_count": len(bot_markers),
                    "cross_marker_count": len(cross_markers),
                    "signal_events": signal_events,
                    "ma_short": ma_short_series,
                    "ma_long": ma_long_series,
                    "ma_short_last": ma_short_last,
                    "ma_long_last": ma_long_last,
                    "short_window": short_window,
                    "long_window": long_window,
                    "current_signal": current_signal,
                    "entry_price": entry_price,
                    "mark_price": mark_price,
                    "position_side": side,
                    "last_price": last_price,
                    "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            )
        except Exception as exc:
            log.exception("Dashboard klines error: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/dashboard/strategy")
    def dashboard_strategy_get() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        rt = _load_runtime_overrides()
        cfg = strategy_cfg()
        return jsonify(
            {
                "ok": True,
                "effective": cfg,
                "runtime": {
                    "trading_paused": rt.get("trading_paused"),
                    "dry_run": rt.get("dry_run"),
                    "allow_live_orders": rt.get("allow_live_orders"),
                    "default_symbol": rt.get("default_symbol"),
                    "symbol_qty_map": _load_runtime_symbol_qty_map(),
                },
                "env": {
                    "dry_run": dry_run,
                    "allow_live_orders": allow_live_orders,
                    "default_symbol": default_symbol,
                    "symbol_qty_map": symbol_qty_map,
                },
            }
        )

    @app.put("/api/dashboard/strategy")
    def dashboard_strategy_put() -> Any:
        err = _dashboard_auth_error()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        data = _load_runtime_overrides()
        if "trading_paused" in body:
            data["trading_paused"] = bool(body["trading_paused"])
        if "dry_run" in body:
            if body["dry_run"] is None:
                data.pop("dry_run", None)
            else:
                data["dry_run"] = bool(body["dry_run"])
        if "allow_live_orders" in body:
            if body["allow_live_orders"] is None:
                data.pop("allow_live_orders", None)
            else:
                data["allow_live_orders"] = bool(body["allow_live_orders"])
        if "default_symbol" in body:
            sym = str(body["default_symbol"]).strip().upper()
            if sym:
                data["default_symbol"] = sym
        raw_map = body.get("symbol_qty_map")
        if raw_map is not None:
            if not isinstance(raw_map, dict):
                return jsonify({"ok": False, "error": "symbol_qty_map must be object"}), 400
            try:
                data["symbol_qty_map"] = {str(k).upper(): float(v) for k, v in raw_map.items()}
            except Exception:
                return jsonify({"ok": False, "error": "invalid symbol_qty_map"}), 400
        _save_runtime_overrides(data)
        cfg = strategy_cfg()
        log.info(
            "Dashboard strategy update | paused=%s dry_run=%s live=%s default=%s",
            cfg["trading_paused"],
            cfg["dry_run"],
            cfg["allow_live_orders"],
            cfg["default_symbol"],
        )
        return jsonify({"ok": True, "effective": cfg})

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
    port = int(os.getenv("PANEL_PORT", os.getenv("WEBHOOK_PORT", "8080")))
    app.run(host="0.0.0.0", port=port)
