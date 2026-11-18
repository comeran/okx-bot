# OKX Bot

面向 OKX 的量化交易工作台，提供策略配置、现货历史回测、行情浏览、账户同步、资金分配和交易运行控制。后端使用 Python / FastAPI，前端使用 Vue 3 / TypeScript，数据保存在本地 SQLite。

**当前处于模拟盘联调阶段。** 已有 OKX 模拟盘和实盘下单路径，但成交终态与资金账本对账、手工改单风控、衍生品资金执行仍有未解决的问题，尚未完成完整交易闭环验收。具体边界见下方“已知限制”。

## 当前能力

| 模块 | 已实现内容 | 当前边界 |
| --- | --- | --- |
| 工作台 | 账户资产、订单、持仓、成交、策略状态与运行告警；中英文界面 | 账户快照、策略账本和回测结果分别展示，不能相互替代 |
| 行情与市场 | 现货、永续、交割、期权的标的查询和按标的路由 | 访问能力取决于 OKX 账户权限；策略行情目前使用 REST 轮询 |
| 策略 | 创建和编辑实例、参数校验、启停控制、YAML 配置与规则条件 | 运行状态不代表成交及资金已完成对账 |
| 历史回测 | 现货 K 线缓存与补齐、手续费及滑点、收益指标、权益与回撤曲线、成交明细及样本外回测 | 当前回测仅支持现货 |
| 资金分配 | 预算草稿、只读就绪检查、本地 paper 激活、交易所 funded 激活和订单资金预留 | 保存草稿与就绪预览不会自动激活；衍生品 funded 执行尚不完整 |
| 手工交易 | 提交、撤单、改价入口 | 改价的风控与订单标识处理仍待修复 |
| 运行控制 | 开仓暂停、kill switch、撤单、平仓、引擎停止、恢复检查与审计 | 健康状态为 fresh / ready 不等于账实一致 |

内置四类策略：

- `ma_cross`：均线交叉。
- `rsi_mean_reversion`：RSI 均值回归。
- `bollinger_mean_reversion`：布林带均值回归。
- `donchian_breakout`：唐奇安通道突破。

另外提供 `rule` 策略，可用 `sma`、`ema`、`rsi` 和比较条件描述买卖规则。示例 YAML 见 [example_ma_cross.yaml](config/strategies/example_ma_cross.yaml)。

## 本地启动

### 环境与依赖

- Python **3.12+** 和 `uv`。
- Node.js **22** 和 npm；仓库 CI 使用 Node.js 22。
- 获取历史行情、账户数据及交易所执行需要能够访问 OKX。

在仓库根目录安装依赖：

```bash
uv sync --frozen --group dev
npm --prefix frontend ci
```

### 启动服务

终端一，启动后端：

```bash
uv run uvicorn src.web.app:app --host 127.0.0.1 --port 8080
```

终端二，启动前端：

```bash
npm --prefix frontend run dev -- --host 127.0.0.1
```

