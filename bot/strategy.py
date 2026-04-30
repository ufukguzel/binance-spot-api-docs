from typing import List


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
