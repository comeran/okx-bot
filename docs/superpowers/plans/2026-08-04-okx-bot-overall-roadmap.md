# OKX Bot Overall Roadmap Implementation Plan

> **状态（2026-08-07）：非策略范围已暂停。** 本文件不再作为当前执行计划。策略配置 CRUD 与四种内建策略运行时硬化已完成，权威完成记录见 [Strategy Runtime Hardening Implementation Plan](2026-08-04-strategy-runtime-hardening.md)。CI、Dashboard、会计扩展、部署、迁移、远程访问、外部 OKX 冒烟与真实资金等里程碑均未因该策略工作获得授权，不得自动开始。

> **历史执行说明：** 只有用户明确重新授权某个暂停里程碑后，才可为该里程碑创建独立详细计划并使用相应执行流程；未授权条目、优先级和检查点仅保留作历史参考，不能据此推断当前授权。

**历史目标：** 在不放宽现有交易安全约束的前提下，把已经完成策略配置 CRUD 的 OKX Bot 推进为可持续验证、可观测、可恢复、适合 paper/demo 长期运行的量化交易工作台。

**历史架构设想：** 后端继续沿用 FastAPI、SQLModel/SQLite、注册表驱动的策略定义、`BotEngine`、统一订单管理、风险门禁和 WebSocket 快照；前端继续沿用 Vue 3、TypeScript、Pinia、Element Plus、Axios、Monaco 和 Vite。路线图不做一次性大改，而是将 CI、策略执行、可观测性、运行时恢复、风险、回测、交易/行情产品、部署安全拆成可独立验收的里程碑。

**技术栈参考：** Python 3.12、FastAPI、SQLModel、SQLite、ccxt、pytest、pytest-asyncio、Ruff、uv；Vue 3、TypeScript、Pinia、Vue Router、Vue I18n、Element Plus、Axios、ECharts、Monaco、Vitest、vue-tsc、Vite。

---

## 1. 已验证基线

- 后端完整回归：`456 passed, 3 warnings`，Ruff 通过；warning 为非阻塞环境/弃用清理项。
- 前端完整回归：`237 passed`，`vue-tsc` 与生产构建通过。
- 已通过真实浏览器验证策略创建、编辑、克隆、删除、JSON/YAML 校验、脏状态保护、启动/停止和 WebSocket 只读切换。
- 策略定义已包含：
  - `ma_cross`
  - `rsi_mean_reversion`
  - `bollinger_mean_reversion`
  - `donchian_breakout`
- `ma_cross` 仍是唯一隐式 legacy runtime 策略；持久化配置通过 `StrategyRegistry.create_instance(...)` 通用实例化。
- 四种 built-in 已统一通过持久化配置 API、真实 `BotEngine` start/stop、状态同步、统一订单管理器、disabled-start 与 mutation-safety 验收；`strategy_type` 与名称在编辑时保持不可变。
- 策略运行时硬化的定向回归为 `2 passed` 与 `18 passed`，聚焦后端套件为 `233 passed, 3 warnings`，最终独立审查结论为 `APPROVED`。
- Paper 会计已经具备现金、仓位、成交、费用、已实现/未实现 PnL、权益和现金账本基础；后续工作是时间窗口正确性、共享价格源、风险输入完整性和产品可见性，而不是从零重写会计。
- Demo 私有同步代码路径已在本地自动化范围内覆盖 divergence risk event 持久化和高风险 kill switch；尚未执行外部 OKX demo/private API 冒烟，后续工作需继续受单独授权门禁约束。
- `/api/health` 当前仅返回 `{"status": "ok"}`。
- 前端 WebSocket 已有自动重连和 REST/WebSocket 竞态保护；市场轮询能容忍异常，但尚无明确的退避状态、数据新鲜度和恢复可观测性。
- 当前 API 使用开放 CORS，未观察到应用级认证；仅适合受控本地环境，不能直接作为公网暴露方案。
- 当前工作区已有大量未提交修改和未跟踪文件。本路线图不得通过 reset、clean、checkout 或覆盖文件来获得“干净环境”。

## 2. 不可破坏的安全与产品语义

- 不执行真实资金交易，不把 `exchange.demo=false` 纳入任何自动化或手动验收。
- 所有外部 OKX demo/private API 冒烟都必须由用户单独明确授权。
- 自动化测试不得访问 OKX 或依赖真实凭证。
- 不输出、记录或取消掩码 API key、secret、passphrase、Telegram token 或本地私密配置。
- 编辑策略时不能改名；名称与策略类型保持只读。
- 克隆功能必须保留；克隆草稿使用空白新名称，且 `enabled=false`。
- 禁用策略不能启动。
- 运行中的策略配置完全只读。
- 存在未平仓仓位时阻止不安全配置变更和删除。
- 不增加持久化策略自动启动；应用重启后默认通过运行时对账回到 stopped/error，而不是自行恢复交易。
- 不移除 `ma_cross` 的唯一隐式 legacy 注册语义。
- 未经用户明确要求，不执行 git commit 或 push。

