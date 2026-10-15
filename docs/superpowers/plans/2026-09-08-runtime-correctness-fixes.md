# Runtime Correctness Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复策略运行、私有同步、订单成交、实盘账户归属和 Dashboard 实时快照中的高风险正确性问题，避免错误持仓、错误绩效、误触发熔断和交易所状态分叉。

**Architecture:** 先统一订单成交量/状态模型，再修复 private sync 的 fail-closed 和幂等语义；随后把共享 live 账户明确建模为 `__exchange__`，禁止在没有 ownership 证据时伪造策略归属。最后修复生命周期、行情事件重试、策略解析和前端 WebSocket scope，使全局账户快照与策略局部更新保持边界清晰。

**Scope:** 本计划针对 `strategy-runtime-hardening` 相对 `main` 的代码审查阻塞项。不要在执行期间顺手做 UI 美化、重构无关模块或修改 API 之外的业务行为。

## Test cadence policy

为避免每个微修复反复运行完整测试套件，执行时遵循以下节奏：

- 每个修复方向只运行一轮 focused test suite；同一方向后续小修复只运行受影响的最小测试集。
- 不在每个代理修复后重复运行完整 backend/frontend suite。
- 每个 Slice 结束时运行一次该 Slice 的完整回归。
- 准备合并前再运行一次全仓库 backend tests、frontend tests、type-check、build 和 Ruff。
- 不删除交易、迁移、风控、provenance 等关键安全测试；通过合并重复 fixture/参数化测试减少数量。
- 浏览器/运行时验证单独作为最终验收，不用测试套件替代真实界面或服务验证。

**不变约束：**

- 不改变 OKX API 认证方式和交易所请求契约。
- 不自动重命名或删除历史策略配置。
- 不伪造无法证明的 live 策略 ownership。
- 不把 provider 不可用当作交易所状态正常或异常。
- 所有数据库迁移必须可重复执行，并先在临时 SQLite 数据库验证。
- 未经用户明确要求，不提交或推送额外修复提交。

## Locked architecture decision: one authoritative order state

继续修复时采用“单一权威源”架构，不再通过更多局部条件修补 snapshot 与增量 trade 的冲突：

- 对同一 exchange order，交易所订单 snapshot 的累计 `filled`、`remaining`、`average` 和明确终态是当前订单状态的权威来源。
- recent trades 只负责记录可审计的成交明细，以及在订单 snapshot 不可用时补充尚未持久化的成交；它不能把已由 snapshot 反映的累计数量再次相加。
- 订单聚合必须由一个可测试的纯函数/服务完成：输入本地订单、最新 snapshot（可空）、已持久化 trade IDs/成交量和本次 trade page，输出单调的订单状态、累计成交量、剩余量和加权均价。
- `filled_amount`、`remaining_amount`、`fill_price`、`snapshot_filled_amount` 的关系必须在一个地方定义；Repository 只负责持久化和数据库唯一性，不重新解释业务来源。
- 如果无法判断 trade page 是累计窗口还是 since 后增量，必须由 adapter 明确返回 metadata/模式，不能在 reconciliation 中猜测。
- 无法证明交易所订单状态完整时，不能把 pending/partial 推进为 filled，也不能把 filled/canceled/rejected 降级。

这项决定允许重构 Slice 1/2 的订单聚合边界，但不改变后续 live ownership、provider fail-closed 和 WebSocket scope 的产品决策。

## 已确认的问题

1. 部分成交后订单消失于 open orders 时被写成全量 `FILLED`。
2. recent trades provider 失败后仍继续 divergence 检查并可能触发 kill switch。
3. symbol-scoped sync 将局部交易所响应与全部本地 pending orders 比较。
4. live 全局账户/持仓被按触发刷新的策略复制归属。
5. derivative contracts 未乘 contractSize。
6. 交易所已接受订单后 post-submit sync 失败被当作 submit failure。
7. 持久化策略名可与 builtin 或 `__exchange__` 冲突。
8. backtest 同名 persisted config 被 builtin 优先解析。
9. `ensure_started()` market-data service 没有在 engine stop 时释放。
10. market bar cursor 在 subscriber callback 成功前推进，失败后丢 bar。
11. strategy definition API 丢失 exclusive bounds。
12. risk event 只在单次 sync 内去重。
13. strategy-scoped account/positions WebSocket 更新覆盖全局 Dashboard snapshot。

