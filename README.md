# Candle JSON（只研究，不下单）

把已经收盘的 K 线打成一行 JSON，经 webhook 进本地接收端，再拼成给 Fable 5.2 的研究提示词。

指标本身不产生多空信号。接收端不下单、不算仓位，只写纸面研究文件。这次演示没有调用模型。

## 文件

- `candle_to_json.pine` — TradingView Pine v5 指标，仅在收盘后 `alert()` 一行 JSON
- `receiver.py` — 本地接收端，保留最近 5 根，拼提示词
- `prompts/researcher.md` — 研究员提示词（只能输出「观望」或「待验证」）
- `simulate_alerts.py` — 用币安公开的 BTCUSDT 1 小时 K 线模拟 TradingView 推送（先请求 api.binance.com，若返回 451 则改走 data-api.binance.vision，同一套 /api/v3/klines）
- `indicators.py` — 与 Pine `ta.ema` / `ta.atr` 对齐的计算

## 本地模拟

```bash
.venv/bin/python receiver.py
.venv/bin/python simulate_alerts.py
```

接收端默认听 `127.0.0.1:8765/alert`。跑完后看：

- `data/alerts.jsonl` — 每根收到的 K 线
- `data/last_payload.json` — 最近 5 根窗口
- `data/last_prompt.md` — 拼好的研究提示词

## 接到真实 TradingView 警报

1. 把 `candle_to_json.pine` 贴进 Pine Editor，加到图上。
2. 创建警报，条件选 Any alert() function call，频率 Once Per Bar Close。
3. 打开 Webhook URL。TradingView 官方说明：webhook 需要账户开启 2FA；付费套餐才带 webhook（免费 Basic 没有），以 [Pricing](https://www.tradingview.com/pricing/) 和 [webhook 说明](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/) 为准。
4. TradingView 只向 80 / 443 端口发 POST，超时 3 秒，且需要公网地址。本地 8765 端口只适合模拟。真实使用需要反代或隧道把 HTTPS:443 转到接收端，不要把下单密钥写进警报消息。