## 3. 历史优先级和依赖关系

> 以下顺序记录路线图最初的分解方式。除已完成的文档同步与策略运行时硬化外，其余条目均保持暂停；编号、依赖和检查点不构成开始工作的授权。

### P0：保护当前成果

1. 文档基线同步（已完成，2026-08-07）。
2. CI 基线（暂停，未授权自动开始）。
3. 四种 built-in 持久化执行硬化（已由专用策略计划完成）。

### P1：让运行时可运维、可恢复、风险输入可信

4. 健康检查、风险事件和运维 UI。
5. 行情/WebSocket/进程生命周期恢复。
6A. Rolling PnL 与 mark-to-market 正确性。
6B. 回撤、保证金和衍生品风险闭环。

### P2：扩展研究和日常使用能力

7. 回测分析。
8A. 交易与订单历史。
8B. 行情分析。

### P3：可重复部署和受控暴露

9A. 打包、备份与运行手册。
9B. CORS、绑定范围与访问控制。
10. 经单独授权的 OKX demo 私有接口冒烟。

依赖约束：

- 里程碑 3 原计划依赖 CI 基线；专用策略计划后来以聚焦回归、完整回归、Ruff 和独立审查替代该前置条件并已完成交付。这不表示里程碑 2 已完成，也不授权开始 CI 工作。
- 里程碑 5 依赖里程碑 4 提供连接状态、错误和数据新鲜度的观察面。
- 里程碑 6A 依赖稳定的行情新鲜度定义；6B 依赖 6A 的权益、PnL 和峰值口径。
- 里程碑 8B 的实时订单簿/流式行情依赖里程碑 5；静态指标和 watchlist 可提前独立实施。
- 里程碑 10 至少依赖 3、4、5、6、9A 全部通过，并仍需用户再次明确授权。
- P2 可在 P1 稳定接口之后并行推进，但每个子项目使用独立计划和独立验收。

### 历史交付检查点

> 检查点仅说明原路线图的验收边界；除已完成项外，当前均未激活。

- **Checkpoint A — 基线保护：** 里程碑 1–2。只同步事实并建立 CI；不改变交易行为。
- **Checkpoint B — 策略执行信心：** 里程碑 3。单独验收四种 built-in 的持久化 lifecycle，再进入 runtime 扩展。
- **Checkpoint C — 可运维运行时：** 里程碑 4–5。先建立观察面，再实现退避、重连、优雅关闭和启动对账。
- **Checkpoint D — 风险正确性：** 里程碑 6A–6B。先锁定会计与价格口径，再把它们接入 drawdown、衍生品状态和 kill switch。
- **Checkpoint E — 产品扩展：** 里程碑 7、8A、8B。回测、交易历史和行情分别交付，不合并成一个大型 UI 变更。
- **Checkpoint F — 部署边界：** 里程碑 9A–9B。先证明可备份恢复，再允许通过受控 reverse proxy 暴露。
- **Checkpoint G — 外部 demo 验证：** 里程碑 10。仅在前置检查点完成且用户当次明确授权后执行。

若用户未来明确重启某个暂停里程碑，则只执行该里程碑的独立计划，并在完成后运行定向测试和全量回归、检查数据迁移/回滚说明；不得据此自动进入下一个里程碑，也不把整个路线图打包为一次原子变更。

---

## Milestone 1：同步项目文档基线

**状态：** 已完成（2026-08-07）。

**目标：** 消除 `overview.md` 中已经过时的 CRUD、测试数量和优先级描述。

**文件：**

- Modify: `overview.md`
- Reference: `docs/superpowers/plans/2026-08-04-okx-bot-overall-roadmap.md`

**任务：**

- [x] 将后端基线更新为 `456 passed, 3 warnings`，并记录前端 237、类型检查、生产构建、Ruff 和浏览器 CRUD/lifecycle 验证。
- [x] 将策略配置前端 CRUD UX 标记为完成。
- [x] 记录四种 built-in 的持久化实例化、start/stop/status、统一订单管理器与安全契约均已通过验收。
- [x] 明确克隆仍受支持，且新克隆默认空名称、禁用。
- [x] 将 demo 冒烟从默认下一步改为单独授权门禁，并明确非策略工作整体暂停。
- [x] 将 rolling PnL、mark-to-market 和私有同步描述修正为“已有基础、待正确性与运维闭环加强”，避免暗示它们完全不存在。