打开 [工作台](http://127.0.0.1:3000)。后端提供 [健康检查](http://127.0.0.1:8080/api/health) 和 [API 文档](http://127.0.0.1:8080/docs)。Vite 将 `/api`、`/ws` 代理到 `127.0.0.1:8080`。

修改后端代码时可在启动命令后添加 `--reload`；策略运行期间应使用不带自动重载的命令。前端默认脚本监听 `0.0.0.0`，上面的命令将其限制在本机。

仓库默认运行模式为 `backtest`。历史回测使用公共行情，不需要私有 API 凭据；交易所账户与交易功能需要配置凭据。

## 账户、模式与市场配置

### 执行模式

在“设置”页选择运行模式，页面据此确定 OKX 环境：

| `mode` | 执行方式 | OKX 环境 |
| --- | --- | --- |
| `backtest` | 本地历史回测 | 回测行情来自公共接口 |
| `paper` | 本地模拟撮合 | 本地成交，不向 OKX 提交交易订单 |
| `demo` | 向 OKX 模拟盘提交订单并读取交易所回报 | `exchange.demo: true` |
| `live` | 向 OKX 真实账户提交订单 | `exchange.demo: false` |

`demo` 与 `live` 的模式和环境必须一致。旧配置若包含 `mode: live` 与 `exchange.demo: true` 等冲突，保存设置及启动策略会拒绝；需在设置页重新选择模式并保存。

模拟盘内部复用交易所执行路径，部分记录会标为 `runtime_mode=live`；判断真实盘或模拟盘应结合账户作用域中的 `environment`，不能只看该内部字段。

选择 `live` 并配置完整凭据后，策略和手工交易具备真实下单路径。当前实现中，`allow_live_open_orders: false` 不能作为独立阻止所有实盘开仓的保证。

### 一组凭据，多种市场

API Key、Secret、Passphrase 在账户层配置一次。市场类型在行情页、策略编辑器和手工订单中选择，由完整标的确定路由：

- `spot`：现货，例如 `BTC-USDT`。
- `swap`：永续，例如 `BTC-USDT-SWAP`。
- `future`：交割合约。
- `option`：期权。

账户适配器按四类市场读取数据；同一组凭据可复用，实际可用标的及操作仍受账户权限限制。四市场路由已实现，衍生品资金执行的限制见下文。

旧配置中的 `exchange.market_type` 仍可读取以兼容历史文件，但不再控制全局市场路由，保存设置时会移除该字段。

### 配置来源与凭据

配置按以下顺序读取：

1. `OKX_BOT_SETTINGS_PATH` 指定的本地文件；未设置时使用 `data/settings.local.yaml`。
2. 本地文件不存在时，使用仓库模板 [config/settings.yaml](config/settings.yaml)。

可在设置页填写凭据，也可在启动后端的终端中导出环境变量：

```bash
export OKX_API_KEY='your-api-key'
export OKX_SECRET='your-secret'
export OKX_PASSPHRASE='your-passphrase'
```

凭据须与所选模拟盘或真实盘环境匹配。可选通知变量为 `TG_BOT_TOKEN`、`TG_CHAT_ID`。应用不会自动加载 `.env` 文件。

保存设置时，来自环境变量的凭据保留为 `${VARIABLE}` 引用；手工输入的凭据会保存在本地 YAML 中，文件权限为 `0600`，并非加密存储。`data/` 和 `.env` 已被 Git 忽略。

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `OKX_BOT_SETTINGS_PATH` | 本地设置文件 | `data/settings.local.yaml` |
| `OKX_BOT_DB_PATH` | 主 SQLite 数据库 | `data/bot.db` |
| `OKX_BOT_API_TOKEN` | 工作台后端访问令牌，与 OKX API Key 分开 | 未启用 |
| `OKX_BOT_PRIVATE_SYNC_SECONDS` | 后台私有账户探测间隔，单位秒 | `30` |
| `OKX_BOT_SUPERVISOR_SECONDS` | 后台风控检查间隔，单位秒 | `30` |

设置 `OKX_BOT_API_TOKEN` 后，页面需要输入访问令牌；API 客户端使用 `Authorization: Bearer <token>` 或 `X-API-Token`，WebSocket 使用 `token` 查询参数。健康检查及登录、会话接口为公开例外。未配置令牌时，应保持服务仅监听本机；跨域和 WebSocket 来源校验只允许 `localhost` / `127.0.0.1`。

## 账户同步与策略资金

账户鉴权与数据同步是两个步骤。当前页面在 **sandbox 账户鉴权成功后**自动发起首次私有同步，失败时提供重试；普通缓存刷新不会再次触发完整私有同步。真实盘尚无相同的页面自动触发流程。

后台默认每 30 秒运行的私有任务只探测余额，并标记 `complete=false`，不能视为订单、成交、持仓均已完整同步。

策略页可保存预算草稿，并查看只读的 readiness 预览。预览按原生报价币种比较预算，展示账户证据、订单及保护状态；它不会分配资金，也不是可复用的下单授权。资金激活是单独操作：`paper` 使用本地资金账本，`demo` / `live` 提供交易所 funded 激活路径。

交易所执行已有策略资金预留和自有持币检查。账户总余额、策略账本余额和实际成交需分别核对；现有持仓不会因写入一个预算数字就自动变成策略自有库存。

## 历史回测

在回测页选择策略、现货标的、时间范围和初始资金即可运行。也可调用 `POST /api/backtest/run`，时间戳单位为毫秒：

```json
{
  "strategy": "ma_cross",
  "symbol": "BTC-USDT",
  "market_type": "spot",
  "timeframe": "1h",
  "start_time": 1700002800000,
  "end_time": 1700089200000,
  "initial_capital": 100000
}
```

`strategy` 可为内置类型或已保存的策略实例名称。支持周期为 `1m`、`5m`、`15m`、`1h`、`4h`、`1d`；单次请求最多 200,000 根 K 线。可选 `oos_ratio` 取值范围为 `0` 至 `0.5`，用于末段样本外回测。

缓存缺失时自动分页补取 OKX 现货 K 线。起点为不早于 `start_time` 的第一根周期对齐 K 线，终点包含 `end_time` 范围内的对齐 K 线。完整性校验通过后才写入本轮补取数据；覆盖不足会报错，不保存部分回测结果。

成功后保存收益率、年化收益率、夏普比率、最大回撤、胜率、成交数及结果明细：

- `GET /api/backtest/results`：结果列表。
- `GET /api/backtest/results/{result_id}`：结果详情。

常见错误：`422` 表示不支持的周期、非法范围或历史数据不足；`502` 表示获取历史行情失败。页面会展示后端返回的错误说明。

## 数据存储与备份

| 内容 | 默认位置 |
| --- | --- |
| 主库：策略配置、预算草稿、本地模拟与回测记录、运行审计等 | `data/bot.db` |
| 当前交易所账户事实 | `data/account-facts/bot.db/<account_scope_id>.db` |
| 本地设置 | `data/settings.local.yaml` |

账户事实库按 **API Key 指纹与 sandbox / live 环境**隔离；市场类型不参与账户身份。更换 API Key 后会使用新的历史作用域，即使两把 Key 属于同一个 OKX 账户。该绑定不是交易所已验证的账户身份，历史主库交易记录也不会自动导入当前账户视图。

若自定义主库路径，账户事实库位于主库同级的 `account-facts/<主库文件名>/` 目录。身份元数据缺失或不匹配的事实库会被拒绝使用。

备份应同时包含主库、整个匹配的账户事实目录以及本地设置。普通文件复制前先停后端，确保 SQLite WAL 数据一并完整保留。`src/ops/backup.py` 提供单个数据库的 SQLite 备份函数，不会自动遍历并备份全部账户文件。

自建 API 客户端应先读取 `GET /api/trading/context`，再按 API 文档将当前 `account_scope_id` 作为 `expected_account_scope_id` 传入要求该参数的账户快照、账户视图、私有刷新 / 同步及交易所 WebSocket 请求。上下文与缓存快照读取本身不会访问 OKX；手工交易接口目前未提供同样的参数约束。

## 已知限制

以下为 **2026-11-18 合并版本**仍存在的问题，同时影响模拟盘或真实盘的相关执行路径：

1. **成交终态与资金对账未闭合。** 最近记录的 20 策略模拟盘观察中，一张 LINK 市价单在交易所已终结，分两笔成交且总量小于原委托量；本地仍为 pending，策略资金账本未结算。差异检测触发 kill switch，20 个策略全部停止。恢复需按原订单核对终态、成交及手续费，不能把 fresh / ready 或未决计数为零当作恢复依据。
2. **手工改价仍绕过部分检查。** 当前改价未通过统一 manager 的风控和资金预留调整，且本地订单号到交易所订单号的转换不完整。
3. **衍生品资金执行不完整。** 激活入口接受永续、交割和期权标的，但 funded 执行账本仍采用现货现金与基础资产持币语义，尚未覆盖合约乘数、保证金和做空；不能据路由成功判断合约策略可完整运行。
4. **账户新鲜度默认值不协调。** 后台账户探测默认 30 秒，readiness 对账户与行情共用默认 15 秒阈值，可能误报 `stale_account` 并触发不会自动解除的开仓暂停。历史试跑使用过 90 秒阈值，该配置不能解决成交对账问题。

完整的历史观察见 [20 个 OKX 模拟盘策略观察记录](docs/observations/2026-11-17-20-strategy-sandbox.md)。它记录一次短时联调及提前中止结果，不能作为当前运行状态、完整交易验收或策略收益能力的证明；其中 `/tmp` 原始证据路径属于当时的本地环境。

## 开发与验证

后端：

```bash
uv run ruff check .
uv run pytest
```

前端：

```bash
npm --prefix frontend run test -- --run
npm --prefix frontend run type-check
npm --prefix frontend run build
```

`build` 已包含 TypeScript 检查，构建产物在 `frontend/dist/`。当前本地运行方式仍为前后端分别启动，FastAPI 不会自动托管该目录。GitHub Actions 在 push 和 pull request 时执行后端 lint / 测试以及前端类型检查 / 测试；自动化检查通过不代表交易所运行验收通过。

## 目录结构

```text
src/
  backtest/     历史数据、撮合、回测引擎与结果
  core/         配置、执行环境和账户作用域
  data/         SQLite 持久化、账户事实与资金账本
  exchange/     OKX / CCXT 适配与多市场路由
  market/       行情轮询、K 线与心跳
  strategy/     内置策略、YAML 与规则策略
  order/        订单路由、管理与手工交易
  risk/         风控规则与后台监督
  ops/          同步、对账、运行控制与备份
  web/          FastAPI、REST API 与 WebSocket
frontend/       Vue 3、TypeScript、Vite、Element Plus、ECharts
config/         默认配置与策略示例
tests/          后端单元及集成测试
docs/
  observations/ 实际运行观察
  superpowers/  历史设计与实施计划
```

设计与计划记录用于追溯背景，当前行为以代码和已保存的运行证据为准。
