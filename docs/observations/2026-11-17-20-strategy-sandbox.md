# 20 个 OKX 模拟盘策略观察记录

**结果：20 个策略已完成配置并全部启动试跑；实际成交后的对账差异触发保护，提前停止观察。当前 20 个策略全部停止，kill switch 与开仓暂停保持开启，尚不能继续运行。**

正式观察：2026-11-17 17:47:16 至 2026-11-17 17:52:14（北京时间，299 秒）。

配置为 4 类算法各 5 组，20 个不同现货 USDT 标的，全部使用 1 分钟收盘 K 线。每组独立模拟预算 100 USDT，共 2,000 USDT；每单配置量在设置时约 10 USDT，单笔硬上限 20 USDT。原 MAG 策略保持停止。

当前状态：{'stopped': 20}；环境校验通过：True。
正式观察窗口内订单状态：{'pending': 1, 'rejected': 4}；成交明细 2 条。包含启动排障阶段的本批累计成交明细 2 条。
观察结束时采样的风控状态（后续已另行暂停开仓）：{"runtime_risk": {"blocked": false, "reason": null}, "opening_pause": {"paused": false, "reason": "", "updated_at": 1794908815050}, "kill_switch": {"engaged": true, "reason": "Private sync detected high-risk divergence", "updated_at": 1794908884297}, "private_sync": {"status": "fresh", "fresh": true, "attempts": 1, "cursor_ms": 1794909134423, "complete": false}, "readiness": {"ready": true, "reasons": []}, "unresolved_live_orders": 0}

## 中止原因与待核对资产

LINK 策略的一张订单（交易所订单号 4107280016619196416）拆成两笔成交：0.039757 + 1.104985 = 1.144742 LINK，成交金额 10.066463578 USDT，LINK 手续费 0.001144742。交易所明确返回 state=filled/status=closed，但成交量小于原委托量 1.146，差额 0.001258。

本地订单仍为 pending/filled_amount=0；funded 账本仍显示现金100、持币0，同时预留10.066464 USDT。已保存的两条真实成交与账本未闭合，不能把账本持币0解释为未成交，也不能据此计算利润。未自动卖出本次净增的约1.143597258 LINK 模拟资产。

现有同步将这类已终结但未足量成交的市价单识别成“挂单消失但仅部分成交”，触发全局保护。本轮保存的证据未见重复订单或实盘成交。恢复前需按这一唯一订单号核对终态、成交、币种手续费并结算策略账本；fresh/ready或unresolved_live_orders=0不能证明已完成对账。

## 正式观察窗口的拒单原因

- Local pending order is missing from OKX open orders despite partial matching trades：2 次
- Kill switch engaged：4 次

## 各策略

| 名称 | 算法 | 标的 | 状态 | 累计成交条数 | 账本现金 USDT | 账本持币 |
|---|---|---|---|---:|---:|---:|
| OBS20_01_MA_BTC | ma_cross | BTC-USDT | stopped | 0 | 100 | 0 |
| OBS20_02_RSI_ETH | rsi_mean_reversion | ETH-USDT | stopped | 0 | 100 | 0 |
| OBS20_03_BB_SOL | bollinger_mean_reversion | SOL-USDT | stopped | 0 | 100 | 0 |
| OBS20_04_DC_XRP | donchian_breakout | XRP-USDT | stopped | 0 | 100 | 0 |
| OBS20_05_MA_DOGE | ma_cross | DOGE-USDT | stopped | 0 | 100 | 0 |
| OBS20_06_RSI_ADA | rsi_mean_reversion | ADA-USDT | stopped | 0 | 100 | 0 |
| OBS20_07_BB_LTC | bollinger_mean_reversion | LTC-USDT | stopped | 0 | 100 | 0 |
| OBS20_08_DC_BCH | donchian_breakout | BCH-USDT | stopped | 0 | 100 | 0 |
| OBS20_09_MA_LINK | ma_cross | LINK-USDT | stopped | 2 | 100 | 0 |
| OBS20_10_RSI_AVAX | rsi_mean_reversion | AVAX-USDT | stopped | 0 | 100 | 0 |
| OBS20_11_BB_DOT | bollinger_mean_reversion | DOT-USDT | stopped | 0 | 100 | 0 |
| OBS20_12_DC_UNI | donchian_breakout | UNI-USDT | stopped | 0 | 100 | 0 |
| OBS20_13_MA_ATOM | ma_cross | ATOM-USDT | stopped | 0 | 100 | 0 |
| OBS20_14_RSI_NEAR | rsi_mean_reversion | NEAR-USDT | stopped | 0 | 100 | 0 |
| OBS20_15_BB_TRX | bollinger_mean_reversion | TRX-USDT | stopped | 0 | 100 | 0 |
| OBS20_16_DC_ETC | donchian_breakout | ETC-USDT | stopped | 0 | 100 | 0 |
| OBS20_17_MA_FIL | ma_cross | FIL-USDT | stopped | 0 | 100 | 0 |
| OBS20_18_RSI_APT | rsi_mean_reversion | APT-USDT | stopped | 0 | 100 | 0 |
| OBS20_19_BB_SUI | bollinger_mean_reversion | SUI-USDT | stopped | 0 | 100 | 0 |
| OBS20_20_DC_OP | donchian_breakout | OP-USDT | stopped | 0 | 100 | 0 |

## 启动排障与验证

- 行情服务补上真实收盘行情接收心跳；私有账户同步在请求完成后采样时钟，避免把刚收到的数据判成未来时间。页面运行状态使用相同心跳与配置阈值。
- 已有公有 WebSocket 实现会在预热时只返回订阅确认，且多个标的共享接收连接；本次策略服务采用既有的限速 REST 行情。
- 全账户快照优先采用账户级 uTime；保留币种级时间回退、数据可信度检查及过期拒单。
- 客户单号改为符合 OKX 约束的字母数字。旧非法单号收到明确 51000 拒单回执，已使用原始回执核对为 rejected，未重发该订单。
- 现货可卖库存按最新账户资产可用余额校验，并继续先检查策略自有 funded 份额；无资产行、余额为零及无自有持币均被拒绝。

## 说明与证据

这是短时功能运行观察，不能据此判断策略收益能力。资金均为 OKX 模拟盘额度；成交与订单事实以保存的交易所回报为准。

原始证据目录：`/tmp/okx-batch20-20261117-170715`。`manifest.json` 保存精确配置，`starts.json` 保存本轮启动结果，`observations.jsonl` 保存逐次状态、订单、成交和资金账本快照；排障前观察另存为 before-* 文件。

后端完整测试：`4289 passed, 1 warning in 109.34s (0:01:49)`。