**验证：**

```bash
git diff -- overview.md docs/superpowers/plans/2026-08-04-okx-bot-overall-roadmap.md
git diff --check -- overview.md docs/superpowers/plans/2026-08-04-okx-bot-overall-roadmap.md
```

**完成标准：** 文档与当前代码/测试事实一致，未把任何未来里程碑提前标记为完成。

**不包含：** 业务代码、数据库、外部接口、提交或推送。

---

## Milestone 2：建立无外部依赖的 CI 基线

**状态：** 暂停，未授权自动开始；Milestone 3 的完成不改变该状态。

**目标：** 在继续修改策略和运行时之前，为现有验证基线建立每次 PR 都可重复执行的保护网。

**文件：**

- Create: `.github/workflows/ci.yml`
- Modify: `README.md`
- Reference: `pyproject.toml`
- Reference: `frontend/package.json`
- Reference: `uv.lock`
- Reference: `frontend/package-lock.json`

**任务：**

- [ ] 建立 backend job：安装 Python/uv，执行 Ruff 和完整 pytest。
- [ ] 建立 frontend job：使用 Node 22 LTS，执行 `npm ci`、完整 Vitest、`vue-tsc` 和生产构建。
- [ ] 明确测试环境只使用临时/测试 SQLite 和 mock adapter，不注入 OKX 凭证。
- [ ] 为 workflow 增加依赖缓存，但不缓存数据库、`.env`、本地 settings 或构建秘密。
- [ ] 在 README 记录与 CI 完全一致的本地命令。

**验证：**

```bash
uv run --group dev ruff check .
uv run --group dev pytest -q
npm --prefix "frontend" run test -- --run
npm --prefix "frontend" run type-check
npm --prefix "frontend" run build
```

**完成标准：** backend/frontend job 独立通过；workflow 不读取 secrets、不访问 OKX、不运行 demo/private sync。

**不包含：** Docker 发布、部署、浏览器 E2E、真实交易所 smoke。

---

## Milestone 3：硬化四种内建策略的持久化执行路径

**状态：** 已完成（2026-08-07）；详细任务与完成证据见 [Strategy Runtime Hardening Implementation Plan](2026-08-04-strategy-runtime-hardening.md)。

**目标：** 用端到端集成测试证明四种内建策略都能通过共享持久化路径完成创建、启动、状态更新、订单管理器注入和停止。

**文件：**

- Modify: `tests/integration/test_web_api.py`
- Modify: `tests/unit/test_strategy_registry.py`
- Modify: `tests/unit/test_builtin_strategies.py`
- Modify only when a failing test proves necessary:
  - `src/web/api/strategies.py`
  - `src/strategy/registry.py`
  - `src/core/engine.py`
  - `src/strategy/builtin/rsi_mean_reversion.py`
  - `src/strategy/builtin/bollinger_mean_reversion.py`
  - `src/strategy/builtin/donchian_breakout.py`

**任务：**

- [x] 为四种 built-in 增加持久化配置 API 与 start/stop 集成覆盖。
- [x] 断言启动时使用保存的 symbol、timeframe 和 params 创建正确实例。
- [x] 断言实例获得统一订单管理器并使用真实 `BotEngine` 生命周期。
- [x] 断言 runtime API 与 WebSocket 状态按 starting/running/stopped/error 正确对账。
- [x] 对四种策略统一验证 disabled-start、running/starting readonly 与 open-position mutation guard。
- [x] 断言隐式列表仍只有 `ma_cross`；RSI/Bollinger/Donchian 仅从持久化配置实例化。
- [x] 通过聚焦回归修复编辑时可替换 `strategy_type` 的共享路径缺口，未重构整个 runtime。

**验证：**

```bash
uv run --group dev pytest \
  tests/unit/test_strategy_registry.py \
  tests/unit/test_builtin_strategies.py \
  tests/unit/test_strategy_backtest_smoke.py \
  tests/integration/test_web_api.py -q
uv run --group dev pytest -q
uv run --group dev ruff check .
```

涉及前端状态或按钮语义时追加：

```bash
npm --prefix "frontend" run test -- --run \
  src/stores/strategies.test.ts \
  src/views/Strategy.test.ts
npm --prefix "frontend" run type-check
npm --prefix "frontend" run build
```

**完成记录：** 策略类型回归 `2 passed`，六项共享契约矩阵 `18 passed`，聚焦后端 `233 passed, 3 warnings`，完整后端 `456 passed, 3 warnings`；Ruff 通过，独立最终审查为 `APPROVED`。

**完成标准：** 已达到。四种 built-in 均有持久化 lifecycle 覆盖；现有 CRUD 和交易安全语义无回归；自动化测试未发出外部 OKX 请求。

