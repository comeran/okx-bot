# Runtime UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish Dashboard and Strategies as truthful runtime-control pages using existing backend REST/WebSocket contracts only.

**Architecture:** Keep backend contracts unchanged. Extend the Dashboard Pinia store to consume existing `strategy_status` and `strategy_error` WebSocket events, make runtime formatting testable through small utilities, then update Dashboard and Strategies views to present status, errors, empty values, and runtime-only scope clearly.

**Tech Stack:** Vue 3, Pinia, Vue I18n, Element Plus, TypeScript, Vitest, Vite, FastAPI backend already committed in `d1d9c45`.

---

## Locked Scope

- Do not add backend APIs, backend fields, or new WebSocket event shapes.
- Keep machine-readable values raw: strategy names, symbols, side values, order status values, API field names.
- Do not create persisted strategy config save/edit/delete UI in this slice.
- Do not fabricate account/position/order values. Missing values render as `—`.
- Browser verification must cover `/` and `/strategies`.

## File Structure

- Modify `frontend/src/types/dashboard.ts` to type existing backend `strategy_status` and `strategy_error` WebSocket messages.
- Modify `frontend/src/stores/dashboard.ts` to maintain `strategyErrors` and apply strategy status/error events.
- Modify `frontend/src/stores/dashboard.test.ts` to TDD the store reducer behavior.
- Create `frontend/src/utils/dashboard.ts` for Dashboard formatting/status helpers that are currently inline and hard to test.
- Create `frontend/src/utils/dashboard.test.ts` to verify empty rendering, payload preview, and status tag mapping.
- Modify `frontend/src/views/Dashboard.vue` to use helpers, render strategy status tags, render last strategy error, and make message payload preview easier to scan.
- Modify `frontend/src/views/Strategy.vue` to show explicit runtime-control-only copy near the strategy list.
- Modify `frontend/src/locales/en.ts` and `frontend/src/locales/zh-CN.ts` for new labels and hints.
- Modify `frontend/src/i18n.test.ts` to verify the new labels exist in both locales.

---

### Task 1: Dashboard store consumes strategy status/error runtime events

**Files:**
- Modify: `frontend/src/types/dashboard.ts`
- Modify: `frontend/src/stores/dashboard.ts`
- Test: `frontend/src/stores/dashboard.test.ts`

- [ ] **Step 1: Write failing store tests**

Add these tests after the existing `adds received timestamps to websocket messages` test in `frontend/src/stores/dashboard.test.ts`:

```ts
  it('applies strategy_status websocket messages to known strategies', () => {
    const dashboard = useDashboardStore();
    dashboard.strategies = [{ name: 'ma_cross', status: 'stopped' }];
    dashboard.strategyErrors = { ma_cross: 'previous error' };

    dashboard.addWebSocketMessage({
      type: 'strategy_status',
      strategy: 'ma_cross',
      status: 'running',
      timestamp: 1700000000000,
    });

    expect(dashboard.strategies).toEqual([{ name: 'ma_cross', status: 'running' }]);
    expect(dashboard.strategyErrors).toEqual({});
  });

  it('adds unknown strategies from strategy_status websocket messages', () => {
    const dashboard = useDashboardStore();

    dashboard.addWebSocketMessage({
      type: 'strategy_status',
      strategy: 'ma_cross_btc',
      status: 'stopped',
      timestamp: 1700000000000,
    });

    expect(dashboard.strategies).toEqual([{ name: 'ma_cross_btc', status: 'stopped' }]);
  });

  it('records strategy_error websocket messages without fabricating status', () => {
    const dashboard = useDashboardStore();
    dashboard.strategies = [{ name: 'ma_cross_btc', status: 'stopped' }];

    dashboard.addWebSocketMessage({
      type: 'strategy_error',
      strategy: 'ma_cross_btc',
      error: 'boom',
      timestamp: 1700000000000,
    });

    expect(dashboard.strategyErrors).toEqual({ ma_cross_btc: 'boom' });
    expect(dashboard.strategies).toEqual([{ name: 'ma_cross_btc', status: 'stopped' }]);
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
npm --prefix frontend exec vitest run src/stores/dashboard.test.ts
```

Expected: FAIL because `strategyErrors` does not exist and `strategy_status` / `strategy_error` messages are ignored.

- [ ] **Step 3: Add message types**

In `frontend/src/types/dashboard.ts`, add these interfaces after `StrategiesDashboardWebSocketMessage`:

```ts
export interface StrategyStatusDashboardWebSocketMessage extends DashboardMessageBase {
  type: 'strategy_status';
  strategy: string;
  status: string;
  timestamp?: number;
}

export interface StrategyErrorDashboardWebSocketMessage extends DashboardMessageBase {
  type: 'strategy_error';
  strategy: string;
  error: string;
  timestamp?: number;
}
```

Then include them in `DashboardWebSocketMessage`:

```ts
export type DashboardWebSocketMessage =
  | ConnectedDashboardWebSocketMessage
  | RawDashboardWebSocketMessage
  | AccountDashboardWebSocketMessage
  | PositionsDashboardWebSocketMessage
  | OrdersDashboardWebSocketMessage
  | StrategiesDashboardWebSocketMessage
  | StrategyStatusDashboardWebSocketMessage
  | StrategyErrorDashboardWebSocketMessage
  | SnapshotDashboardWebSocketMessage
  | UnknownDashboardWebSocketMessage;
```

- [ ] **Step 4: Add store state and reducer helpers**

In `frontend/src/stores/dashboard.ts`, add `strategyErrors` to `DashboardState`:

```ts
  strategyErrors: Record<string, string>;
```

Add it to initial state:

```ts
    strategyErrors: {},
```

Add this helper above `export const useDashboardStore`:

```ts
function upsertStrategyStatus(
  strategies: StrategySummary[],
  name: string,
  status: string,
): StrategySummary[] {
  const existingIndex = strategies.findIndex((strategy) => strategy.name === name);
  if (existingIndex === -1) {
    return [...strategies, { name, status }];
  }

  return strategies.map((strategy, index) => (
    index === existingIndex ? { ...strategy, status } : strategy
  ));
}
```

In `applyWebSocketMessage`, add cases before `snapshot`:

```ts
        case 'strategy_status': {
          if (typeof message.strategy === 'string' && typeof message.status === 'string') {
            this.strategies = upsertStrategyStatus(this.strategies, message.strategy, message.status);
            if (message.status === 'running') {
              const { [message.strategy]: _cleared, ...remainingErrors } = this.strategyErrors;
              this.strategyErrors = remainingErrors;
            }
          }
          break;
        }
        case 'strategy_error': {
          if (typeof message.strategy === 'string' && typeof message.error === 'string') {
            this.strategyErrors = {
              ...this.strategyErrors,
              [message.strategy]: message.error,
            };
          }
          break;
        }
```

- [ ] **Step 5: Run store tests to verify green**

Run:

```bash
npm --prefix frontend exec vitest run src/stores/dashboard.test.ts
```

Expected: PASS.

---

### Task 2: Extract testable Dashboard formatting helpers

**Files:**
- Create: `frontend/src/utils/dashboard.ts`
- Create: `frontend/src/utils/dashboard.test.ts`
- Modify: `frontend/src/views/Dashboard.vue`

- [ ] **Step 1: Write failing utility tests**

Create `frontend/src/utils/dashboard.test.ts`:

```ts
import { describe, expect, it } from 'vitest';

import {
  EMPTY_RUNTIME_VALUE,
  formatRuntimeCurrency,
  formatRuntimeNumber,
  formatRuntimePayloadPreview,
  formatRuntimeText,
  formatRuntimeTime,
  formatTickerPrice,
  getDashboardStrategyStatusTagType,
} from './dashboard';


describe('dashboard runtime UI helpers', () => {
  it('renders missing runtime values as an em dash', () => {
    expect(formatRuntimeCurrency(undefined)).toBe(EMPTY_RUNTIME_VALUE);
    expect(formatRuntimeNumber(undefined)).toBe(EMPTY_RUNTIME_VALUE);
    expect(formatRuntimeText('')).toBe(EMPTY_RUNTIME_VALUE);
    expect(formatRuntimeTime(undefined)).toBe(EMPTY_RUNTIME_VALUE);
    expect(formatTickerPrice('')).toBe(EMPTY_RUNTIME_VALUE);
  });

  it('formats finite numeric runtime values', () => {
    expect(formatRuntimeCurrency(1234.5)).toBe('$1,234.50');
    expect(formatRuntimeNumber(0.123456789)).toBe('0.12345679');
    expect(formatTickerPrice('68000.12345')).toBe('68,000.1235');
  });

  it('builds short payload previews without received metadata', () => {
    expect(formatRuntimePayloadPreview({
      type: 'strategy_error',
      strategy: 'ma_cross_btc',
      error: 'boom',
      received_at: 1700000000000,
    })).toBe('{"strategy":"ma_cross_btc","error":"boom"}');
  });

  it('truncates long payload previews', () => {
    const preview = formatRuntimePayloadPreview({ type: 'raw', data: 'x'.repeat(150) });

    expect(preview.endsWith('…')).toBe(true);
    expect(preview.length).toBe(121);
  });

  it('maps strategy statuses to Element Plus tag types', () => {
    expect(getDashboardStrategyStatusTagType('running')).toBe('success');
    expect(getDashboardStrategyStatusTagType('stopped')).toBe('info');
    expect(getDashboardStrategyStatusTagType('error')).toBe('danger');
    expect(getDashboardStrategyStatusTagType('starting')).toBe('warning');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
npm --prefix frontend exec vitest run src/utils/dashboard.test.ts
```

