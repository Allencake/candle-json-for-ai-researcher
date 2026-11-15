# K线转JSON · AI 研究台（candle-json v2）

**把每根收盘 K 线拆成一份 JSON，交给 AI 当研究员。它只给纸面结论，不下单，最后由你自己决定。**

> English: A TradingView Pine v6 indicator that turns every closed candle into a structured JSON packet (candle anatomy, last 5 bars, regime, EMA stack, RSI/MACD/ADX, volume, structure, key levels, long/short scores, paper risk map) and sends it to your AI researcher via webhook. A small Flask receiver stores packets, builds the prompt, optionally calls the Anthropic API, and keeps a paper journal. Research only. There is no order-placing code anywhere in this repo.

Pine Script v6 · Python 3 · MIT

---

## 它解决什么问题

指标是画给人看的：颜色、箭头、线。模型看不懂颜色。
这个指标在每根 K 线收盘时，把人眼会看的东西全部写成字段，发给你的 AI。AI 读完给一份研究结论：偏多、偏空、观望还是跳过，理由是什么，可能错在哪。

## 每根 K 线发出去的数据

- **K 线拆解**：实体、上下影线占比，振幅相对 ATR，收在区间什么位置，跳空，形态（吞没、锤子、十字星、内包等）
- **最近 5 根**：同样格式，让 AI 读的是一段走势，不是孤零零一根
- **市场状态**：`TREND_UP` / `TREND_DOWN` / `RANGE` / `HIGH_VOL`
- **趋势**：EMA 20/50/200 排列、EMA200 斜率、大周期方向（默认 4h）
- **动量与强度**：RSI、MACD 柱、ADX、DI+/DI−
- **量能与波动**：量比、ATR、ATR 百分位
- **结构与关键位**：摆动高低点和突破、前日高低、VWAP
- **多空分 0–100**：五项拆分（趋势 25 / 动量 20 / 强度 15 / 量能 15 / 大周期 25）
- **纸面风险地图**：入场、1.5×ATR 止损、1.5R / 3R 目标、当前状态

字段说明：[docs/PACKET_SCHEMA.md](docs/PACKET_SCHEMA.md) · 真实样例：[examples/sample_packet.json](examples/sample_packet.json)

## 流程

```
TradingView 图表                 你的接收端 / AI                         你
┌──────────────────┐  Webhook  ┌───────────────────────────────┐    ┌──────────────┐
│ CJ 研究台指标     │ ────────► │ receiver.py 存包、拼提示词      │ ─► │ 看纸面结论    │
│ 收盘 → 一行 JSON  │           │ 研究员：偏多/偏空/观望/跳过      │    │ 自己决定做不做 │
└──────────────────┘           │ 写纸面日志，跟踪 T1 / 止损       │    └──────────────┘
                               └───────────────────────────────┘
```

---

## 快速开始

### 1. 把指标加到 TradingView

1. 打开 [`candle_json_v2.pine`](candle_json_v2.pine)，全部复制。
2. TradingView 图表底部打开 **Pine Editor**，清空默认代码，粘贴，保存，**添加到图表**。

图上会出现：EMA 带（20/50 + 200）、前日高低和 VWAP、摆动点和“破高 / 破低”标记、▲偏多 / ▼偏空 分数标签、纸面风险框（红色止损区、绿色目标区、虚线目标 1），右上角是**研究面板**。

### 2. 建一个提醒

1. 打开提醒（`Alt + A`）。
2. 条件选 `K线转JSON · AI研究台` → `Any alert() function call`（任意 alert() 函数调用）。
3. 通知里勾 **Webhook URL**，填第 3 步的地址。
4. 消息框不用动，内容由指标自己写。

一个提醒只管一张图（一个品种 + 一个周期）。

> **Webhook 的门槛**：TradingView 官方帮助页写明，账户要先开**两步验证**才能用 Webhook；免费 Basic 版没有 Webhook 通知，需要付费套餐（以 TradingView 价格页为准）。Webhook 只接受 80 / 443 端口，3 秒内没响应就会取消，所以接收端先回 200，研究放后台做。

### 3. 启动接收端

```bash
git clone https://github.com/Allencake/candle-json-for-ai-researcher
cd candle-json-for-ai-researcher
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python receiver.py --port 8080 --token 换成你自己的口令
# 另开一个终端，给它一个公网 HTTPS 地址（任选一种隧道）
cloudflared tunnel --url http://localhost:8080
```

Webhook 地址：`https://<你的隧道地址>/webhook?token=你的口令`

想让真模型来读，再设两个环境变量（不设就是 dry-run，只复述指标分数，会标明 `source: dry-run`）：

```bash
export ANTHROPIC_API_KEY=...
export CJ_MODEL=...   # 你在 Anthropic 控制台看到的模型 ID，比如 Fable 5.2 对应的那个
```

接收端会写这些文件（都在 `data/`，已加入 .gitignore）：

| 文件 | 内容 |
|---|---|
| `packets/latest.json`、`packets/latest/<代码>_<周期>.json` | 最新数据包 |
| `packets/history.jsonl` | 所有数据包 |
| `last_prompt.md` | 最近一次拼好的提示词 |
| `journal.jsonl` | 每根 K 线的研究结论 |
| `paper_book.json` | 纸面账本：进行中 / 已结束，T1 到达后止损移到入场 |

### 不开 TradingView 先试一下

```bash
python tools/make_samples.py --symbol BTCUSDT --interval 1h   # 拉币安公开 K 线，按同一套规则回放
python receiver.py --replay examples/replay_packets.jsonl      # 把回放的数据包喂给接收端
```

`tools/lens.py` 是指标规则的 Python 版，用来生成真实样例、核对 Pine 的输出。行情来自币安公开接口 `data-api.binance.vision`，不需要账号和密钥。

### 也可以不用接收端

- **Claude Code**：在仓库目录里打开，让它“按 `prompts/researcher.md` 读 `data/packets/latest.json`，给我这根 K 线的纸面结论”。
- **n8n / Make**：Webhook 触发 → AI 节点（系统提示词用 `prompts/researcher.md`，用户消息是数据包）→ 推到 Telegram。

---

## 研究员会输出什么

见 [`prompts/researcher.md`](prompts/researcher.md)。只输出一个 JSON：

- `decision`：偏多 / 偏空 / 观望 / 跳过（拿不准就观望或跳过）
- `confidence`：0–100，表示把握，不是胜率
- `paper_plan`：只有偏多 / 偏空才写，入场、止损、两个目标；**不写仓位、杠杆、金额**
- `invalidation`、`why_wrong`、`missing_data`：每次都要写它可能错在哪、还缺什么数据

`examples/sample_research.dryrun.json` 是 dry-run 的输出，不是模型结论。

---

## 和原版的关系

思路来自 [roan-maverick/tradingview-indicator-for-ai-agents](https://github.com/roan-maverick/tradingview-indicator-for-ai-agents)（AI Agent Lens，MIT），感谢原作者。
这里的 Pine、接收端、提示词和文档都是我们自己重写的，主要区别：

- 打分权重、字段命名、面板都不同，界面和提示词是中文
- 研究员只给纸面结论：没有仓位大小，没有“交给 AI 自动下单”这一步
- 接收端自带纸面账本（T1 / 止损 / 保本 / T2 跟踪）和 dry-run 模式
- 附带 Python 参考实现，样例数据来自真实行情回放

## 免责声明

仅供学习和研究。不是投资建议，不承诺任何收益。指标和接收端都不下单；任何真实交易都由你自己判断、自己负责。
