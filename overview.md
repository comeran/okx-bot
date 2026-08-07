# OKX Bot 项目状态与待办

> 状态同步：2026-08-07 ｜ 后端：456 passed, 3 warnings ｜ 前端：237 passed ｜ 源码 TODO：0

## 一、项目健康度

| 维度 | 状态 |
|------|------|
| 后端验证 | 456 passed, 3 warnings；Ruff 全过（warning 为非阻塞环境/弃用项） |
| 前端验证 | 237 passed；`vue-tsc` 与生产构建通过；策略 CRUD/lifecycle 已完成浏览器验证 |
| 源码遗留标记 | 零 TODO/FIXME/XXX/HACK/NotImplementedError |
| 前端页面 | 6 个齐全（Dashboard/Strategy/Backtest/Market/Trades/Settings） |
| 策略计划 | 专用 runtime-hardening 计划已完成并同步；宽泛 overall roadmap 的非策略范围已暂停 |

## 二、已完成的里程碑（经代码交叉验证，非仅凭文档）

- 回测引擎 + 真实历史数据 + 结果持久化（`/api/backtest/run` 已跑真实引擎，非合成）
- 回测历史 K线图 + 买卖标记（`backtest-kline-progress-plan` 顶部标 Status: Implemented）
- Paper 模式会计契约（订单/成交/账户/现金账本/净仓位/已实现PnL/费用）
- 风险 / 账户 / 运行时切片
- 运行时 UI polish（Dashboard/Strategies 作为运行时控制页）
- 策略配置与运行时硬化：
  - 前端已完成创建、读取、编辑、删除、克隆、结构化/YAML 编辑、校验、脏状态保护与运行时只读切换
  - `ma_cross`、`rsi_mean_reversion`、`bollinger_mean_reversion`、`donchian_breakout` 均支持持久化配置，并共享 registry、统一订单管理器、真实 `BotEngine`、状态、WebSocket、错误与清理路径
  - `ma_cross` 保持唯一隐式 legacy 策略；其余 built-in 仅通过持久化配置实例化
  - 编辑时名称与 `strategy_type` 不可变；克隆保留，草稿名称为空且默认禁用
  - 禁用配置不能启动；active/starting 与未平仓仓位状态会阻止不安全更新或删除；kill switch 同时拦截策略启动和订单提交
  - 不自动启动持久化策略，不在失败后自动重启；单个策略错误只停止该策略
- `live/demo` 交易集成基础（代码与自动化覆盖；未执行外部私有 API 冒烟，未授权真实资金）：
  - OKX 私有账户/仓位快照同步（`live_sync.py`）
  - 适配器工厂支持 spot/swap/futures/**options**（`exchange/factory.py`）
  - live state refresh（下单前后 + 策略启动 + 手动 `/api/trading/live-state/refresh`）
  - 可配置开仓控制（`allow_live_open_orders` / `live_max_order_notional`，取代硬 reduce-only）
  - 市场类型感知（行情/交易按 market_type 选适配器）
  - **SL/TP/trigger 订单参数**（`exchange/base.py:380-388`，映射 OKX triggerPrice/stopLoss/takeProfit）
- `live/demo` 运维安全基础（代码与自动化覆盖；外部验证仍受授权门禁）：
  - Kill switch（`GET/PUT /api/ops/kill-switch`，下单前 + 策略启动前双拦截）
  - 私有对账（`POST /api/ops/sync/private`，demo-only，账户/挂单/成交幂等 upsert + 差异检测）
  - Telegram 风险通知（接 risk event 广播路径）+ 测试端点（`POST /api/settings/notify/test`，不泄露 token）
- 账户资产概览（最新 PR #11）

## 三、暂停的非策略待办（需单独请求）

> 当前策略范围已经完成。以下条目仅保留为历史待办清单，不是当前执行计划，也未因策略工作而获得自动实施授权。

### A. 外部交易所与真实资金门禁

- [ ] 手动 OKX **demo** 私有 API 冒烟（`exchange.demo=true`，仅在用户当次明确授权后验证 `/sync/private`）
- [ ] `exchange.demo=false` 真实资金启用（必须由用户明确授权并先制定独立计划）
- [ ] 实盘安全检查清单（开仓开关、notional 上限、对账频率、kill switch 演练）

### B. 运行时与会计增强（暂停）

- [ ] 真正的滚动日 PnL（基于时间戳的已实现 PnL 账本事件）
- [ ] 盯市未实现 PnL 的共享行情价格源正确性闭环
- [ ] 熔断状态 / pause-all / 手动解锁
- [ ] 监控最大日亏 / 最大回撤 / 总仓位 / 保证金率 / 强平风险
- [ ] 衍生品特有：杠杆 / 保证金模式 / 仓位模式处理
- [ ] 重试/退避 + 交易所错误归一化
- [ ] 行情数据共享缓存 + WebSocket 流式重连 + 丢消息恢复
- [ ] 配置热重载决策 + 运行时状态恢复（graceful restart）

### C. 前端产品

- [x] 策略配置前端 CRUD UX 与浏览器 lifecycle 验证
- [x] 四种 built-in 的持久化配置、实例化与共享 start/stop/status 路径
- [ ] YAML 策略 DSL 扩展与更多算子（暂停）
- [ ] 回测：资金曲线 / 回撤曲线 / 单笔交易列表 / 多 run 对比（暂停）
- [ ] 行情：可配置标的、实时更新、订单簿、技术指标叠加（暂停）
- [ ] 交易页：筛选、分页、订单历史（暂停）

### D. 运维部署（暂停）

- [ ] Docker / 部署打包
- [ ] CI 检查（后端测试 / 前端测试 / build / lint）
- [ ] 生产/实盘安全检查清单文档
- [ ] 备份/恢复指引（`data/bot.db` + 配置）
- [ ] 健康检查端点（runtime services status）

## 四、后续工作门禁

1. 当前没有默认自动开始的下一里程碑；非策略工作保持暂停，直到用户明确指定范围。
2. 若继续策略相关工作，应基于新需求建立独立策略计划，不从宽泛 overall roadmap 推断授权。
3. 外部 OKX demo/private API 冒烟必须获得当次明确授权；自动化测试继续保持无外部调用。
4. 真实资金启用必须获得明确授权并使用单独计划，不能并入普通策略或文档任务。
5. Git commit 与 push 仍需用户明确要求。

## 五、注意事项

- `docs/superpowers/plans/2026-06-03-okx-bot-next-steps.md` 是早期清单，其 "Current state" 多处已过时，使用前必须对照当前代码和测试。
- 策略范围以 `docs/superpowers/plans/2026-08-04-strategy-runtime-hardening.md` 的完成记录为准；宽泛 overall roadmap 已暂停，不能把其中未完成里程碑当作已授权工作。