## 必须先确认的产品决策

- [x] **Live ownership：** 接受账户余额和交易所持仓只作为 `__exchange__` 级事实；只有能通过 exchange/client order ID 明确匹配的订单和成交，才归属具体策略。
- [x] **未知 ownership 的下单策略：** 在没有明确 allocation/ownership 机制前，未知归属的 live 持仓不能被当作某策略自己的减仓依据；默认继续拒绝无法证明安全性的 live 操作。
- [x] **订单模型迁移：** 接受新增 `filled_amount`、`remaining_amount` 和 `PARTIALLY_FILLED` 状态。
- [x] **callback 重试：** 接受 callback 首次失败后重试同一根 bar，成功 subscriber 可能收到重复事件；避免丢失市场事件优先于去重复杂度。
- [x] **legacy 策略冲突：** 新建/更新禁止冲突名，但历史冲突记录不自动重命名；由管理员人工迁移。

## 依赖顺序

```text
Slice 1: order/fill data model and migrations
  ├── Slice 2: private sync correctness, fail-closed, scope, dedup
  ├── Slice 3: shared live account/position ownership
  └── Slice 4: derivative contractSize
Slice 5: accepted-order/post-submit sync semantics
Slice 6: strategy naming, backtest resolution, exclusive bounds
Slice 7: engine/market lifecycle and scoped WebSocket snapshots
```

## Slice 1: 统一订单成交模型和数据库迁移

**Files:**

- `src/core/types.py` — `OrderStatus`, exchange order/trade snapshot types
- `src/data/models.py` — `OrderRecord`, `RiskEventRecord`
- `src/data/repository.py` — schema migration, order upsert, risk-event persistence
- `tests/unit/test_types.py`
- `tests/unit/test_repository.py`

**Implementation:**

- [ ] 增加 `OrderStatus.PARTIALLY_FILLED`。
- [ ] 增加 `OrderRecord.filled_amount` 和 `remaining_amount`，并定义 `remaining_amount = max(amount - filled_amount, 0)`。
- [ ] 为 `ExchangeOrderSnapshot` 和相关 trade snapshot 增加累计成交字段。
- [ ] 旧 SQLite 迁移规则：旧 `FILLED` 记录的 `filled_amount` 使用原 amount；其它状态默认 0；重新计算 remaining。
- [ ] 为 `RiskEventRecord` 增加可空 `event_key` 和唯一索引；非同步事件允许为 NULL。
- [ ] `save_risk_event` 对相同非空 event_key 幂等返回已有记录，并处理并发唯一约束。

**Failing tests:**

- 构造和序列化部分成交订单。
- 旧 schema 迁移后字段值正确。
- 相同 event_key 重复保存只有一条风险事件。

**Acceptance:**

```bash
uv run pytest tests/unit/test_types.py tests/unit/test_repository.py -q
uv run ruff check src/core/types.py src/data/models.py src/data/repository.py
```

先备份并在临时数据库验证迁移；不允许通过删除数据库规避迁移失败。

## Slice 2: private sync 正确性、fail-closed、scope 和去重

**Files:**

- `src/ops/private_sync.py` — sync orchestration, fill aggregation, divergence detection
- `src/web/api/ops.py` — sync response status
- `tests/unit/test_private_sync.py`
- `tests/integration/test_web_api.py`

**Implementation:**

- [ ] recent trades 获取失败时记录 `trades_status="unavailable"`，仍可保存成功获取的 account/open orders，但跳过依赖完整 trades 的 divergence、kill switch 和告警。
- [ ] `symbols` 非空时只加载并比较对应 symbol 的本地 pending orders；其它 symbol 不参与本次局部同步判定。
- [ ] 按 `exchange_trade_id` 去重窗口重复返回的成交。
- [ ] 累计成交量小于订单 amount 时写 `PARTIALLY_FILLED`，等于/超过时写 `FILLED`；fill price 使用成交量加权均价。
- [ ] open order 消失不能单独证明全量成交；必须结合累计成交量和已知终态。
- [ ] 使用持久化 event_key 跨 sync 幂等风险事件，保留单次内存去重。
- [ ] 将 pending、partial、filled、canceled 的状态转换写成显式状态机，禁止通过 truthy/empty trade list 隐式推断。

