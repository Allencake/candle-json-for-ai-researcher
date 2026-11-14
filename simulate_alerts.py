#!/usr/bin/env python3
"""Fetch real Binance BTCUSDT 1h klines, compute Pine-identical indicators,
and POST each closed bar to the local receiver as if TradingView had fired alert().

This script does not call a model and does not place orders.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

from indicators import atr_series, bar_payload, ema_series, to_pine_json

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
# Official public kline path. api.binance.com may return HTTP 451 from
# some regions; data-api.binance.vision is Binance's public market-data host
# with the same /api/v3/klines contract.
BINANCE_KLINES_CANDIDATES = (
    "https://api.binance.com/api/v3/klines",
    "https://data-api.binance.vision/api/v3/klines",
)
# Pine timeframe.period for a 1-hour chart is "60".
TF = "60"
SYMBOL = "BTCUSDT"
INTERVAL = "1h"


def iso_sh(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=SHANGHAI).strftime("%Y-%m-%d %H:%M:%S %Z")


def fetch_klines(symbol: str, interval: str, limit: int) -> tuple[list[list], str]:
    last_err = None
    for base in BINANCE_KLINES_CANDIDATES:
        url = f"{base}?symbol={symbol}&interval={interval}&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "candle-json-sim/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, list) or not data:
                raise RuntimeError(f"unexpected klines response from {base}: {data!r}")
            return data, base
        except urllib.error.HTTPError as e:
            last_err = e
            print(f"  {base} -> HTTP {e.code}, trying next host", flush=True)
            continue
    raise RuntimeError(f"all kline hosts failed: {last_err}")


def closed_bars(raw: list[list], now_ms: int | None = None) -> list[dict]:
    """Drop the still-open candle. Binance close_time is inclusive end of the bar."""
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    rows = []
    for k in raw:
        open_ms = int(k[0])
        close_ms = int(k[6])
        if close_ms >= now_ms:
            continue
        rows.append(
            {
                "time": open_ms,
                "close_time": close_ms,
                "o": float(k[1]),
                "h": float(k[2]),
                "l": float(k[3]),
                "c": float(k[4]),
                "v": float(k[5]),
            }
        )
    return rows


def attach_indicators(rows: list[dict]) -> list[dict]:
    opens = [r["o"] for r in rows]
    highs = [r["h"] for r in rows]
    lows = [r["l"] for r in rows]
    closes = [r["c"] for r in rows]
    volumes = [r["v"] for r in rows]
    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50)
    atr14 = atr_series(highs, lows, closes, 14)
    out = []
    for i, r in enumerate(rows):
        if ema20[i] is None or ema50[i] is None or atr14[i] is None:
            continue
        out.append(
            bar_payload(
                symbol=SYMBOL,
                tf=TF,
                time_ms=r["time"],
                o=opens[i],
                h=highs[i],
                l=lows[i],
                c=closes[i],
                v=volumes[i],
                ema20=ema20[i],
                ema50=ema50[i],
                atr14=atr14[i],
            )
        )
    return out


def post_alert(url: str, pine_json: str) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        data=pine_json.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/alert")
    parser.add_argument("--limit", type=int, default=200, help="how many klines to fetch (includes warmup)")
    parser.add_argument("--post", type=int, default=8, help="how many most recent closed bars to POST")
    args = parser.parse_args()

    print(f"fetching {SYMBOL} {INTERVAL} klines  limit={args.limit}", flush=True)
    raw, source = fetch_klines(SYMBOL, INTERVAL, args.limit)
    print(f"source: {source}", flush=True)
    closed = closed_bars(raw)
    ready = attach_indicators(closed)
    if len(ready) < args.post:
        print(f"not enough closed bars with indicators: got {len(ready)}", file=sys.stderr)
        return 1

    to_send = ready[-args.post :]
    skipped_open = len(raw) - len(closed)
    print(
        f"fetched={len(raw)}  closed={len(closed)}  ready={len(ready)}  "
        f"dropped_open={skipped_open}  posting_last={len(to_send)}",
        flush=True,
    )
    print(
        f"window: {iso_sh(to_send[0]['time'])}  ->  {iso_sh(to_send[-1]['time'])}  (bar open times, Asia/Shanghai)",
        flush=True,
    )
    print("-----", flush=True)

    from pathlib import Path

    out_dir = Path(__file__).resolve().parent / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    sent_log = out_dir / "simulated_posts.jsonl"
    if sent_log.exists():
        sent_log.unlink()

    ok = 0
    for i, bar in enumerate(to_send, 1):
        body = to_pine_json(bar)
        status, resp = post_alert(args.url, body)
        print(f"[{i}/{len(to_send)}] {iso_sh(bar['time'])}  POST {status}  {body}", flush=True)
        if status != 200:
            print(f"  error: {resp}", flush=True)
        else:
            ok += 1
        with sent_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"status": status, "request": body, "response": resp}, ensure_ascii=False) + "\n")

    print("-----", flush=True)
    print(f"posted {ok}/{len(to_send)}  log: {sent_log}", flush=True)
    last_prompt = out_dir / "last_prompt.md"
    last_payload = out_dir / "last_payload.json"
    if last_payload.exists():
        print(f"\n=== last assembled payload ({last_payload}) ===\n", flush=True)
        print(last_payload.read_text(encoding="utf-8"), end="", flush=True)
    if last_prompt.exists():
        print(f"\n=== last assembled prompt ({last_prompt}) ===\n", flush=True)
        print(last_prompt.read_text(encoding="utf-8"), end="", flush=True)
    print(
        "\n\n[note] No model was called. The output above is the real pipeline: "
        "JSON received + assembled researcher prompt.",
        flush=True,
    )
    return 0 if ok == len(to_send) else 2


if __name__ == "__main__":
    raise SystemExit(main())
