#!/usr/bin/env python3
"""Python 参考实现：和 candle_json_v2.pine 用同一套规则，把收盘 K 线算成 candle-json/v2 数据包。

用途：
  1. 不开 TradingView 也能用真实 K 线回放，检查数据包长什么样；
  2. 给 examples/ 生成真实样例（不是编的）。

只做计算和纸面记账，不连交易所账户，不下单。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

SCHEMA = "candle-json/v2"

# ---- 默认参数（和 Pine 输入项保持一致） ----
EMA_FAST, EMA_MID, EMA_SLOW = 20, 50, 200
RSI_LEN, ADX_LEN, ATR_LEN = 14, 14, 14
VOL_LEN, ATR_RANK_LEN, HIGH_VOL_PCT = 20, 100, 90
SWING_LEN = 5
HTF_MINUTES, HTF_EMA = 240, 50
STOP_ATR, T1_R, T2_R = 1.5, 1.5, 3.0
THRESHOLD = 70
WINDOW = 5


# ---------------- 基础指标（按 Pine 的定义：SMA 起步再递推） ----------------
def ema(xs, n):
    out, acc, a = [None] * len(xs), None, 2 / (n + 1)
    for i, x in enumerate(xs):
        if x is None:
            continue
        if acc is None:
            if i + 1 >= n and all(v is not None for v in xs[i + 1 - n:i + 1]):
                acc = sum(xs[i + 1 - n:i + 1]) / n
        else:
            acc = a * x + (1 - a) * acc
        out[i] = acc
    return out


def rma(xs, n):
    out, acc = [None] * len(xs), None
    for i, x in enumerate(xs):
        if x is None:
            continue
        if acc is None:
            win = xs[max(0, i + 1 - n):i + 1]
            if len(win) == n and all(v is not None for v in win):
                acc = sum(win) / n
        else:
            acc = (acc * (n - 1) + x) / n
        out[i] = acc
    return out


def sma(xs, n):
    out = [None] * len(xs)
    for i in range(n - 1, len(xs)):
        win = xs[i + 1 - n:i + 1]
        if all(v is not None for v in win):
            out[i] = sum(win) / n
    return out


def sub(a, b):
    return [None if x is None or y is None else x - y for x, y in zip(a, b)]


@dataclass
class Bar:
    t: int  # 开盘时间 ms (UTC)
    o: float
    h: float
    l: float
    c: float
    v: float


def bars_from_binance(rows) -> list[Bar]:
    return [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def pattern_of(b: Bar, p: Bar | None) -> str:
    rng = b.h - b.l
    if rng <= 0:
        return "NONE"
    body = abs(b.c - b.o)
    up = b.h - max(b.o, b.c)
    lo = min(b.o, b.c) - b.l
    if body <= 0.1 * rng:
        return "DOJI"
    if p is not None:
        pbody = abs(p.c - p.o)
        if b.c > b.o and p.c < p.o and b.c >= p.o and b.o <= p.c and body > pbody:
            return "BULL_ENGULF"
        if b.c < b.o and p.c > p.o and b.c <= p.o and b.o >= p.c and body > pbody:
            return "BEAR_ENGULF"
    if lo >= 2 * body and up <= 0.3 * rng:
        return "HAMMER"
    if up >= 2 * body and lo <= 0.3 * rng:
        return "SHOOTING_STAR"
    if body >= 0.85 * rng:
        return "BULL_MARUBOZU" if b.c > b.o else "BEAR_MARUBOZU"
    if p is not None and b.h < p.h and b.l > p.l:
        return "INSIDE"
    return "NONE"


def r2(x, nd=2):
    return None if x is None else round(x, nd)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Paper:
    side: str = "NONE"       # LONG / SHORT / NONE
    status: str = "FLAT"     # FLAT / OPEN / T1_HIT / STOPPED / BREAKEVEN / T2_HIT
    entry: float | None = None
    stop: float | None = None
    t1: float | None = None
    t2: float | None = None
    risk: float | None = None
    opened_at: int | None = None
    bars_open: int = 0
    history: list = field(default_factory=list)

    def is_live(self):
        return self.status in ("OPEN", "T1_HIT")


def compute(bars: list[Bar], symbol="BINANCE:BTCUSDT", tf="60", bar_minutes=60):
    """返回每根已收盘 K 线的数据包列表。"""
    n = len(bars)
    O = [b.o for b in bars]; H = [b.h for b in bars]; L = [b.l for b in bars]
    C = [b.c for b in bars]; V = [b.v for b in bars]

    e20, e50, e200 = ema(C, EMA_FAST), ema(C, EMA_MID), ema(C, EMA_SLOW)

    ch = [None] + [C[i] - C[i - 1] for i in range(1, n)]
    gain = [None if x is None else max(x, 0) for x in ch]
    loss = [None if x is None else max(-x, 0) for x in ch]
    ag, al = rma(gain, RSI_LEN), rma(loss, RSI_LEN)
    rsi = [None if a is None or b is None else (100.0 if b == 0 else (0.0 if a == 0 else 100 - 100 / (1 + a / b))) for a, b in zip(ag, al)]

    macd = sub(ema(C, 12), ema(C, 26))
    sig = ema(macd, 9)
    hist = sub(macd, sig)

    tr = [H[0] - L[0]] + [max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(1, n)]
    atr = rma(tr, ATR_LEN)
    pdm = [None] + [0.0] * (n - 1); mdm = [None] + [0.0] * (n - 1)
    for i in range(1, n):
        u, d = H[i] - H[i - 1], L[i - 1] - L[i]
        pdm[i] = u if (u > d and u > 0) else 0.0
        mdm[i] = d if (d > u and d > 0) else 0.0
    trr = rma([None] + tr[1:], ADX_LEN)
    pr, mr = rma(pdm, ADX_LEN), rma(mdm, ADX_LEN)
    dip = [None if a is None or t in (None, 0) else 100 * a / t for a, t in zip(pr, trr)]
    dim = [None if a is None or t in (None, 0) else 100 * a / t for a, t in zip(mr, trr)]
    dx = [None if a is None or b is None or (a + b) == 0 else abs(a - b) / (a + b) for a, b in zip(dip, dim)]
    adx = [None if x is None else 100 * x for x in rma(dx, ADX_LEN)]

    vavg = sma(V, VOL_LEN)
    relv = [None if a in (None, 0) else v / a for v, a in zip(V, vavg)]
    atr_rank = [None] * n
    for i in range(n):
        if i >= ATR_RANK_LEN and atr[i] is not None and atr[i - ATR_RANK_LEN] is not None:
            prev = atr[i - ATR_RANK_LEN:i]
            atr_rank[i] = 100 * sum(1 for x in prev if x <= atr[i]) / ATR_RANK_LEN

    # 日线：前一日高低（UTC 日）+ 当日锚定 VWAP
    day = [b.t // 86_400_000 for b in bars]
    dhl: dict[int, list] = {}
    for i in range(n):
        d = dhl.setdefault(day[i], [H[i], L[i]])
        d[0] = max(d[0], H[i]); d[1] = min(d[1], L[i])
    vwap = [None] * n
    pv = vv = 0.0
    for i in range(n):
        if i == 0 or day[i] != day[i - 1]:
            pv = vv = 0.0
        tp = (H[i] + L[i] + C[i]) / 3
        pv += tp * V[i]; vv += V[i]
        vwap[i] = pv / vv if vv else None

    # 大周期：用已收完的 4h K 线
    blk_ms = HTF_MINUTES * 60_000
    blk = [b.t // blk_ms for b in bars]
    htf_close: dict[int, float] = {}
    for i in range(n):
        htf_close[blk[i]] = C[i]
    keys = sorted(htf_close)
    if bars and (bars[0].t % blk_ms) != 0:
        keys = keys[1:]  # 第一个块不完整
    hema = ema([htf_close[k] for k in keys], HTF_EMA)
    hmap = {k: (htf_close[k], e) for k, e in zip(keys, hema)}

    pats = [pattern_of(bars[i], bars[i - 1] if i else None) for i in range(n)]

    def rng_atr(i):
        return None if atr[i] in (None, 0) else (H[i] - L[i]) / atr[i]

    paper = Paper()
    last_sh = last_sl = None
    sh_broken = sl_broken = True
    last_break, last_break_i = "NONE", None
    prev_long = prev_short = 0
    out = []
    for i in range(n):
        # 摆动点：第 i 根确认第 i-SWING_LEN 根
        k = i - SWING_LEN
        if k - SWING_LEN >= 0:
            win_h = H[k - SWING_LEN:i + 1]; win_l = L[k - SWING_LEN:i + 1]
            if H[k] == max(win_h) and win_h.count(H[k]) == 1:
                last_sh, sh_broken = H[k], False
            if L[k] == min(win_l) and win_l.count(L[k]) == 1:
                last_sl, sl_broken = L[k], False
        brk = "NONE"
        if last_sh is not None and not sh_broken and C[i] > last_sh:
            brk, sh_broken = "BREAK_UP", True
        elif last_sl is not None and not sl_broken and C[i] < last_sl:
            brk, sl_broken = "BREAK_DOWN", True
        if brk != "NONE":
            last_break, last_break_i = brk, i

        if None in (e200[i], e200[i - 5] if i >= 5 else None, adx[i], atr[i], rsi[i], hist[i], hist[i - 1] if i else None, relv[i], atr_rank[i]):
            continue

        hk = blk[i] - 1
        htf_bias = "N/A"
        if hk in hmap and hmap[hk][1] is not None:
            hc, he = hmap[hk]
            htf_bias = "UP" if hc > he else ("DOWN" if hc < he else "FLAT")

        stack = "BULL" if e20[i] > e50[i] > e200[i] else ("BEAR" if e20[i] < e50[i] < e200[i] else "MIXED")
        slope = e200[i] - e200[i - 5]
        slope_s = "UP" if slope > 0.0002 * C[i] else ("DOWN" if slope < -0.0002 * C[i] else "FLAT")

        if atr_rank[i] >= HIGH_VOL_PCT:
            regime = "HIGH_VOL"
        elif adx[i] >= 20 and e20[i] > e50[i] and C[i] > e200[i]:
            regime = "TREND_UP"
        elif adx[i] >= 20 and e20[i] < e50[i] and C[i] < e200[i]:
            regime = "TREND_DOWN"
        else:
            regime = "RANGE"

        bull, bear = C[i] > O[i], C[i] < O[i]
        hist_up = hist[i] > hist[i - 1]
        lp = {
            "trend": (10 if e20[i] > e50[i] else 0) + (5 if e50[i] > e200[i] else 0) + (10 if C[i] > e20[i] else 0),
            "momentum": (10 if 50 < rsi[i] < 70 else 0) + (5 if hist[i] > 0 else 0) + (5 if hist_up else 0),
            "strength": (7 if dip[i] > dim[i] else 0) + (8 if dip[i] > dim[i] and adx[i] >= 20 else 0),
            "volume": (15 if bull and relv[i] >= 1.2 else (8 if bull and relv[i] >= 1.0 else 0)),
            "htf": 25 if htf_bias == "UP" else (10 if htf_bias == "FLAT" else 0),
        }
        sp = {
            "trend": (10 if e20[i] < e50[i] else 0) + (5 if e50[i] < e200[i] else 0) + (10 if C[i] < e20[i] else 0),
            "momentum": (10 if 30 < rsi[i] < 50 else 0) + (5 if hist[i] < 0 else 0) + (5 if not hist_up else 0),
            "strength": (7 if dim[i] > dip[i] else 0) + (8 if dim[i] > dip[i] and adx[i] >= 20 else 0),
            "volume": (15 if bear and relv[i] >= 1.2 else (8 if bear and relv[i] >= 1.0 else 0)),
            "htf": 25 if htf_bias == "DOWN" else (10 if htf_bias == "FLAT" else 0),
        }
        ls, ss = sum(lp.values()), sum(sp.values())

        # ---- 纸面记账：先结算旧单，再看新信号 ----
        event, upd = "BAR_CLOSE", None
        if paper.is_live():
            before = paper.status
            paper.bars_open += 1
            if paper.side == "LONG":
                if L[i] <= paper.stop:
                    paper.status = "BREAKEVEN" if paper.status == "T1_HIT" else "STOPPED"
                elif H[i] >= paper.t2:
                    paper.status = "T2_HIT"
                elif paper.status == "OPEN" and H[i] >= paper.t1:
                    paper.status, paper.stop = "T1_HIT", paper.entry
            else:
                if H[i] >= paper.stop:
                    paper.status = "BREAKEVEN" if paper.status == "T1_HIT" else "STOPPED"
                elif L[i] <= paper.t2:
                    paper.status = "T2_HIT"
                elif paper.status == "OPEN" and L[i] <= paper.t1:
                    paper.status, paper.stop = "T1_HIT", paper.entry
            if paper.status != before:
                event, upd = "PAPER_UPDATE", paper.status
                if paper.status != "T1_HIT":
                    paper.history.append((paper.side, paper.status, paper.opened_at, bars[i].t))

        new_setup = None
        if not paper.is_live() and regime != "HIGH_VOL":
            if ls >= THRESHOLD and prev_long < THRESHOLD and ls > ss:
                new_setup = "LONG"
            elif ss >= THRESHOLD and prev_short < THRESHOLD and ss > ls:
                new_setup = "SHORT"
        if new_setup:
            sgn = 1 if new_setup == "LONG" else -1
            risk = STOP_ATR * atr[i]
            paper = Paper(new_setup, "OPEN", C[i], C[i] - sgn * risk, C[i] + sgn * T1_R * risk,
                          C[i] + sgn * T2_R * risk, risk, bars[i].t, 0, paper.history)
            event = "SETUP_" + new_setup
        prev_long, prev_short = ls, ss

        r_now = None
        if paper.is_live() and paper.risk:
            sgn = 1 if paper.side == "LONG" else -1
            r_now = (C[i] - paper.entry) * sgn / paper.risk

        last5 = []
        for j in range(WINDOW - 1, -1, -1):
            q = i - j
            last5.append({"ago": j, "o": bars[q].o, "h": H[q], "l": L[q], "c": C[q], "v": r2(V[q], 3),
                          "dir": "bull" if C[q] > O[q] else ("bear" if C[q] < O[q] else "flat"),
                          "body_pct": r2(100 * abs(C[q] - O[q]) / (H[q] - L[q]) if H[q] > L[q] else 0, 1),
                          "range_atr": r2(rng_atr(q)), "pattern": pats[q]})
        rngi = H[i] - L[i]
        prev_day = dhl.get(day[i] - 1)
        side_txt = f"{paper.side}:{paper.status}" if paper.side != "NONE" else "NONE:FLAT"
        pkt = {
            "schema": SCHEMA,
            "event": event,
            "symbol": symbol,
            "tf": tf,
            "bar_close_utc": iso(bars[i].t + bar_minutes * 60_000),
            "summary": f"CJ|{symbol.split(':')[-1]}|{tf}|{regime}|L{ls}|S{ss}|{side_txt}",
            "paper_only": True,
            "bar": {
                "o": bars[i].o, "h": H[i], "l": L[i], "c": C[i], "v": r2(V[i], 3),
                "dir": "bull" if bull else ("bear" if bear else "flat"),
                "body_pct": r2(100 * abs(C[i] - O[i]) / rngi if rngi else 0, 1),
                "upper_wick_pct": r2(100 * (H[i] - max(O[i], C[i])) / rngi if rngi else 0, 1),
                "lower_wick_pct": r2(100 * (min(O[i], C[i]) - L[i]) / rngi if rngi else 0, 1),
                "range_atr": r2(rng_atr(i)),
                "close_pos_pct": r2(100 * (C[i] - L[i]) / rngi if rngi else 50, 1),
                "gap_pct": r2(100 * (O[i] - C[i - 1]) / C[i - 1], 3),
                "pattern": pats[i],
            },
            "last5": last5,
            "regime": regime,
            "trend": {"ema20": r2(e20[i]), "ema50": r2(e50[i]), "ema200": r2(e200[i]), "stack": stack,
                      "ema200_slope": slope_s, "htf": str(HTF_MINUTES), "htf_bias": htf_bias},
            "osc": {"rsi": r2(rsi[i], 1), "macd_hist": r2(hist[i]), "macd_hist_up": hist_up,
                    "adx": r2(adx[i], 1), "di_plus": r2(dip[i], 1), "di_minus": r2(dim[i], 1)},
            "vol": {"rel_volume": r2(relv[i]), "atr": r2(atr[i]), "atr_pctile": r2(atr_rank[i], 0)},
            "structure": {"swing_high": last_sh, "swing_low": last_sl, "event": brk, "last_break": last_break,
                          "bars_since_break": None if last_break_i is None else i - last_break_i},
            "levels": {"pdh": prev_day[0] if prev_day else None, "pdl": prev_day[1] if prev_day else None,
                       "vwap": r2(vwap[i])},
            "score": {"long": ls, "short": ss, "threshold": THRESHOLD, "long_parts": lp, "short_parts": sp},
            "paper": {"side": paper.side, "status": paper.status if paper.side != "NONE" else "FLAT",
                      "entry": r2(paper.entry), "stop": r2(paper.stop), "t1": r2(paper.t1), "t2": r2(paper.t2),
                      "r_now": r2(r_now), "bars_open": paper.bars_open, "update": upd},
        }
        out.append(pkt)
    return out, paper
