"""Panel için son işlem / webhook kayıtları (JSONL, paylaşımlı dosya)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

BOT_DIR = Path(__file__).resolve().parent
ACTIVITY_PATH = BOT_DIR / "activity_log.jsonl"
MAX_LINES = 400
_lock = Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_activity(
    *,
    source: str,
    event: str,
    symbol: str = "",
    action: str = "",
    status: str = "",
    message: str = "",
    ok: Optional[bool] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    row: Dict[str, Any] = {
        "ts": _now_iso(),
        "source": source,
        "event": event,
        "symbol": symbol.upper() if symbol else "",
        "action": action.upper() if action else "",
        "status": status,
        "message": message[:500] if message else "",
    }
    if ok is not None:
        row["ok"] = bool(ok)
    if extra:
        row["extra"] = extra
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
        ACTIVITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ACTIVITY_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
        _trim_locked()


def _trim_locked() -> None:
    if not ACTIVITY_PATH.exists():
        return
    lines = ACTIVITY_PATH.read_text(encoding="utf-8").splitlines()
    if len(lines) <= MAX_LINES:
        return
    keep = lines[-MAX_LINES:]
    ACTIVITY_PATH.write_text("\n".join(keep) + "\n", encoding="utf-8")


def read_activity(limit: int = 80) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), MAX_LINES))
    if not ACTIVITY_PATH.exists():
        return []
    lines = ACTIVITY_PATH.read_text(encoding="utf-8").splitlines()
    out: List[Dict[str, Any]] = []
    for raw in lines[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


_QTY_RE = re.compile(r"qty\s*=\s*([\d.]+)", re.IGNORECASE)


def parse_qty_from_message(message: str) -> Optional[float]:
    m = _QTY_RE.search(message or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def event_type_label(event: str) -> str:
    labels = {
        "signal_change": "Sinyal değişimi",
        "order": "Emir sonucu",
        "received": "Webhook alındı",
        "ma_cross": "MA kesişimi",
    }
    return labels.get(event, event or "—")


def status_label(status: str, event: str) -> str:
    labels = {
        "trigger": "Tetiklendi",
        "filled": "Gerçekleşti",
        "skipped": "Atlandı",
        "error": "Hata",
        "theoretical": "Teorik (grafik)",
        "ok": "Tamam",
    }
    if status in labels:
        return labels[status]
    if event == "ma_cross":
        return "Teorik kesişim"
    return status or "—"


def _parse_ts_iso(ts: str) -> Optional[int]:
    try:
        raw = str(ts).strip().replace("Z", "+00:00")
        return int(datetime.fromisoformat(raw).timestamp())
    except (TypeError, ValueError):
        return None


def snap_to_bar(ts_sec: int, candle_times: List[int], interval_sec: int) -> Optional[int]:
    if not candle_times:
        return None
    for i, t in enumerate(candle_times):
        end = candle_times[i + 1] if i + 1 < len(candle_times) else t + interval_sec
        if t <= ts_sec < end:
            return t
    if ts_sec >= candle_times[-1]:
        return candle_times[-1]
    if ts_sec < candle_times[0]:
        return candle_times[0]
    return None


def markers_from_activity(
    activities: List[Dict[str, Any]],
    symbol: str,
    candles: List[Dict[str, Any]],
    interval: str,
) -> List[Dict[str, Any]]:
    """lightweight-charts setMarkers() için al/sat işaretleri."""
    sym = symbol.upper()
    interval_sec = INTERVAL_SECONDS.get(interval, 60)
    candle_times = [int(c["time"]) for c in candles]
    if not candle_times:
        return []

    priority = {"signal_change": 3, "received": 2, "order": 1}
    best: Dict[tuple, Dict[str, Any]] = {}

    for item in activities:
        if (item.get("symbol") or "").upper() != sym:
            continue
        action = (item.get("action") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue
        ev = item.get("event") or ""
        if ev not in {"signal_change", "received", "order"}:
            continue
        if ev == "order" and item.get("status") == "error":
            continue

        ts_sec = _parse_ts_iso(str(item.get("ts", "")))
        if ts_sec is None:
            continue
        bar_time = snap_to_bar(ts_sec, candle_times, interval_sec)
        if bar_time is None:
            continue

        key = (bar_time, action)
        score = priority.get(ev, 0)
        if key not in best or score > best[key]["_score"]:
            best[key] = {**item, "bar_time": bar_time, "_score": score}

    markers: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    for (bar_time, action) in sorted(best.keys(), key=lambda k: k[0]):
        item = best[(bar_time, action)]
        ev = item.get("event") or ""
        st = item.get("status") or ""
        is_buy = action == "BUY"
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        price = extra.get("price")
        price_txt = f" @{float(price):.4g}" if price is not None else ""

        if ev == "signal_change":
            label = ("AL" if is_buy else "SAT") + price_txt
            color = "#34d399" if is_buy else "#f87171"
        elif ev == "received":
            label = ("TV AL" if is_buy else "TV SAT") + price_txt
            color = "#22d3ee" if is_buy else "#fbbf24"
        else:
            label = ("Al" if is_buy else "Sat") + price_txt
            if st == "filled":
                label += " ✓"
            color = "#34d399" if is_buy else "#f87171"
            if st == "skipped":
                color = "#8b95a8"
                label += " ⊘"

        markers.append(
            {
                "time": bar_time,
                "position": "belowBar" if is_buy else "aboveBar",
                "color": color,
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": label,
                "size": 2,
            }
        )
        msg = item.get("message", "") or ""
        qty = parse_qty_from_message(msg)
        events.append(
            {
                "id": f"bot-{bar_time}-{action}",
                "time": bar_time,
                "ts": item.get("ts", ""),
                "action": action,
                "event": ev,
                "event_label": event_type_label(ev),
                "status": st,
                "status_label": status_label(st, ev),
                "message": msg,
                "price": price,
                "quantity": qty,
                "source": item.get("source", ""),
                "kind": "bot",
                "ok": item.get("ok"),
            }
        )
    return markers, events


def markers_from_ma_crosses(
    crosses: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Teorik MA kesişimleri — daha küçük, yarı saydam işaretler."""
    markers: List[Dict[str, Any]] = []
    for row in crosses:
        action = (row.get("action") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue
        is_buy = action == "BUY"
        price = row.get("price")
        price_txt = f" {float(price):.4g}" if price is not None else ""
        markers.append(
            {
                "time": int(row["time"]),
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "rgba(52, 211, 153, 0.55)" if is_buy else "rgba(248, 113, 113, 0.55)",
                "shape": "circle",
                "text": ("AL" if is_buy else "SAT") + price_txt,
                "size": 1,
            }
        )
    return markers


def summarize_order_result(result: Dict[str, Any]) -> tuple[str, str, bool]:
    if result.get("skipped"):
        return "skipped", str(result.get("reason", "skipped")), True
    if not result.get("ok"):
        return "error", str(result.get("error", "failed")), False
    orders = result.get("orders") or []
    parts = []
    for o in orders:
        r = o.get("result") or {}
        parts.append(f"{o.get('type', '?')}:{r.get('status', '?')}")
    return "filled", " · ".join(parts) if parts else "ok", True