Expected: FAIL because `frontend/src/utils/dashboard.ts` does not exist.

- [ ] **Step 3: Create helper module**

Create `frontend/src/utils/dashboard.ts`:

```ts
import type { DashboardWebSocketMessage } from '@/types/dashboard';

export const EMPTY_RUNTIME_VALUE = '—';

export function formatRuntimeCurrency(value?: number): string {
  if (value === undefined || !Number.isFinite(value)) {
    return EMPTY_RUNTIME_VALUE;
  }

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatTickerPrice(value?: number | string): string {
  if (value === undefined || value === '') {
    return EMPTY_RUNTIME_VALUE;
  }

  const numberValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numberValue)) {
    return EMPTY_RUNTIME_VALUE;
  }

  return numberValue.toLocaleString('en-US', { maximumFractionDigits: 4 });
}

export function formatRuntimeNumber(value?: number): string {
  if (value === undefined || !Number.isFinite(value)) {
    return EMPTY_RUNTIME_VALUE;
  }

  return value.toLocaleString('en-US', { maximumFractionDigits: 8 });
}

export function formatRuntimeText(value?: string): string {
  return value || EMPTY_RUNTIME_VALUE;
}

export function formatRuntimeTime(timestamp?: number): string {
  if (timestamp === undefined || !Number.isFinite(timestamp)) {
    return EMPTY_RUNTIME_VALUE;
  }

  return new Date(timestamp).toLocaleString();
}

export function formatRuntimePayloadPreview(message: DashboardWebSocketMessage): string {
  const { type, received_at, ...payload } = message;

  if (Object.keys(payload).length === 0) {
    return EMPTY_RUNTIME_VALUE;
  }

  const preview = JSON.stringify(payload);
  return preview.length > 120 ? `${preview.slice(0, 120)}…` : preview;
}

export function getDashboardStrategyStatusTagType(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'running') return 'success';
  if (status === 'stopped') return 'info';
  if (status === 'error') return 'danger';
  return 'warning';
}
```

- [ ] **Step 4: Run utility tests to verify green**

Run:

```bash
npm --prefix frontend exec vitest run src/utils/dashboard.test.ts
```

Expected: PASS.

---

### Task 3: Render Dashboard runtime status, errors, and readable message previews

**Files:**
- Modify: `frontend/src/views/Dashboard.vue`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`
- Test: `frontend/src/utils/dashboard.test.ts`
- Test: `frontend/src/stores/dashboard.test.ts`

- [ ] **Step 1: Replace inline Dashboard helpers with imports**

In `frontend/src/views/Dashboard.vue`, replace the inline helper constants/functions from `const emptyValue = '—';` through `formatPayloadPreview` with this import block after the existing type import:

```ts
import {
  formatRuntimeCurrency,
  formatRuntimeNumber,
  formatRuntimePayloadPreview,
  formatRuntimeText,
  formatRuntimeTime,
  formatTickerPrice,
  getDashboardStrategyStatusTagType,
} from '@/utils/dashboard';
```

Then update uses:

```vue
{{ formatRuntimeTime(dashboard.lastUpdatedAt ?? undefined) }}
{{ formatRuntimeCurrency(dashboard.account?.equity) }}
{{ formatRuntimeCurrency(dashboard.account?.cash_balance) }}
{{ formatRuntimeCurrency(dashboard.account?.realized_pnl) }}
{{ formatRuntimeCurrency(dashboard.account?.daily_pnl) }}
{{ formatRuntimeCurrency(dashboard.account?.fees_paid) }}
{{ formatRuntimeText(row.symbol) }}
{{ formatRuntimeText(row.side) }}
{{ formatRuntimeText(row.type) }}
{{ formatRuntimeText(row.status) }}
{{ formatRuntimeNumber(row.amount) }}
{{ formatRuntimeNumber(row.price) }}
{{ formatRuntimeNumber(row.entry_price) }}
{{ formatRuntimeNumber(row.mark_price) }}
{{ formatRuntimeCurrency(row.unrealized_pnl) }}
{{ formatRuntimeTime(row.timestamp) }}
{{ formatRuntimePayloadPreview(row) }}
```

Update `lastUpdatedText` to:

```ts
const lastUpdatedText = computed(() => formatRuntimeTime(dashboard.lastUpdatedAt ?? undefined));
```

- [ ] **Step 2: Add localized label for last strategy error**

In `frontend/src/locales/en.ts`, add under `dashboard`:

```ts
    lastError: 'Last Error',