**不包含：** 新策略、新 DSL 算子、自动启动、真实 demo/live 调用。

---

## Milestone 4：健康检查、风险事件和运维 UI

**目标：** 让操作者能从 API 和 Dashboard 判断数据库、策略 runtime、行情新鲜度、kill switch、私有同步和风险事件状态。

**文件：**

- Modify: `src/web/app.py`
- Modify: `src/web/api/ops.py`
- Modify: `src/web/api/trading.py`
- Modify: `src/data/repository.py`
- Modify only if the response contract needs stored fields: `src/data/models.py`
- Create: `frontend/src/services/ops.ts`
- Create: `frontend/src/services/ops.test.ts`
- Modify: `frontend/src/views/Dashboard.vue`
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/stores/dashboard.ts`
- Modify: `frontend/src/stores/dashboard.test.ts`
- Modify: `frontend/src/types/dashboard.ts`
- Test: `tests/integration/test_web_api.py`
- Test: `tests/unit/test_private_sync.py`

**任务：**

- [ ] 扩展 `/api/health`：返回 DB 可访问性、paper/demo 模式、kill switch、running/starting/error 策略数量、市场数据状态、最后行情时间和最后风险事件时间；不得包含任何 secret。
- [ ] 使用已经持久化的 `RiskEventRecord` 增加最近风险事件只读查询；若 repository 尚无查询 helper，只增加最小的倒序限量查询。
- [ ] 为最近一次 private sync 暴露安全摘要：执行时间、同步 section/count、divergence 数量和最高严重级别；不返回原始私有 payload。
- [ ] Dashboard 展示 health、WebSocket 最后消息年龄、市场数据新鲜度、kill switch 和最近风险事件。
- [ ] Settings 或 Dashboard 提供 kill switch 操作和 demo-only private sync 手动入口；在非 demo 模式明确禁用并解释原因。
- [ ] 保留现有 `src/ops/private_sync.py` 的 risk-event 持久化和高风险自动 kill-switch 语义，不重复实现第二套事件存储。

**验证：**

```bash
uv run --group dev pytest \
  tests/integration/test_web_api.py \
  tests/unit/test_repository.py \
  tests/unit/test_private_sync.py -q
npm --prefix "frontend" run test -- --run \
  src/stores/dashboard.test.ts \
  src/services/ops.test.ts
npm --prefix "frontend" run type-check
npm --prefix "frontend" run build
```

UI 完成后启动本地服务，用浏览器验证 health、kill switch、空风险事件、风险事件列表、demo/private-sync 禁用态和移动端布局。浏览器验证不得触发外部 private sync。

**完成标准：** 操作者无需查看日志即可判断核心状态；API 响应无敏感字段；高风险同步差异仍先触发 kill switch 再写事件。

**不包含：** 自动定时 private sync、新告警渠道、真实 OKX 调用、自动解除 kill switch。

---

## Milestone 5：运行时连接韧性和状态恢复

**目标：** 在网络波动、WebSocket 重连、市场数据异常和应用退出/重启时提供明确、可测试、可观察的行为。

**文件：**

- Modify: `src/core/engine.py`
- Modify: `src/market/service.py`
- Modify: `src/web/ws.py`
- Modify: `src/web/app.py`
- Modify: `frontend/src/composables/useWebSocket.ts`
- Modify: `frontend/src/composables/useWebSocket.test.ts`
- Modify: `frontend/src/stores/dashboard.ts`
- Modify: `frontend/src/stores/strategies.ts`
- Test: `tests/unit/test_engine.py`
- Test: `tests/unit/test_market_service.py`
- Test: `tests/integration/test_web_api.py`

**任务：**

- [ ] 为市场服务定义 `connected/reconnecting/stale/stopped/error` 状态、retry count、last message、last success 和 last error。
- [ ] 将当前固定轮询异常容忍改为有上限、带抖动且可注入时钟的指数退避；成功获取数据后重置退避。
- [ ] 保留 `watch_ohlcv` 对 `NotSupported` 的 `fetch_ohlcv` fallback，但统一记录连接状态和数据新鲜度。
- [ ] 前端 WebSocket 自动重连加入 bounded backoff、可见状态和最后成功连接时间。
- [ ] 每次 WebSocket 重连后由服务端发送完整 snapshot；前端继续使用 revision/tombstone/authority guard，确保旧快照不能覆盖更新的 CRUD/lifecycle 事件。
- [ ] FastAPI lifespan shutdown 必须停止所有 `BotEngine`、市场服务和广播任务，并有超时测试。
- [ ] 应用启动时将无实际 engine 的持久化配置对账为 stopped/error；不引入 auto-start。
- [ ] 明确热加载规则：running 配置不可改，stopped 配置在下一次 start 时读取最新值。

**验证：**

```bash
uv run --group dev pytest \
  tests/unit/test_engine.py \
  tests/unit/test_market_service.py \
  tests/integration/test_web_api.py -q
