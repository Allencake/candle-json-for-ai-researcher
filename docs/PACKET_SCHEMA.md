# 数据包格式 `candle-json/v2`

每根 K 线收盘，指标发出一个 JSON 对象。缺的值写 `null`，价格按品种最小跳动取整。
真实样例（币安 BTCUSDT 1h 回放生成）：[`examples/sample_packet.json`](../examples/sample_packet.json)、[`examples/sample_setup_packet.json`](../examples/sample_setup_packet.json)

## 顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | string | 固定 `candle-json/v2` |
| `event` | enum | `BAR_CLOSE` 普通收盘 · `SETUP_LONG` / `SETUP_SHORT` 分数刚过线 · `PAPER_UPDATE` 纸面单状态变化 |
| `symbol` | string | 交易所前缀 + 代码，如 `BINANCE:BTCUSDT` |
| `tf` | string | TradingView 周期代码：`15`、`60`、`240`、`D` |
| `bar_close_utc` | string | 收盘时间，ISO 8601，UTC |
| `summary` | string | 一行摘要：`CJ\|代码\|周期\|状态\|L多分\|S空分\|方向:纸面状态` |
| `paper_only` | bool | 固定 `true`，提醒下游：这是研究数据，不是下单指令 |

## `bar`：刚收盘的这根

| 字段 | 说明 |
|---|---|
| `o` `h` `l` `c` `v` | 开高低收量 |
| `dir` | `bull` / `bear` / `flat` |
| `body_pct` | 实体占整根振幅的 % |
| `upper_wick_pct` / `lower_wick_pct` | 上 / 下影线占振幅的 % |
| `range_atr` | 振幅 ÷ ATR，>1.5 算大 K 线 |
| `close_pos_pct` | 收盘在振幅里的位置，0 = 最低，100 = 最高 |
| `gap_pct` | 开盘相对上一根收盘的跳空 % |
| `pattern` | 形态，见下表 |

**形态**（按顺序判断，命中第一个就停）：

| 值 | 条件 |
|---|---|
| `DOJI` 十字星 | 实体 ≤ 振幅 10% |
| `BULL_ENGULF` / `BEAR_ENGULF` 吞没 | 和上一根方向相反，实体完全包住上一根实体，且更大 |
| `HAMMER` 锤子线 | 下影 ≥ 2 倍实体，上影 ≤ 振幅 30% |
| `SHOOTING_STAR` 射击之星 | 上影 ≥ 2 倍实体，下影 ≤ 振幅 30% |
| `BULL_MARUBOZU` / `BEAR_MARUBOZU` 光头光脚 | 实体 ≥ 振幅 85% |
| `INSIDE` 内包线 | 高点低于上一根高点，低点高于上一根低点 |
| `NONE` | 以上都不是 |

## `last5[]`：最近 5 根（旧 → 新，最后一根就是 `bar`）

`ago`（0 = 当前）、`o` `h` `l` `c` `v`、`dir`、`body_pct`、`range_atr`、`pattern`。

## `regime`：市场状态

| 值 | 条件（按顺序） |
|---|---|
| `HIGH_VOL` | ATR 百分位 ≥ 90 |
| `TREND_UP` | ADX ≥ 20，EMA20 > EMA50，收盘 > EMA200 |
| `TREND_DOWN` | ADX ≥ 20，EMA20 < EMA50，收盘 < EMA200 |
| `RANGE` | 其他 |

## `trend` / `osc` / `vol`

| 字段 | 说明 |
|---|---|
| `trend.ema20` `ema50` `ema200` | 三条 EMA |
| `trend.stack` | `BULL`（20>50>200）/ `BEAR` / `MIXED` |
| `trend.ema200_slope` | EMA200 对比 5 根前：`UP` / `DOWN` / `FLAT`（变化小于价格 0.02% 算平） |
| `trend.htf` / `htf_bias` | 大周期（默认 240）上一根已收盘 K 线相对它的 EMA50：`UP` / `DOWN` / `FLAT` / `N/A` |
| `osc.rsi` | RSI 14 |
| `osc.macd_hist` / `macd_hist_up` | MACD(12,26,9) 柱子，和是否比上一根高 |
| `osc.adx` `di_plus` `di_minus` | DMI 14 |
| `vol.rel_volume` | 成交量 ÷ 20 根均量 |
| `vol.atr` / `atr_pctile` | ATR 14，和它在最近 100 根里的百分位 |

## `structure` / `levels`

| 字段 | 说明 |
|---|---|
| `structure.swing_high` / `swing_low` | 最近确认的摆动高 / 低点（左右各 5 根） |
| `structure.event` | 这一根有没有收盘突破：`BREAK_UP` / `BREAK_DOWN` / `NONE`（同一个摆动点只算一次） |
| `structure.last_break` / `bars_since_break` | 最近一次突破和距今根数 |
| `levels.pdh` / `pdl` | 前一日高 / 低 |
| `levels.vwap` | 当日 VWAP |

## `score`：多空分（0–100）

五项加总，权重是我们自己定的：趋势 25、动量 20、强度 15、量能 15、大周期 25。

| 项 | 多头得分（空头对称） |
|---|---|
| 趋势 25 | EMA20>EMA50 +10，EMA50>EMA200 +5，收盘>EMA20 +10 |
| 动量 20 | 50<RSI<70 +10，MACD 柱>0 +5，柱子抬高 +5 |
| 强度 15 | DI+>DI− +7，同时 ADX≥20 再 +8 |
| 量能 15 | 阳线且量比≥1.2 得 15，量比≥1.0 得 8 |
| 大周期 25 | 大周期 UP 得 25，FLAT 得 10 |

`long_parts` / `short_parts` 给出每一项的得分，方便 AI 判断分数是被哪一项撑起来的。

## `paper`：指标自己记的纸面单

| 字段 | 说明 |
|---|---|
| `side` | `LONG` / `SHORT` / `NONE` |
| `status` | `FLAT` / `OPEN` / `T1_HIT` / `STOPPED` / `BREAKEVEN` / `T2_HIT` |
| `entry` `stop` `t1` `t2` | 收盘价入场；止损 = 1.5×ATR；目标 1.5R 和 3R |
| `r_now` | 持仓中时按收盘价算的 R 倍数，不在持仓时为 `null` |
| `bars_open` | 持仓根数 |
| `update` | 这一根发生的状态变化，没有就是 `null` |

规则：分数从线下穿到 ≥70（默认）、且高于另一边、当前没有纸面单、不在高波动状态时，才记一张新纸面单。
到达目标 1 后止损移到入场价。同一根 K 线里止损和目标都碰到时，按止损算（保守）。

这些只是复盘用的风险地图，指标和接收端都不会下单。