```

In `frontend/src/locales/zh-CN.ts`, add under `dashboard`:

```ts
    lastError: '最近错误',
```

- [ ] **Step 3: Render strategy status tags and last error**

Replace the Dashboard strategies table columns in `frontend/src/views/Dashboard.vue` with:

```vue
<el-table-column prop="name" :label="t('common.name')" min-width="140" />
<el-table-column :label="t('common.status')" min-width="110">
  <template #default="{ row }">
    <el-tag :type="getDashboardStrategyStatusTagType(row.status)" effect="plain">
      {{ row.status }}
    </el-tag>
  </template>
</el-table-column>
<el-table-column :label="t('dashboard.lastError')" min-width="180">
  <template #default="{ row }">
    {{ formatRuntimeText(dashboard.strategyErrors[row.name]) }}
  </template>
</el-table-column>
```

- [ ] **Step 4: Render message type as a tag and payload as monospace preview**

Replace the message table type/payload templates in `frontend/src/views/Dashboard.vue` with:

```vue
<el-table-column :label="t('dashboard.messageType')" min-width="120">
  <template #default="{ row }">
    <el-tag size="small" effect="plain">{{ row.type }}</el-tag>
  </template>
</el-table-column>
<el-table-column :label="t('dashboard.messagePayload')" min-width="260">
  <template #default="{ row }">
    <code class="dashboard-message-payload">{{ formatRuntimePayloadPreview(row) }}</code>
  </template>
</el-table-column>
```

Add scoped CSS:

```css
.dashboard-message-payload {
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  color: #606266;
}
```

- [ ] **Step 5: Run targeted tests and build check**

Run:

```bash
npm --prefix frontend exec vitest run src/stores/dashboard.test.ts src/utils/dashboard.test.ts
npm --prefix frontend run build
```

Expected: both commands PASS.

---

### Task 4: Clarify Strategies as runtime-control-only UI

**Files:**
- Modify: `frontend/src/views/Strategy.vue`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`
- Modify: `frontend/src/i18n.test.ts`

- [ ] **Step 1: Write failing i18n assertions**

In `frontend/src/i18n.test.ts`, extend the first test with:

```ts
    expect(i18n.global.t('strategies.runtimeControlHint')).toBe(
      'Start and stop existing runtime strategies here. Saving or editing persisted strategy configs is not part of this page yet.',
    );

    setLocale(i18n, 'zh-CN');

    expect(i18n.global.t('strategies.runtimeControlHint')).toBe(
      '此页面只用于启动和停止现有运行态策略，暂不保存或编辑持久化策略配置。',
    );
```

Keep the existing `nav.dashboard` assertions intact; if needed, move the existing `setLocale(i18n, 'zh-CN')` call earlier only once.

- [ ] **Step 2: Run i18n test to verify it fails**

Run:

```bash
npm --prefix frontend exec vitest run src/i18n.test.ts
```

Expected: FAIL because `strategies.runtimeControlHint` is missing.

- [ ] **Step 3: Add localized runtime-control hint**

In `frontend/src/locales/en.ts`, add under `strategies`:

```ts
    runtimeControlHint: 'Start and stop existing runtime strategies here. Saving or editing persisted strategy configs is not part of this page yet.',
```

In `frontend/src/locales/zh-CN.ts`, add under `strategies`:

```ts
    runtimeControlHint: '此页面只用于启动和停止现有运行态策略，暂不保存或编辑持久化策略配置。',
```

- [ ] **Step 4: Render hint below strategy list header**

In `frontend/src/views/Strategy.vue`, add this paragraph immediately after the `strategyList` card header:

```vue
<p class="strategy-page__hint strategy-page__hint--list">
  {{ t('strategies.runtimeControlHint') }}
</p>
```

Update scoped CSS:

```css
.strategy-page__hint--list {
  margin-bottom: 12px;
}
```

- [ ] **Step 5: Run i18n and helper tests**

Run:

```bash
npm --prefix frontend exec vitest run src/i18n.test.ts src/utils/strategy.test.ts
```

Expected: PASS.

---

### Task 5: Full frontend verification and browser smoke

**Files:**
- No production file changes in this task.

- [ ] **Step 1: Run all frontend unit tests**

Run:

```bash
npm --prefix frontend exec vitest run
```

Expected: PASS with all frontend tests passing.

- [ ] **Step 2: Run production build**

Run:

```bash
npm --prefix frontend run build
```

Expected: PASS with `vue-tsc --noEmit` and Vite build success.

- [ ] **Step 3: Start backend and frontend dev servers**

Run backend in one background process:

```bash
uv run uvicorn src.web.app:app --host 127.0.0.1 --port 8000
```

Run frontend in another background process:

```bash
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Expected: backend serves FastAPI, frontend serves Vite at `http://127.0.0.1:5173`.

- [ ] **Step 4: Browser verify Dashboard `/`**

Open `http://127.0.0.1:5173/` and verify:

- Page loads without blank screen.
- Refresh button calls the existing REST endpoints and leaves main error empty when successful.
- Account cards render real values or `—`, not fabricated values.
- Positions and Orders render tables or empty states.
- Strategy table renders status as tags and last error as `—` when none exists.
- WebSocket status is visible.
- WebSocket message log shows message type, received time, and short payload preview.
- Browser console has no error/warn messages from this page.

- [ ] **Step 5: Browser verify Strategies `/strategies`**

Open `http://127.0.0.1:5173/strategies` and verify:

- Page loads without blank screen.
- Strategy list renders existing strategies from `/api/strategies`.
- Running strategies have Start disabled and Stop enabled.
- Stopped strategies have Stop disabled and Start enabled.
- Clicking Start/Stop shows row-level loading only for that strategy.
- YAML area is clearly labeled as generated draft, not saved backend config.
- Runtime-control hint is visible.
- Browser console has no error/warn messages from this page.

- [ ] **Step 6: Review git diff before reporting completion**

Run:

```bash
git diff --stat
git diff
```

Expected: only frontend UI/test/locale files and the new plan file changed.

---

### Task 6: Remove Monaco worker warnings from Strategies browser smoke

**Files:**
- Create: `frontend/src/monaco.ts`
- Create: `frontend/src/monaco.test.ts`
- Create: `frontend/src/vite-env.d.ts`
- Modify: `frontend/src/main.ts`

- [ ] **Step 1: Write failing Monaco environment tests**

Create `frontend/src/monaco.test.ts` to verify the app can assign `MonacoEnvironment.getWorker` through a testable initializer.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
npm --prefix frontend exec vitest run src/monaco.test.ts
```

Expected: FAIL because `frontend/src/monaco.ts` does not exist.

- [ ] **Step 3: Add Monaco worker environment setup**

Create `frontend/src/monaco.ts` with `createMonacoEnvironment()` and `configureMonacoEnvironment()`, using Vite's `?worker` import for `monaco-editor/esm/vs/editor/editor.worker`.

- [ ] **Step 4: Add Vite client type reference**

Create `frontend/src/vite-env.d.ts` with:

```ts
/// <reference types="vite/client" />
```

- [ ] **Step 5: Configure Monaco before mounting Vue**

Import and call `configureMonacoEnvironment()` in `frontend/src/main.ts` before creating the app.

- [ ] **Step 6: Verify tests, build, and browser console**

Run:

```bash
npm --prefix frontend exec vitest run src/monaco.test.ts
npm --prefix frontend exec vitest run
npm --prefix frontend run build
```

Expected: tests and build pass; `/strategies` no longer logs Monaco worker warnings.

---

## Plan Self-Review

- Spec coverage: covers all locked grill-me decisions for this UI slice: existing backend data only, Dashboard + Strategies, no fake values, readable WebSocket log, no strategy config save/edit UI, browser verification.
- Placeholder scan: no unresolved placeholder steps are present.
- Type consistency: `strategy_status`, `strategy_error`, `strategyErrors`, and helper names are defined before later tasks use them.
