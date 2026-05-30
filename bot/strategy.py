from typing import Any, Dict, List


def sma(values: List[float], window: int) -> float:
    if len(values) < window:
        raise ValueError(f"Not enough values for SMA({window})")
    subset = values[-window:]
    return sum(subset) / window


def signal_from_closes(closes: List[float], short_window: int, long_window: int) -> str:
    if short_window >= long_window:
        raise ValueError("SHORT_WINDOW must be smaller than LONG_WINDOW")
    if len(closes) < long_window + 1:
        return "HOLD"

    prev_closes = closes[:-1]
    short_prev = sma(prev_closes, short_window)
    long_prev = sma(prev_closes, long_window)
    short_now = sma(closes, short_window)
    long_now = sma(closes, long_window)

    if short_prev <= long_prev and short_now > long_now:
        return "BUY"
    if short_prev >= long_prev and short_now < long_now:
        return "SELL"
    return "HOLD"


def ma_line_series(candles: List[Dict[str, Any]], window: int) -> List[Dict[str, Any]]:
    """lightweight-charts line series: [{time, value}, ...]"""
    if window < 1 or len(candles) < window:
        return []
    closes = [float(c["close"]) for c in candles]
    out: List[Dict[str, Any]] = []
    for i in range(window - 1, len(candles)):
        avg = sum(closes[i - window + 1 : i + 1]) / window
        out.append({"time": int(candles[i]["time"]), "value": avg})
    return out


def historical_ma_signals(
    candles: List[Dict[str, Any]], short_window: int, long_window: int
) -> List[Dict[str, Any]]:
    """Grafikteki tüm MA kesişim noktaları (teorik al/sat)."""
    if short_window >= long_window or len(candles) < long_window + 2:
        return []
    closes = [float(c["close"]) for c in candles]
    out: List[Dict[str, Any]] = []
    prev = "HOLD"
    for i in range(long_window + 1, len(candles)):
        sig = signal_from_closes(closes[: i + 1], short_window, long_window)
        if sig in {"BUY", "SELL"} and sig != prev:
            out.append(
                {
                    "time": int(candles[i]["time"]),
                    "action": sig,
                    "price": closes[i],
                    "kind": "ma_cross",
                }
            )
            prev = sig
    return out