npm --prefix "frontend" run test -- --run \
  src/composables/useWebSocket.test.ts \
  src/stores/dashboard.test.ts \
  src/stores/strategies.test.ts
uv run --group dev ruff check .
npm --prefix "frontend" run type-check
npm --prefix "frontend" run build
```

**完成标准：** 退避与恢复使用虚拟时钟即可稳定测试；断线状态在 health/UI 可见；重连不会回滚新状态；优雅关闭不遗留运行 engine。

**不包含：** 失败策略自动重启、多进程协调、分布式锁、交易所容灾切换。

---

## Milestone 6A：Rolling PnL 与 mark-to-market 正确性

**目标：** 在现有 paper 会计基础上统一 rolling 24h 净已实现 PnL、未实现 PnL、权益和价格新鲜度口径。

**文件：**

- Modify: `src/order/accounting.py`
- Modify: `src/order/mark_to_market.py`
- Modify: `src/data/repository.py`
- Modify only for proven contract gaps: `src/data/models.py`
- Modify: `src/web/api/trading.py`
- Modify: `frontend/src/types/dashboard.ts`
- Modify: `frontend/src/stores/dashboard.ts`
- Modify: `frontend/src/views/Dashboard.vue`
- Test: `tests/unit/test_paper_accounting.py`
- Test: `tests/unit/test_repository.py`

**任务：**

- [ ] 用 `CashLedgerRecord`/trade 时间戳计算滚动 24 小时净已实现 PnL，明确手续费是否已计入并在测试中锁定。
- [ ] 建立 account/strategy 维度的一致聚合，避免 lifetime realized PnL 被误当成 daily PnL。
- [ ] 确认 `PaperMarkToMarketService.mark_update(...)` 是未实现 PnL 和权益更新的单一入口；删除经测试确认的重复计算，而不是新增第二套公式。
- [ ] 保存或暴露 mark price 与更新时间，使 stale price 可以被 health/risk 层识别。
- [ ] 用 long、short、partial close、flip、fee、跨 24h 边界和 stale mark 的表格化测试锁定会计不变量。
- [ ] Dashboard 明确区分 realized、unrealized、rolling 24h、fees 和 equity。

**验证：**

```bash
uv run --group dev pytest \
  tests/unit/test_paper_accounting.py \
  tests/unit/test_repository.py \
  tests/integration/test_web_api.py -q
npm --prefix "frontend" run test -- --run src/stores/dashboard.test.ts
uv run --group dev pytest -q
npm --prefix "frontend" run build
```

**完成标准：** rolling 24h 结果可由账本事件重算；权益满足当前 `cash_balance + signed open-position cost basis + unrealized_pnl` 契约；stale mark 不被静默当作新价格。

**不包含：** 修改真实交易所余额、设置杠杆/保证金模式、真实资金损益核验。

---

## Milestone 6B：回撤、保证金和衍生品风险闭环

**目标：** 让现有 `MaxDailyLossRule`、`MaxDrawdownRule` 和 kill switch 使用完整、可解释的风险输入，并覆盖衍生品运营字段。

**文件：**

- Modify: `src/risk/manager.py`
- Modify: `src/risk/rules.py`
- Modify: `src/order/manager.py`
- Modify: `src/exchange/live_sync.py`
- Modify: `src/ops/private_sync.py`
- Modify: `src/data/models.py`
- Modify: `src/data/repository.py`
- Modify: `frontend/src/types/dashboard.ts`
- Modify: `frontend/src/views/Dashboard.vue`
- Test: `tests/unit/test_risk_rules.py`
- Test: `tests/unit/test_order_manager_kill_switch.py`
- Test: `tests/unit/test_live_sync.py`
- Test: `tests/unit/test_private_sync.py`

**任务：**

- [ ] 持久化并更新 peak equity，使 max drawdown 在重启后保持一致。
- [ ] 将 rolling daily loss、drawdown、总仓位和 stale mark 状态作为明确的 risk context 输入。
- [ ] 对齐 account/position snapshot、SQLModel 和 API 中的 leverage、margin mode、position mode、margin ratio、liquidation price；只增加当前 contract 缺失的字段。
- [ ] 为阈值越界生成持久化 risk event；高严重级别使用现有 kill switch 自动阻断新订单和策略启动。
- [ ] 自动触发的 kill switch 必须保留原因、时间和来源，且只允许显式人工解除。
- [ ] Dashboard 展示当前 breach、阈值、观测值、来源和时间，不增加真实交易参数写入入口。

**验证：**

```bash
uv run --group dev pytest \
  tests/unit/test_risk_rules.py \
  tests/unit/test_order_manager_kill_switch.py \
  tests/unit/test_live_sync.py \
  tests/unit/test_private_sync.py \
  tests/integration/test_web_api.py -q