**Failing tests:**

- partial fill + canceled order 不得写全量 FILLED。
- recent trades provider failure 不得 divergence/kill switch。
- symbol-scoped sync 不影响其它 symbol pending order。
- 重复 exchange trade ID 不重复累计。
- 同一 divergence 跨两次 sync 只产生一个风险事件。

**Acceptance:**

```bash
uv run pytest tests/unit/test_private_sync.py tests/integration/test_web_api.py -q
uv run ruff check src/ops/private_sync.py src/web/api/ops.py
```

## Slice 3: 共享 live account/position ownership

**Files:**

- `src/exchange/live_sync.py` — live account/position persistence
- `src/web/api/strategies.py` — strategy start/order refresh paths
- `src/order/manager.py` — live risk and ownership checks
- `src/web/api/trading.py` — account/position/performance reads
- `src/analytics/strategy_performance.py`
- `tests/unit/test_live_sync.py`
- `tests/unit/test_order_router.py`
- `tests/unit/test_strategy_performance.py`
- `tests/integration/test_web_api.py`

**Implementation:**

- [ ] OKX global account and exchange position snapshots统一写入 `__exchange__`。
- [ ] 只有 private sync 能用 exchange order/client order ID 明确匹配时，订单/成交才保留具体 strategy ownership。
- [ ] live risk gate 使用共享 `__exchange__` account/positions，不为每个策略复制完整 equity/balance。
- [ ] 无法证明 ownership 的 live position 不作为某策略的自有减仓依据；按安全策略拒绝相关操作并记录明确风险事件。
- [ ] `/api/trading/account` 读取共享 exchange snapshot；strategy-performance 排除 `__exchange__`，避免把全账户 PnL 当成策略 PnL。
- [ ] 为历史被错误写入策略名的 live records 提供只读审计/迁移报告，不自动合并历史绩效。

**Failing tests:**

- live refresh account/positions 只写 `__exchange__`。
- 两策略共享账户只产生一个 exchange account/position snapshot。
- 无 ownership position 无法作为策略减仓依据。
- performance 不返回 exchange account 作为策略行。

**Acceptance:**

```bash
uv run pytest tests/unit/test_live_sync.py tests/unit/test_order_router.py tests/unit/test_strategy_performance.py -q
uv run pytest tests/integration/test_web_api.py -q
```

这是风险最高的切片，发布前必须备份 SQLite 并审计已有 live records。

## Slice 4：derivative contractSize 转换

**Files:**

- `src/exchange/base.py` — `_parse_position_snapshot`
- `tests/unit/test_exchange_base.py`
- `tests/integration/test_exchange_adapter.py`

**Implementation:**

- [ ] derivative position amount 使用 `abs(contracts) * contractSize`。
- [ ] contractSize 优先取 market metadata，其次取 exchange row/info，缺省才使用安全默认 1。
- [ ] spot amount 不乘 contractSize。
- [ ] 所有 notional、风险、减仓、Dashboard exposure 统一消费转换后的 base amount。

**Tests:**

- contracts=100、contractSize=0.01 得到 amount=1。
- 缺少 contractSize 时明确默认行为。
- spot/futures 分支不混淆。

## Slice 5：accepted order 与 post-submit sync 解耦

**Files:**

- `src/order/manager.py` — `UnifiedOrderManager.submit`
- `src/web/api/strategies.py` — live manager callback
- `tests/unit/test_order_router.py`
- `tests/integration/test_web_api.py`

**Implementation:**

