#!/usr/bin/env python3
"""Local TradingView-style webhook receiver.

Accepts one closed-bar JSON object per POST, appends it to a jsonl log,
keeps the last 5 bars per (symbol, tf), and assembles a researcher prompt.

This process never places orders, never sizes a position, and never calls
an exchange trading API. It only writes paper research files.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, request, Response

from indicators import REQUIRED_FIELDS, to_pine_json

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PROMPT_TEMPLATE = ROOT / "prompts" / "researcher.md"
ALERTS_LOG = DATA / "alerts.jsonl"
PROMPT_LOG = DATA / "prompt_log.jsonl"
LAST_PROMPT = DATA / "last_prompt.md"
LAST_PAYLOAD = DATA / "last_payload.json"
WINDOW = 5
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

app = Flask(__name__)
_lock = Lock()
_windows: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=WINDOW))


def now_sh() -> datetime:
    return datetime.now(SHANGHAI)


def iso_sh(dt: datetime | None = None) -> str:
    return (dt or now_sh()).strftime("%Y-%m-%d %H:%M:%S %Z")


def ms_to_sh(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=SHANGHAI).strftime("%Y-%m-%d %H:%M:%S %Z")


def log(msg: str) -> None:
    line = f"[{iso_sh()}] {msg}"
    print(line, flush=True)


def parse_bar(raw) -> dict:
    if raw is None:
        raise ValueError("empty body")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("empty body")
        data = json.loads(text)
    elif isinstance(raw, dict):
        data = raw
    else:
        raise ValueError("body must be a JSON object")
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    missing = [k for k in REQUIRED_FIELDS if k not in data]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    bar = {
        "symbol": str(data["symbol"]),
        "tf": str(data["tf"]),
        "time": int(data["time"]),
        "o": float(data["o"]),
        "h": float(data["h"]),
        "l": float(data["l"]),
        "c": float(data["c"]),
        "v": float(data["v"]),
        "ema20": float(data["ema20"]),
        "ema50": float(data["ema50"]),
        "atr14": float(data["atr14"]),
    }
    if bar["h"] < max(bar["o"], bar["c"]) or bar["l"] > min(bar["o"], bar["c"]):
        raise ValueError("inconsistent OHLC")
    return bar


def assemble_prompt(symbol: str, tf: str, bars: list[dict], received_at: str) -> tuple[str, dict]:
    template = PROMPT_TEMPLATE.read_text(encoding="utf-8").rstrip()
    compact_bars = []
    for b in bars:
        compact_bars.append(
            {
                "symbol": b["symbol"],
                "tf": b["tf"],
                "time": int(b["time"]),
                "time_shanghai": ms_to_sh(int(b["time"])),
                "o": b["o"],
                "h": b["h"],
                "l": b["l"],
                "c": b["c"],
                "v": b["v"],
                "ema20": b["ema20"],
                "ema50": b["ema50"],
                "atr14": b["atr14"],
            }
        )
    payload = {
        "symbol": symbol,
        "tf": tf,
        "bar_count": len(compact_bars),
        "bars": compact_bars,
        "received_at": received_at,
        "note": "bars are closed candles only; this payload is for paper research, never an order",
    }
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    prompt = (
        template
        + "\n\n# 本次数据\n\n"
        + f"生成时间：{received_at}\n"
        + f"品种：{symbol}  周期：{tf}  已收盘根数：{len(compact_bars)}\n\n"
        + "```json\n"
        + payload_text
        + "\n```\n"
        + "\n请只按上面的输出格式作答。不要下单，不要给仓位，不要写开仓价或止损止盈。\n"
    )
    return prompt, payload


@app.get("/health")
def health():
    with _lock:
        n = {f"{s}:{t}": len(v) for (s, t), v in _windows.items()}
    return {"ok": True, "windows": n, "time": iso_sh()}


@app.post("/alert")
def alert():
    received_at = iso_sh()
    try:
        raw = request.get_data(as_text=True)
        bar = parse_bar(raw if raw else request.get_json(silent=True))
    except (ValueError, json.JSONDecodeError, TypeError, KeyError) as e:
        log(f"POST /alert  400  {e}")
        return jsonify({"ok": False, "error": str(e)}), 400

    key = (bar["symbol"], bar["tf"])
    with _lock:
        window = _windows[key]
        # If the same closed bar is resent, replace it instead of duplicating.
        if window and window[-1]["time"] == bar["time"]:
            window[-1] = bar
            replaced = True
        else:
            window.append(bar)
            replaced = False
        bars = list(window)
        prompt, payload = assemble_prompt(bar["symbol"], bar["tf"], bars, received_at)

        DATA.mkdir(parents=True, exist_ok=True)
        record = {
            "received_at": received_at,
            "replaced": replaced,
            "bar": bar,
            "pine_json": to_pine_json(bar),
            "window_size": len(bars),
        }
        with ALERTS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        LAST_PROMPT.write_text(prompt, encoding="utf-8")
        LAST_PAYLOAD.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with PROMPT_LOG.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "received_at": received_at,
                        "symbol": bar["symbol"],
                        "tf": bar["tf"],
                        "window_size": len(bars),
                        "prompt_path": str(LAST_PROMPT),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    log(
        "POST /alert  200  "
        f"{bar['symbol']} {bar['tf']}  "
        f"time={ms_to_sh(bar['time'])}  "
        f"c={bar['c']}  ema20={bar['ema20']:.2f}  ema50={bar['ema50']:.2f}  "
        f"atr14={bar['atr14']:.2f}  bars_kept={len(bars)}"
        + ("  replaced" if replaced else "")
    )
    log(f"assembled prompt -> {LAST_PROMPT}  ({len(bars)} bars, paper research only, never order)")
    return jsonify(
        {
            "ok": True,
            "replaced": replaced,
            "window_size": len(bars),
            "prompt_path": str(LAST_PROMPT),
            "payload_path": str(LAST_PAYLOAD),
        }
    )


@app.get("/last-prompt")
def last_prompt():
    if not LAST_PROMPT.exists():
        return jsonify({"ok": False, "error": "no prompt yet"}), 404
    return Response(LAST_PROMPT.read_text(encoding="utf-8"), mimetype="text/markdown; charset=utf-8")


@app.get("/last-payload")
def last_payload():
    if not LAST_PAYLOAD.exists():
        return jsonify({"ok": False, "error": "no payload yet"}), 404
    return Response(LAST_PAYLOAD.read_text(encoding="utf-8"), mimetype="application/json")


@app.get("/bars")
def bars():
    symbol = request.args.get("symbol")
    tf = request.args.get("tf")
    with _lock:
        if symbol and tf:
            data = list(_windows.get((symbol, tf), []))
        else:
            data = {f"{s}:{t}": list(v) for (s, t), v in _windows.items()}
    return jsonify(data)


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("CANDLEJSON_HOST", "127.0.0.1")
    port = int(os.environ.get("CANDLEJSON_PORT", "8765"))
    log(f"receiver listening on http://{host}:{port}/alert")
    log("paper research only; this process never places an order")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