uv run --group dev pytest -q
uv run --group dev ruff check .
npm --prefix "frontend" run test -- --run src/stores/dashboard.test.ts
npm --prefix "frontend" run build
```

**完成标准：** risk rule 的输入可追溯；重启不重置 peak equity；严重 breach 自动进入 kill switch；现有 live-order safety gate 未被放宽。

**不包含：** 向 OKX 写入 leverage/margin/position mode、自动平仓、真实资金阈值调优。

---

## Milestone 7：回测配置复用和分析能力

**目标：** 使用持久化策略配置运行回测，并补齐 equity/drawdown 曲线、丰富交易明细和有限多 run 对比。

**文件：**

- Modify: `src/web/api/backtest.py`
- Modify: `src/backtest/engine.py`
- Modify: `src/backtest/report.py`
- Modify: `src/data/models.py`
- Modify: `src/data/repository.py`
- Modify: `frontend/src/services/backtest.ts`
- Modify: `frontend/src/types/backtest.ts`
- Modify: `frontend/src/views/Backtest.vue`
- Create: `frontend/src/views/Backtest.test.ts`
- Test: `tests/unit/test_backtest_engine.py`
- Test: `tests/unit/test_report.py`
- Test: `tests/integration/test_backtest_flow.py`

**任务：**

- [ ] 在 backtest request 中支持 `strategy_config_name`；与直接 strategy type/params 模式互斥，并通过 `StrategyRegistry.create_instance(...)` 实例化。
- [ ] 让 `POST /api/backtest/run` 返回 `result_id` 和现有 metrics，前端可直接加载详情。
- [ ] 按 `result_id` 持久化 equity 与 drawdown point；保留当前 result、klines、markers 和 trade persistence。
- [ ] 扩展详情响应，包含 result、klines、markers、equity curve、drawdown curve 和 rich trades。
- [ ] rich trade 至少包含时间、方向、数量、入场/退出价格、费用、PnL 和持有时长；字段不可推导时返回明确空值，不伪造数据。
- [ ] 前端增加 Kline、equity、drawdown、trade list 标签页，并支持最多三个历史 run 的指标/曲线对比。
- [ ] 现有直接运行 `ma_cross` 的流程继续通过同一测试套件，不增加临时兼容层。

**验证：**

```bash
uv run --group dev pytest \
  tests/unit/test_backtest_engine.py \
  tests/unit/test_report.py \
  tests/integration/test_backtest_flow.py \
  tests/integration/test_web_api.py -q
npm --prefix "frontend" run test -- --run src/views/Backtest.test.ts
npm --prefix "frontend" run type-check
npm --prefix "frontend" run build
```

UI 完成后在浏览器运行固定历史数据回测，验证 persisted config、结果详情、空交易、单笔交易、多 run 选择和窄屏布局。

**完成标准：** 持久化配置可复用于回测；曲线和 trade 明细可重新加载；对比无需重新运行策略。

**不包含：** 网格搜索、参数优化、蒙特卡洛、live replay、外部数据供应商扩展。

---

## Milestone 8A：交易与订单历史查询

**目标：** 将当前基础交易页升级为可筛选、可分页、可区分 open/history 的运营视图。

**文件：**

- Modify: `src/web/api/trading.py`
- Modify: `src/data/repository.py`
- Modify: `frontend/src/services/trades.ts`
- Modify: `frontend/src/types/trades.ts`
- Modify: `frontend/src/views/Trades.vue`
- Test: `tests/integration/test_web_api.py`
- Create: `frontend/src/views/Trades.test.ts`

**任务：**

- [ ] 为 orders/trades 增加 strategy、symbol、side、status、时间范围、limit、offset 查询参数。
- [ ] repository 使用确定性排序和边界测试，分页不能重复或遗漏相同时间戳记录。
- [ ] 响应返回总数和当前页信息，前端不依赖一次加载全部记录。
- [ ] UI 分离 open orders、order history 和 trades，并保留空态、加载态、错误态。
- [ ] 筛选条件写入 URL query，刷新后可恢复，但不把敏感信息写入 URL。

**验证：**

```bash
uv run --group dev pytest tests/integration/test_web_api.py tests/unit/test_repository.py -q
npm --prefix "frontend" run test -- --run src/views/Trades.test.ts
npm --prefix "frontend" run type-check
npm --prefix "frontend" run build
```

**完成标准：** 大量记录下仍按页读取；筛选结果与 repository 一致；open/history 定义有测试锁定。

**不包含：** 从交易页修改策略、批量取消真实订单、导出税务报表。

---

## Milestone 8B：行情 watchlist、指标和受控实时数据

**目标：** 在不增加交易操作面的情况下增强市场观察能力。

**文件：**

- Modify: `src/web/api/market.py`
- Modify: `src/market/service.py`
- Modify: `frontend/src/services/market.ts`
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/views/Market.vue`
- Test: `tests/unit/test_market_service.py`
- Test: `tests/integration/test_web_api.py`
- Create: `frontend/src/views/Market.test.ts`

