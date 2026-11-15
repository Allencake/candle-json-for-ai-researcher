#!/usr/bin/env python3
"""candle-json/v2 接收端：收数据包 → 拼提示词 → （可选）问 AI 研究员 → 记纸面日志。

    python receiver.py --port 8080 --token 你的口令
    python receiver.py --replay examples/replay_packets.jsonl     # 不开服务，直接回放

- 设置了 ANTHROPIC_API_KEY 和 CJ_MODEL 才会真的调用模型，否则走 dry-run（本地规则占位，结果会标明 source=dry-run）。
- 这个程序没有任何下单代码，不连交易所账户。纸面账本只是一份 json 文件。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PROMPT_FILE = ROOT / "prompts" / "researcher.md"
SCHEMA = "candle-json/v2"
TZ = timezone(timedelta(hours=8))  # 日志按北京时间显示，可用 CJ_TZ_HOURS 改
if os.getenv("CJ_TZ_HOURS"):
    TZ = timezone(timedelta(hours=float(os.environ["CJ_TZ_HOURS"])))

_lock = threading.Lock()
_recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))


# ---------------- 小工具 ----------------
def now_txt() -> str:
    return datetime.now(TZ).strftime("%m-%d %H:%M")


def bar_txt(p: dict) -> str:
    t = datetime.strptime(p["bar_close_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return t.astimezone(TZ).strftime("%m-%d %H:%M")


def key_of(p: dict) -> str:
    return f"{p['symbol'].split(':')[-1]}_{p['tf']}"


def log(p: dict | None, tag: str, msg: str) -> None:
    stamp = bar_txt(p) if p else now_txt()
    print(f"[{stamp}] {tag:<8} {msg}", flush=True)


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def validate(p: dict) -> None:
    if p.get("schema") != SCHEMA:
        raise ValueError(f"schema 应为 {SCHEMA}")
    for k in ("event", "symbol", "tf", "bar_close_utc", "bar", "last5", "score", "paper"):
        if k not in p:
            raise ValueError(f"缺字段 {k}")


# ---------------- 存包 ----------------
def store(p: dict) -> None:
    pk = DATA / "packets"
    (pk / "latest").mkdir(parents=True, exist_ok=True)
    txt = json.dumps(p, ensure_ascii=False, indent=2)
    (pk / "latest.json").write_text(txt, encoding="utf-8")
    (pk / "latest" / f"{key_of(p)}.json").write_text(txt, encoding="utf-8")
    append_jsonl(pk / "history.jsonl", p)


# ---------------- 拼提示词 ----------------
def build_prompt(p: dict) -> tuple[str, str]:
    system = PROMPT_FILE.read_text(encoding="utf-8")
    prev = [x["summary"] for x in list(_recent[key_of(p)])[:-1]]
    user = "最近几次数据包摘要（旧→新）：\n" + ("\n".join(prev) if prev else "（无）")
    user += "\n\n这一根收盘的数据包：\n```json\n" + json.dumps(p, ensure_ascii=False) + "\n```"
    return system, user


# ---------------- 研究员 ----------------
def ask_model(system: str, user: str) -> dict:
    key, model = os.getenv("ANTHROPIC_API_KEY"), os.getenv("CJ_MODEL")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": model, "max_tokens": 1200, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=90,
    )
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    out = json.loads(m.group(0)) if m else {"decision": "跳过", "candle_read": text[:200]}
    out["source"] = model
    return out


def dry_run(p: dict) -> dict:
    """没配 API 时的本地占位：只看数据包里现成的字段，不是 AI 的判断。"""
    sc, paper, ev = p["score"], p["paper"], p["event"]
    top = max(sc["long"], sc["short"])
    live = paper["status"] in ("OPEN", "T1_HIT")
    plan = None
    if ev.startswith("SETUP_") and p["regime"] != "HIGH_VOL":
        decision = "偏多" if ev == "SETUP_LONG" else "偏空"
        plan = {k: paper[k] for k in ("entry", "stop", "t1", "t2")}
    elif p["regime"] == "HIGH_VOL" or top < 40:
        decision = "跳过"
    else:
        decision = "观望"
    bar = p["bar"]
    read = f"{bar['pattern']}，收在区间 {bar['close_pos_pct']}% 位置，量比 {p['vol']['rel_volume']}"
    return {
        "decision": decision,
        "confidence": None,
        "paper_plan": plan,
        "candle_read": read,
        "reasons": [f"{p['regime']} · 多 {sc['long']} / 空 {sc['short']} · 大周期 {p['trend']['htf_bias']}"],
        "concerns": ["dry-run 只复述指标分数，没有做真正的研究"],
        "invalidation": f"触及纸面止损 {paper['stop']}" if (plan or live) else "",
        "why_wrong": "这是规则占位，不是模型判断",
        "missing_data": ["配置 ANTHROPIC_API_KEY 和 CJ_MODEL 后才有真正的研究结论"],
        "next_check": "下一根 K 线收盘",
        "note": "纸面研究，不下单，最终由我决定",
        "source": "dry-run",
    }


# ---------------- 纸面账本（只记账，不下单） ----------------
BOOK = DATA / "paper_book.json"


def load_book() -> dict:
    return json.loads(BOOK.read_text(encoding="utf-8")) if BOOK.exists() else {"open": {}, "closed": []}


def save_book(b: dict) -> None:
    BOOK.parent.mkdir(parents=True, exist_ok=True)
    BOOK.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")


def update_book(p: dict) -> list[str]:
    """用这根 K 线的高低点检查已记录的纸面计划。保守处理：同一根先看止损。"""
    b, k, msgs = load_book(), key_of(p), []
    t = b["open"].get(k)
    if t:
        h, l = p["bar"]["h"], p["bar"]["l"]
        sgn = 1 if t["side"] == "偏多" else -1
        hit_stop = (l <= t["stop"]) if sgn > 0 else (h >= t["stop"])
        hit_t2 = (h >= t["t2"]) if sgn > 0 else (l <= t["t2"])
        hit_t1 = (h >= t["t1"]) if sgn > 0 else (l <= t["t1"])
        if hit_stop:
            t["status"] = "保本离场" if t["t1_hit"] else "止损"
        elif hit_t2:
            t["status"] = "目标2到达"
        elif hit_t1 and not t["t1_hit"]:
            t["t1_hit"], t["stop"] = True, t["entry"]
            msgs.append(f"目标1到达 {t['t1']} → 纸面止损移到入场 {t['entry']}")
        if t["status"] != "进行中":
            t["closed_at"] = p["bar_close_utc"]
            b["closed"].append(t)
            del b["open"][k]
            msgs.append(f"纸面单结束：{t['status']}")
    save_book(b)
    return msgs


def open_paper(p: dict, res: dict) -> str | None:
    plan = res.get("paper_plan")
    if res.get("decision") not in ("偏多", "偏空") or not plan:
        return None
    b, k = load_book(), key_of(p)
    if k in b["open"]:
        return None
    b["open"][k] = {"side": res["decision"], "opened_at": p["bar_close_utc"], "status": "进行中", "t1_hit": False,
                    **{x: plan[x] for x in ("entry", "stop", "t1", "t2")}}
    save_book(b)
    return f"纸面记录 {res['decision']} · 入场 {plan['entry']} · 止损 {plan['stop']} · T1 {plan['t1']} · T2 {plan['t2']}"


# ---------------- 主流程 ----------------
def handle(p: dict) -> None:
    validate(p)
    with _lock:
        store(p)
        _recent[key_of(p)].append(p)
        sym = p["symbol"].split(":")[-1]
        sc = p["score"]
        log(p, "packet", f"{sym} {p['tf']} {p['event']} · {p['regime']} · 多{sc['long']}/空{sc['short']}")
        for m in update_book(p):
            log(p, "paper", m)
        system, user = build_prompt(p)
        (DATA / "last_prompt.md").write_text(f"<system>\n{system}\n</system>\n\n<user>\n{user}\n</user>\n", encoding="utf-8")
        use_model = bool(os.getenv("ANTHROPIC_API_KEY") and os.getenv("CJ_MODEL"))
        try:
            res = ask_model(system, user) if use_model else dry_run(p)
        except Exception as e:  # 模型出错也只记日志，不做任何动作
            res = dry_run(p)
            res["concerns"].append(f"模型调用失败：{e}")
        log(p, "decision", f"{res['decision']}" + (f" · 把握 {res['confidence']}" if res.get("confidence") is not None else "") + f" · {res.get('source')}")
        msg = open_paper(p, res)
        if msg:
            log(p, "paper", msg)
        append_jsonl(DATA / "journal.jsonl", {"bar_close_utc": p["bar_close_utc"], "symbol": p["symbol"], "tf": p["tf"],
                                              "event": p["event"], "summary": p["summary"], "research": res})


def serve(port: int, token: str | None) -> None:
    from flask import Flask, jsonify, request

    app = Flask(__name__)

    @app.post("/webhook")
    def webhook():
        if token and request.args.get("token") != token:
            return jsonify(ok=False, error="token"), 401
        try:
            p = json.loads(request.get_data(as_text=True))
            validate(p)
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 400
        # TradingView 3 秒超时：先回 200，研究放后台线程
        threading.Thread(target=handle, args=(p,), daemon=True).start()
        return jsonify(ok=True)

    @app.get("/health")
    def health():
        return jsonify(ok=True, schema=SCHEMA, model=bool(os.getenv("ANTHROPIC_API_KEY") and os.getenv("CJ_MODEL")))

    print(f"receiver listening on :{port}  (纸面研究，不下单)")
    app.run(host="0.0.0.0", port=port)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--token", default=os.getenv("CJ_TOKEN"))
    ap.add_argument("--replay", help="jsonl 文件，逐行当作数据包处理")
    a = ap.parse_args()
    if a.replay:
        for line in Path(a.replay).read_text(encoding="utf-8").splitlines():
            if line.strip():
                handle(json.loads(line))
        return
    serve(a.port, a.token)


if __name__ == "__main__":
    main()
