"""Pine-compatible EMA / ATR and the JSON number format used by candle_to_json.pine.

ta.ema / ta.atr (Wilder RMA) both seed with an SMA of the first `length` values,
then recurse. First true range is high-low because previous close is unavailable.
"""

from __future__ import annotations

from typing import Optional


def format_num(x: float) -> str:
    """Match Pine str.tostring(x, \"#.########\"): up to 8 decimals, trim trailing zeros."""
    if x != x:  # NaN
        raise ValueError("cannot format NaN")
    s = f"{x:.8f}"
    s = s.rstrip("0").rstrip(".")
    if s in ("-0", "+0"):
        s = "0"
    return s


def ema_series(values: list[float], length: int) -> list[Optional[float]]:
    if length < 1:
        raise ValueError("length must be >= 1")
    alpha = 2.0 / (length + 1)
    out: list[Optional[float]] = [None] * len(values)
    seed = 0.0
    for i, v in enumerate(values):
        if i < length - 1:
            seed += v
            continue
        if i == length - 1:
            seed += v
            out[i] = seed / length
        else:
            prev = out[i - 1]
            assert prev is not None
            out[i] = alpha * v + (1.0 - alpha) * prev
    return out


def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    n = len(closes)
    if not (len(highs) == len(lows) == n):
        raise ValueError("high/low/close length mismatch")
    tr = [0.0] * n
    if n:
        tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return tr


def rma_series(values: list[float], length: int) -> list[Optional[float]]:
    """Wilder's moving average, same seeding as Pine ta.rma / ta.atr."""
    if length < 1:
        raise ValueError("length must be >= 1")
    alpha = 1.0 / length
    out: list[Optional[float]] = [None] * len(values)
    seed = 0.0
    for i, v in enumerate(values):
        if i < length - 1:
            seed += v
            continue
        if i == length - 1:
            seed += v
            out[i] = seed / length
        else:
            prev = out[i - 1]
            assert prev is not None
            out[i] = alpha * v + (1.0 - alpha) * prev
    return out


def atr_series(
    highs: list[float], lows: list[float], closes: list[float], length: int = 14
) -> list[Optional[float]]:
    return rma_series(true_range(highs, lows, closes), length)


REQUIRED_FIELDS = ("symbol", "tf", "time", "o", "h", "l", "c", "v", "ema20", "ema50", "atr14")


def bar_payload(
    *,
    symbol: str,
    tf: str,
    time_ms: int,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float,
    ema20: float,
    ema50: float,
    atr14: float,
) -> dict:
    # Keep numeric types in the Python object; JSON serialization uses format_num
    # only when we need a Pine-identical compact string.
    return {
        "symbol": symbol,
        "tf": tf,
        "time": int(time_ms),
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
        "ema20": ema20,
        "ema50": ema50,
        "atr14": atr14,
    }


def to_pine_json(bar: dict) -> str:
    """Compact JSON string identical to the Pine f_json() builder."""
    parts = [
        f'"symbol":"{bar["symbol"]}"',
        f'"tf":"{bar["tf"]}"',
        f'"time":{int(bar["time"])}',
    ]
    for key in ("o", "h", "l", "c", "v", "ema20", "ema50", "atr14"):
        parts.append(f'"{key}":{format_num(float(bar[key]))}')
    return "{" + ",".join(parts) + "}"