**任务：**

- [ ] 先实现本地 watchlist 和基于现有 Kline 的 MA、RSI、MACD、Bollinger overlay；不依赖新后端流。
- [ ] 在里程碑 5 完成后，复用其连接状态和重连策略增加实时 ticker/Kline 更新。
- [ ] 增加 order book 只读端点和 UI，adapter 在测试中完全 mock；定义 snapshot 时间和 stale 状态。
- [ ] 对 symbol、market type、timeframe 和指标参数建立可恢复的页面状态。
- [ ] 限制 chart 数据点和 order book 深度，避免无限增长和浏览器内存泄漏。

**验证：**

```bash
uv run --group dev pytest tests/unit/test_market_service.py tests/integration/test_web_api.py -q
npm --prefix "frontend" run test -- --run src/views/Market.test.ts
npm --prefix "frontend" run type-check
npm --prefix "frontend" run build
```

**完成标准：** watchlist/指标无需网络即可测试；实时数据能显示 freshness/reconnecting/stale；页面没有下单入口。

**不包含：** Level-3 order book、策略拖拽构建、交易按钮、真实资金操作。

---

## Milestone 9A：部署打包、备份恢复和运行手册

**目标：** 为 paper/demo 单实例部署提供可重复构建、持久卷、升级前备份和故障恢复流程。

**文件：**

- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `docs/operations/runbook.md`
- Create: `docs/operations/backup-restore.md`
- Modify: `README.md`
- Reference: `src/core/config.py`
- Reference: `src/core/runtime_settings.py`

**任务：**

- [ ] 构建后端与前端产物，并明确采用单容器静态托管或双服务 compose；里程碑设计阶段只选择一种，不同时维护两套生产路径。
- [ ] 将 SQLite/data 和本地配置映射为独立持久卷；镜像中不包含 `data/`、`.env`、settings.local 或凭证。
- [ ] 记录启动、停止、升级、日志、health、kill switch 演练和优雅关闭步骤。
- [ ] 备份说明覆盖 `bot.db`、`bot.db-wal`、`bot.db-shm` 的一致性快照，恢复前必须停止写入进程。
- [ ] 增加从备份恢复到临时目录并运行 repository/API smoke 的演练步骤。
- [ ] 默认 compose 使用 paper/demo 安全配置，不包含真实凭证样例。

**验证：**

```bash
docker build -t okx-bot:local .
docker compose config
uv run --group dev pytest -q
npm --prefix "frontend" run build
```

本地 compose smoke 只能使用无凭证的 paper/demo 配置，并验证重启后 SQLite 数据仍存在。

**完成标准：** 新环境可按文档启动；数据卷可备份和恢复；停止过程优雅；镜像和 compose 不含 secret。

**不包含：** Kubernetes、Helm、云厂商资源、托管数据库、高可用多实例。

---

## Milestone 9B：受控暴露与访问安全

**目标：** 阻止当前开放 CORS、无认证 API 被误部署到不可信网络。

**文件：**

- Modify: `src/web/app.py`
- Modify: `src/core/config.py`
- Modify: `src/core/runtime_settings.py`
- Modify: `README.md`
- Modify after Milestone 9A creates it: `docs/operations/runbook.md`
- Create: `docs/operations/security.md`
- Test: `tests/unit/test_config.py`
- Test: `tests/integration/test_web_api.py`

**任务：**

- [ ] 默认后端绑定 loopback 或受控容器网络，文档禁止直接公网监听。
- [ ] 将 CORS origins 改为配置化 allowlist；默认只允许本地前端来源，测试拒绝未授权 origin。
- [ ] 对外暴露时推荐由同源 reverse proxy 提供 TLS 和 operator authentication，不在首版自建复杂用户系统。
- [ ] WebSocket 与 REST 必须位于同一访问控制边界，不能只保护 REST。
- [ ] 增加启动时安全检查：production/exposed 配置若仍为 wildcard CORS 或缺少访问控制说明，则拒绝启动或明确失败。
- [ ] 安全文档包含 secret 注入、日志脱敏、备份权限、kill switch、demo/private-sync 边界和升级回滚。