- [ ] router 返回 exchange order ID 后视为 exchange accepted。
- [ ] 先持久化 order，再执行 post-submit sync。
- [ ] post-submit sync 失败不改为 rejected、不向调用方伪装为 submit failure、不触发策略停止；保持 exchange ID 和 accepted 状态。
- [ ] 记录 `post_submit_sync_failed` warning/risk event，后续 private sync 负责修正订单状态。
- [ ] 真正 router submit exception 仍按失败传播。

**Tests:**

- exchange accepted + sync failure 保持 accepted/filled reconciliation path。
- router submit failure 仍返回失败。
- callback/strategy 不被错误停止。

## Slice 6：策略命名、回测解析和 exclusive bounds

**Files:**

- `src/strategy/registry.py`
- `src/strategy/definitions.py`
- `src/web/api/strategies.py`
- `src/web/api/backtest.py`
- `frontend/src/types/strategy.ts`
- `frontend/src/components/StrategyForm.vue`
- `tests/unit/test_strategy_registry.py`
- `tests/integration/test_web_api.py`
- 前端 strategy/backtest tests

**Implementation:**

- [ ] 新建/更新/克隆策略名禁止 builtin 名称、`__exchange__`、空白和保留前缀 `__`。
- [ ] create/list/delete 对 legacy conflict 做明确错误或冲突标记，不让 persisted config 静默 shadow builtin。
- [ ] backtest 解析优先 persisted config，再 builtin；同名 legacy config 不静默使用 builtin 默认参数。
- [ ] definition API 序列化 `exclusive_min`/`exclusive_max`，前端表单按真实边界校验。
- [ ] 不自动删除或重命名已有 legacy records。

**Tests:**

- builtin/exchange 保留名拒绝新建和 clone。
- 同名 legacy config 的 list/create/delete/backtest 行为明确且不丢数据。
- exclusive bounds 在 API、前端展示、保存校验中一致。

## Slice 7：生命周期、行情回调和 scoped WebSocket

### 7a. engine/market lifecycle

**Files:** `src/core/engine.py`, `src/market/service.py`, `tests/unit/test_engine.py`, `tests/unit/test_market_service.py`

- [ ] `ensure_started()` 返回明确 lease/release 语义。
- [ ] engine start rollback 和 stop 都释放 lease；共享 service 只在最后一个 lease 释放时 stop。
- [ ] callback 全部成功后再推进 bar cursor；失败时保留 bar 供下轮重试。
- [ ] 添加 callback failure/retry 和 shared lease 生命周期测试。

### 7b. scoped WebSocket

**Files:**

- `src/web/api/strategies.py`
- `src/web/app.py`
- `frontend/src/types/dashboard.ts`
- `frontend/src/stores/dashboard.ts`
- `frontend/src/stores/dashboard.test.ts`
- `tests/integration/test_web_api.py`

**Implementation:**

- [ ] account/positions/snapshot WS 消息增加 `scope: "exchange" | "strategy"`；策略范围消息带 strategy。
- [ ] 前端全局 account/positions 只接受 exchange scope。
- [ ] strategy scope 进入策略明细缓存/消息历史，不覆盖全局 Dashboard。
- [ ] 初始 runtime snapshot 明确为 exchange scope。
- [ ] 缺失 scope 的兼容策略必须明确且限时，不要永久把局部消息当全局快照。

## 全局验证

```bash
uv run pytest -q
uv run ruff check src tests
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

运行时验证：

- 临时 SQLite schema 迁移前后对比。
- 部分成交、取消、重复 trade ID、provider timeout、symbol-scoped sync。
- 两策略共享 live account/同 symbol。
- contractSize=0.01 的 swap exposure。
- exchange accepted + post-submit sync timeout。
- builtin 同名 config、`__exchange__` 名称、exclusive bounds。
- engine ensure_started/stop/restart。
- callback 首次失败后 bar 重试。
- strategy-scoped WS 不覆盖 global account/positions。

## 用户确认项

- 是否接受共享 live 账户下无法证明 ownership 时拒绝交易操作？
- 是否接受新增订单成交字段和 SQLite 迁移？
- 是否接受 callback 重试可能向已成功 subscriber 重复投递同一 bar？
- 是否由管理员人工处理已有 builtin/保留名冲突配置？
