#!/usr/bin/env python3
"""从币安公开行情拉真实 K 线，用 lens.py 回放，生成 examples/ 下的样例。

    python tools/make_samples.py --symbol BTCUSDT --interval 1h

行情接口：data-api.binance.vision（公开只读，不需要账号和密钥）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lens  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TF_CODE = {"15m": ("15", 15), "1h": ("60", 60), "4h": ("240", 240)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1h", choices=TF_CODE)
    ap.add_argument("--klines", help="已有的 klines json 文件（可选）")
    a = ap.parse_args()
    if a.klines:
        rows = json.loads(Path(a.klines).read_text())
    else:
        r = requests.get("https://data-api.binance.vision/api/v3/klines",
                         params={"symbol": a.symbol, "interval": a.interval, "limit": 1000}, timeout=20)
        r.raise_for_status()
        rows = r.json()
    now_ms = int(time.time() * 1000)
    rows = [x for x in rows if int(x[6]) < now_ms]  # 只要已收盘
    tf, mins = TF_CODE[a.interval]
    pkts, paper = lens.compute(lens.bars_from_binance(rows), f"BINANCE:{a.symbol}", tf, mins)
    ex = ROOT / "examples"
    ex.mkdir(exist_ok=True)
    (ex / "sample_packet.json").write_text(json.dumps(pkts[-1], ensure_ascii=False, indent=2) + "\n")
    setups = [p for p in pkts if p["event"].startswith("SETUP_")]
    if setups:
        (ex / "sample_setup_packet.json").write_text(json.dumps(setups[-1], ensure_ascii=False, indent=2) + "\n")
    with (ex / "replay_packets.jsonl").open("w") as f:  # 最近 72 根，给 receiver --replay 用
        for p in pkts[-72:]:
            f.write(json.dumps(p, ensure_ascii=False, separators=(",", ":")) + "\n")
    with (ex / "replay_events.jsonl").open("w") as f:
        for p in pkts:
            if p["event"] != "BAR_CLOSE":
                f.write(json.dumps({"bar_close_utc": p["bar_close_utc"], "event": p["event"], "summary": p["summary"],
                                    "close": p["bar"]["c"], "paper": p["paper"]}, ensure_ascii=False) + "\n")
    print(f"bars={len(rows)} packets={len(pkts)} setups={len(setups)} last={pkts[-1]['bar_close_utc']} {pkts[-1]['summary']}")
    for h in paper.history:
        print("closed", h[0], h[1], lens.iso(h[2]), "->", lens.iso(h[3]))


if __name__ == "__main__":
    main()