**验证：**

```bash
uv run --group dev pytest tests/unit/test_config.py tests/integration/test_web_api.py -q
uv run --group dev ruff check .
```

**完成标准：** 默认安装不能被意外公网暴露；REST/WebSocket 同受保护；settings 和 health 不泄露 secret。

**不包含：** 多租户、RBAC、OAuth provider、自建账户注册、云 IAM 集成。

---

## Milestone 10：单独授权的 OKX demo 私有接口冒烟

**目标：** 在所有自动化和运维前置条件通过后，验证现有 demo-only private sync 与安全门禁在真实 OKX demo 环境中的行为。

**文件：**

- Create: `docs/operations/demo-smoke.md`
- Reference: `src/web/api/ops.py`
- Reference: `src/ops/private_sync.py`
- Reference: `src/exchange/live_sync.py`
- Reference: `src/order/manager.py`
- Reference: `config/settings.yaml`

**执行门禁：**

- [ ] 用户在执行当次明确授权 OKX demo/private API 调用。
- [ ] 确认凭证属于 demo 账户，`exchange.demo=true`。
- [ ] 确认 `risk.allow_live_open_orders=false`、`risk.live_max_order_notional=0`。
- [ ] 先完成数据库备份并测试 kill switch。
- [ ] 先执行自动化：

```bash
uv run --group dev pytest \
  tests/unit/test_private_sync.py \
  tests/unit/test_live_sync.py \
  tests/unit/test_order_manager_kill_switch.py \
  tests/integration/test_web_api.py -q
```

- [ ] 经授权后仅执行 demo account/open orders/trades sync，核对幂等、divergence、risk event、通知和 kill switch。
- [ ] 冒烟后清点本地数据库变更和运行状态，不留下运行策略或临时开仓权限。

**完成标准：** demo sync 可重复执行且幂等；高风险 divergence 会阻断操作；没有真实资金请求；敏感信息未进入日志或文档。

**不包含：** `exchange.demo=false`、真实下单、真实开仓、真实资金验证。真实资金启用必须创建新的设计、计划和授权流程，不属于本路线图的可执行阶段。

---

## 4. 未来经授权里程碑的统一交付门槛

> 本节仅适用于用户未来明确授权并重启的独立里程碑，不构成开始任何暂停工作的授权。

未来经授权开始某个里程碑前：

- 读取 `git status --short`，记录而不是清理已有 dirty state。
- 为该里程碑创建独立的 `docs/superpowers/plans/YYYY-MM-DD-<milestone>.md` 详细计划。
- 先写失败测试，再写满足测试的最小实现。
- 数据库 schema 变化前明确迁移、备份和回滚路径。

涉及后端的里程碑结束时：

```bash
uv run --group dev ruff check .
uv run --group dev pytest -q
```

涉及前端的里程碑结束时：

```bash
npm --prefix "frontend" run test -- --run
npm --prefix "frontend" run type-check
npm --prefix "frontend" run build
```

涉及 UI 的里程碑还必须启动开发服务，在真实浏览器验证主路径、空态、错误态、只读/禁用态和移动端，并检查 console 与网络请求。仅有类型检查和单元测试不算 UI 完成。

未经用户明确要求，不执行 commit 或 push。

## 5. 回滚与数据安全

- SQLite schema 改动优先使用 additive migration 和可计算默认值；不通过删除数据库解决迁移失败。
- 备份必须覆盖 `data/bot.db` 及其 WAL/SHM 一致性状态；运行中直接复制文件不是默认恢复方案。
- runtime/risk 变更的回滚优先恢复上一版代码；只有 schema 已变化且无法向后读取时才使用经验证的数据库备份。
- 不使用 `git reset --hard`、`git clean`、`git checkout .` 或覆盖无关文件作为回滚手段。
- private sync 保持幂等和 demo-only；自动化测试全部使用 mock adapter。
- 任一测试失败时保留当前里程碑为未完成，先定位根因，不绕过 hook、lint 或安全门禁。

## 6. 明确延期与排除项

以下内容不在当前整体路线图的默认执行范围：

- 真实资金启用或任何 `exchange.demo=false` 操作。
- 失败策略自动重启或持久化策略自动启动。
- 多进程/分布式 runtime、分布式锁、主从切换。
- 策略 DSL 新算子和可视化策略编排。
- 参数优化、网格搜索、蒙特卡洛或 walk-forward 平台。
- 自动平仓、自动修改杠杆/保证金/仓位模式。
- Kubernetes、Helm、云资源和多租户/RBAC。
- 在运行时韧性完成前引入无界实时 order-book 流。

这些事项如需启动，必须作为新的独立设计和计划处理，不能静默并入上述里程碑。
