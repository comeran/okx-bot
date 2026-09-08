# Comprehensive UI Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign and standardize every frontend page of the OKX Bot into a coherent, responsive, accessible trading-console UI without changing backend trading contracts.

**Architecture:** Establish a small CSS-token and shared-component foundation first, then migrate the application shell and each page onto those primitives. Keep domain state and API contracts in the existing Pinia stores/services, move only presentational concerns into focused components, and verify each page at desktop, tablet, and mobile widths before moving to the next page.

**Tech Stack:** Vue 3.5, TypeScript, Vite 6, Element Plus, Pinia, Vue I18n, ECharts, Monaco Editor, Vitest, Vue Test Utils, Playwright/browser verification, Figma prototype.

---

## Plan status and superseded plans

This plan supersedes the UI-related scope in these existing documents:

- `docs/superpowers/plans/2026-06-03-okx-bot-next-steps.md`
- `docs/superpowers/plans/2026-06-08-runtime-ui-polish.md`
- `docs/superpowers/plans/2026-08-25-strategy-performance.md` for its presentation-layer work only

Do not delete those files. Leave them as historical plans and track all future UI work here. The backend strategy-performance aggregation and API work already present in the working tree remains valid; this plan governs how that data is presented.

The implementation must not:

- change REST response shapes;
- add or rename WebSocket message types;
- change order placement, strategy lifecycle, backtest execution, market-data, or settings persistence behavior;
- fabricate missing financial values;
- remove the existing English and Simplified Chinese locales;
- commit or push changes unless the user explicitly requests it.

## Current frontend baseline

- Application shell: `frontend/src/App.vue`
- Entry point and Element Plus registration: `frontend/src/main.ts`
- Pages: `frontend/src/views/Dashboard.vue`, `Strategy.vue`, `Market.vue`, `Backtest.vue`, `Trades.vue`, `Settings.vue`
- Shared components: `frontend/src/components/StrategyForm.vue`, `components/charts/Candlestick.vue`, `components/editor/CodeEditor.vue`
- State: `frontend/src/stores/dashboard.ts`, `stores/strategies.ts`, `stores/settings.ts`
- Services: `frontend/src/services/`
- Formatting and domain helpers: `frontend/src/utils/dashboard.ts`, `backtest.ts`, `strategy.ts`, `strategyManagement.ts`, `strategyPerformance.ts`
- UI foundation today: global Element Plus CSS plus page-local `<style scoped>` blocks; no project-owned token file, no Storybook, and no shared page-header/card/state primitives
- Responsive behavior today: Element Plus grid plus page-specific `@media (max-width: 767px)` rules; Strategies has separate desktop table and mobile card DOM
- Existing tests are strongest for strategy management and shared utilities; Dashboard, Market, Backtest, Trades, and Settings need page-level regression coverage

## Figma reference and target visual direction

Prototype: [OKX Bot Dashboard Strategy Performance Prototype](https://www.figma.com/design/grNdEXy7HezGxAsm2t5Vfg)

The implementation should preserve the prototype's intent while adapting it to real data:

- dark navy application navigation with clear active-route state;
- light neutral workspace background;
- blue primary actions and green/red financial semantics;
- compact but readable information-dense cards and tables;
- strong page title and section hierarchy;
- expanded rows or side panels for secondary detail instead of rendering every field at once;
- explicit loading, error, empty, stale, and disconnected states;
- responsive collapse from sidebar + tables into drawer + stacked cards.

### Locked design decision from review

When the Figma composition conflicts with real trading data density, keyboard usability, or an existing Element Plus interaction, prioritize data readability and operational reliability. The implementation may deviate from the prototype in column density, expanded-detail placement, table scrolling, and mobile stacking, but it must preserve the prototype's hierarchy, navigation tone, semantic colors, spacing rhythm, and progressive disclosure. Do not hide a required financial field merely to achieve pixel-level visual similarity.

### Complete Figma prototype deliverable

The current Figma file is the visual source of truth for this UI overhaul:

`https://www.figma.com/design/grNdEXy7HezGxAsm2t5Vfg`

Expand it from the existing Dashboard-only frame into these named sections and frames:

- `00 Foundations` — color, typography, spacing, radius, status, loading, empty, error, stale, and focus examples;
- `01 App Shell / Desktop` — sidebar, active route, header, locale switcher, live connection state;
- `01 App Shell / Mobile` — compact header, menu trigger, drawer navigation, focus restoration state;
- `02 Dashboard / Desktop` — account metrics, account allocation, strategy performance, activity panels;
- `02 Dashboard / Mobile` — stacked metrics, readable strategy details, labeled table scrolling;
- `03 Strategies / Desktop` — strategy list beside editor panel, status actions, validation summary;
- `03 Strategies / Mobile` — strategy cards, editor panel, sticky editor actions;
- `04 Market / Desktop` — responsive query panel, query summary, chart and data states;
- `04 Market / Mobile` — one-column controls, full-width submit, stable chart viewport;
- `05 Backtest / Desktop` — run form, metric grid, result history, selected detail chart;
- `05 Backtest / Mobile` — stacked form, metrics, result selection, detail chart;
- `06 Trades / Desktop` — filters, summary, responsive data table;
- `06 Trades / Mobile` — filter stack and labeled scrollable trade history;
- `07 Settings / Desktop` — six settings sections with top actions and secret states;
- `07 Settings / Mobile` — stacked sections and safe-area-aware bottom actions.

Each route frame must show a populated state plus annotations or adjacent variants for loading, empty, error, stale-data, disconnected, and dirty-confirmation states where that route supports them. Use the actual product strings and representative values from the existing types; label representative values as examples and never imply that Figma numbers are live account data. Build the Figma frames in the same order as Tasks 1–10, validate each major section before starting the next, and keep the desktop/mobile pair for each route visually linked through shared token values.

If Figma MCP read/write quota is unavailable, use Playwright to capture the running implementation at the target viewports and keep those screenshots as temporary visual references. Do not mark the Figma deliverable complete until the named frames exist in the linked Figma file.

## Product-wide UI rules

1. **Data truthfulness:** missing API values use the existing `—` convention; nullable return, win-rate, timestamp, and price values never become `0`.
2. **Financial color semantics:** positive values use the success token, negative values use the danger token, neutral values use the regular text token, and color is never the only indicator.
3. **One page heading:** each route has one `h2` page title, a short description, and a consistent action area.
4. **Progressive disclosure:** primary metrics and actions stay visible; secondary details live in expandable rows, drawers, or detail panels.
5. **Stable layout:** loading uses skeletons or reserved-height placeholders; tables and charts must not cause large layout jumps when data arrives.
6. **Responsive first:** no page may require horizontal scrolling at 390px unless it is an explicitly scrollable data table with an accessible label.
7. **Keyboard access:** every interactive control has a visible focus state, a semantic name, and a predictable tab order.
8. **Localized UI:** all user-facing strings are keys in `frontend/src/locales/en.ts` and `frontend/src/locales/zh-CN.ts`; raw strategy names, symbols, order statuses, and API values remain machine-readable.
9. **No hidden destructive action:** delete, discard, reload-over-dirty, and strategy stop actions require the existing confirmation pattern.
10. **Backend boundary:** visual work consumes the existing stores and services; new client-side filtering is allowed only when it uses already-loaded records.

## Design tokens to introduce

Create project-owned tokens that mirror the existing Element Plus visual language and the Figma prototype. Use CSS custom properties so all SFC styles can migrate incrementally:

```css
:root {
  --ui-color-primary: #409eff;
  --ui-color-primary-soft: #eaf4ff;
  --ui-color-success: #16a34a;
  --ui-color-success-soft: #ecfdf3;
  --ui-color-danger: #dc2626;
  --ui-color-danger-soft: #fef2f2;
  --ui-color-warning: #d97706;
  --ui-color-warning-soft: #fff7ed;
  --ui-color-text: #303133;
  --ui-color-text-secondary: #606266;
  --ui-color-text-muted: #909399;
  --ui-color-border: #e4e7ed;
  --ui-color-border-subtle: #ebeef5;
  --ui-color-surface: #ffffff;
  --ui-color-canvas: #f5f7fa;
  --ui-color-sidebar: #172033;
  --ui-color-sidebar-active: #202b43;
  --ui-radius-sm: 4px;
  --ui-radius-md: 8px;
  --ui-radius-lg: 10px;
  --ui-space-1: 4px;
  --ui-space-2: 8px;
  --ui-space-3: 12px;
  --ui-space-4: 16px;
  --ui-space-5: 20px;
  --ui-space-6: 24px;
  --ui-space-8: 32px;
  --ui-content-max-width: 1600px;
}
```

Typography must use the existing system stack, with `ui-monospace` reserved for code and raw WebSocket payloads. Do not introduce a remote font dependency. CSS custom properties can document breakpoint values for JavaScript and component logic, but CSS `@media` conditions must use a literal breakpoint because native CSS does not resolve `var(--...)` inside media queries.

## File map

### Create

- `frontend/src/styles/tokens.css` — project-owned colors, typography, spacing, radii, shadows, breakpoints, and Element Plus variable overrides.
- `frontend/src/styles/global.css` — reset, body canvas, focus-visible rules, common table/form defaults, and utility classes.
- `frontend/src/components/layout/AppSidebar.vue` — desktop and mobile navigation content with route-aware active state.
- `frontend/src/components/layout/AppHeader.vue` — page shell header, mobile menu trigger, locale selector, connection/status slot.
- `frontend/src/components/ui/AppPageHeader.vue` — consistent page title, description, and action slot.
- `frontend/src/components/ui/SectionCard.vue` — section surface with title, description, actions, and body slots.
- `frontend/src/components/ui/MetricCard.vue` — label, value, delta, semantic tone, and loading state.
- `frontend/src/components/ui/DataState.vue` — loading, error with retry, empty, and stale-data presentation.
- `frontend/src/components/ui/StatusBadge.vue` — localized semantic status presentation with text and optional icon.
- `frontend/src/components/ui/ResponsiveTable.vue` — table wrapper with narrow-screen scroll labeling and stable empty/loading slots.
- `frontend/src/components/dashboard/AccountOverview.vue` — account allocation chart and asset table.
- `frontend/src/components/dashboard/StrategyPerformanceTable.vue` — strategy comparison table and expanded strategy details.
- `frontend/src/components/dashboard/DashboardActivity.vue` — recent orders, positions, runtime strategies, and WebSocket activity panels.
- `frontend/src/components/strategy/StrategyList.vue` — shared strategy list data and action rendering for desktop/mobile containers.
- `frontend/src/components/strategy/StrategyEditorPanel.vue` — structured/advanced editor surface and editor actions.
- `frontend/src/components/market/MarketQueryPanel.vue` — market type, symbol, timeframe, range, limit, and submit controls.
- `frontend/src/components/market/MarketChartPanel.vue` — chart header, query summary, loading, empty, error, and Candlestick content.
- `frontend/src/components/backtest/BacktestForm.vue` — backtest request form and validation presentation.
- `frontend/src/components/backtest/BacktestMetrics.vue` — latest metrics grid.
- `frontend/src/components/backtest/BacktestResultsTable.vue` — historical result selection and empty state.
- `frontend/src/components/backtest/BacktestResultDetail.vue` — selected result detail, chart, loading, and failure state.
- `frontend/src/components/settings/SettingsSection.vue` — consistent settings section surface and description.
- `frontend/src/components/settings/SecretField.vue` — masked secret input and configured-state display.
- `frontend/src/components/trades/TradeFilters.vue` — client-side strategy/symbol/side/time filters over loaded trades.
- `frontend/src/utils/market.ts` — pure market query construction and filter display helpers.
- `frontend/src/utils/trades.ts` — trade filtering, summaries, and formatting helpers.
- `frontend/src/composables/useDirtyGuard.ts` — shared dirty-state confirmation for reload, close, navigation, and reset actions.
- `frontend/src/components/ui/*.test.ts` — focused tests for shared UI contracts.
- `frontend/src/components/layout/*.test.ts` — shell navigation, mobile menu, and locale tests.
- `frontend/src/components/dashboard/*.test.ts` — Dashboard presentation and state tests.
- `frontend/src/components/market/*.test.ts` — query panel and chart state tests.
- `frontend/src/components/backtest/*.test.ts` — form, metrics, result table, and detail tests.
- `frontend/src/components/settings/*.test.ts` — settings section and secret field tests.
- `frontend/src/components/trades/*.test.ts` — filter and summary tests.
- `frontend/src/views/*.test.ts` where a route-level integration test is needed after extraction.

### Modify

- `frontend/src/main.ts` — import project global styles before mounting the app.
- `frontend/src/App.vue` — compose the new shell components and preserve WebSocket, router, locale, and focus behavior.
- `frontend/src/views/Dashboard.vue` — become a page composition layer using the new Dashboard components.
- `frontend/src/views/Strategy.vue` — use `StrategyList`, `StrategyEditorPanel`, shared page/state primitives, and the dirty guard.
- `frontend/src/views/Market.vue` — delegate query and chart presentation while retaining request orchestration.
- `frontend/src/views/Backtest.vue` — delegate form, metrics, results, and detail presentation while retaining request orchestration.
- `frontend/src/views/Trades.vue` — add loaded-data filters and responsive presentation without changing the API response.
- `frontend/src/views/Settings.vue` — split the long form into sections, add dirty guard, secret fields, and sticky actions.
- `frontend/src/components/StrategyForm.vue` — consume tokens and shared field/status styles without changing its payload contract.
- `frontend/src/components/charts/Candlestick.vue` — consume chart tokens, expose stable loading/empty sizing, and retain current data props.
- `frontend/src/components/editor/CodeEditor.vue` — consume tokens and expose accessible labels without changing Monaco behavior.
- `frontend/src/stores/dashboard.ts`, `stores/strategies.ts`, and `stores/settings.ts` only where presentation state needs explicit loading/error/stale/dirty metadata; do not change API contracts.
- `frontend/src/utils/dashboard.ts`, `utils/backtest.ts`, `utils/strategy.ts`, `utils/strategyManagement.ts`, and `utils/strategyPerformance.ts` to centralize display rules without duplicating domain logic.
- `frontend/src/locales/en.ts` and `frontend/src/locales/zh-CN.ts` — add all new shell, state, filter, accessibility, and page labels.
- Existing frontend tests and `frontend/src/test-utils/mount.ts` — extend shared mount helpers and regression coverage.
- `frontend/vite.config.ts` only if the global style import or browser test configuration requires it; do not alter API proxy targets.

## Implementation tasks

### Task 1: Establish the visual foundation and migration contract

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/global.css`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/src/App.vue`
- Test: `frontend/src/test-utils/mount.test.ts`

- [ ] **Step 1: Record the current UI baseline before changing styles.**

Run the existing frontend checks and capture the current route inventory:

```bash
npm --prefix frontend test -- --run
npm --prefix frontend run type-check
npm --prefix frontend run build
```

Expected: the current test suite, type check, and production build pass. If a pre-existing failure exists, record its exact command and message before continuing; do not hide it with test configuration changes.

- [ ] **Step 2: Add the concrete token file.**

Create `frontend/src/styles/tokens.css` with the token values from the Design tokens section, plus these Element Plus mappings:

```css
:root {
  --el-color-primary: var(--ui-color-primary);
  --el-color-success: var(--ui-color-success);
  --el-color-warning: var(--ui-color-warning);
  --el-color-danger: var(--ui-color-danger);
  --el-text-color-primary: var(--ui-color-text);
  --el-text-color-regular: var(--ui-color-text-secondary);
  --el-text-color-secondary: var(--ui-color-text-muted);
  --el-border-color: var(--ui-color-border);
  --el-border-color-light: var(--ui-color-border-subtle);
  --el-bg-color: var(--ui-color-surface);
  --el-fill-color-blank: var(--ui-color-surface);
}
```

- [ ] **Step 3: Add global layout and accessibility styles.**

Create `frontend/src/styles/global.css` with the following concrete baseline:

```css
* { box-sizing: border-box; }
html, body, #app { min-height: 100%; margin: 0; }
body {
  background: var(--ui-color-canvas);
  color: var(--ui-color-text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
}
button, input, textarea, select { font: inherit; }
:focus-visible {
  outline: 2px solid var(--ui-color-primary);
  outline-offset: 2px;
}
[data-monospace], code, pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
}
```

- [ ] **Step 4: Import the base library before project overrides.**

Update `frontend/src/main.ts` so Element Plus establishes its defaults first and the project tokens/global overrides load afterward:

```ts
import 'element-plus/dist/index.css';
import './styles/tokens.css';
import './styles/global.css';
```

This order is required because `tokens.css` maps the `--el-*` variables and must win over Element Plus defaults. Keep `ElementPlus`, Pinia, router, i18n, and Monaco initialization unchanged.

- [ ] **Step 5: Replace App.vue hard-coded canvas values with tokens.**

Change `.app-shell`, `.sidebar`, `.header`, `.content`, borders, text colors, and radii in `frontend/src/App.vue` to use `var(--ui-...)`. Preserve the current `220px` desktop sidebar, `min(82vw, 320px)` mobile drawer, route watcher, locale persistence, and focus restoration.

- [ ] **Step 6: Extend the mount helper for deterministic viewport and locale tests.**

Update `frontend/src/test-utils/mount.ts` so tests can pass a locale and attach the router without duplicating plugin setup. Add tests in `frontend/src/test-utils/mount.test.ts` that mount a minimal component with the helper and assert both `en` and `zh-CN` can be selected.

- [ ] **Step 7: Run the foundation checks.**

```bash
npm --prefix frontend exec -- vitest run src/test-utils/mount.test.ts
npm --prefix frontend run type-check
npm --prefix frontend run build
```

Expected: all commands pass and no page loses its Element Plus styles.

### Task 2: Build the shared UI primitives

**Files:**
- Create: `frontend/src/components/ui/AppPageHeader.vue`
- Create: `frontend/src/components/ui/SectionCard.vue`
- Create: `frontend/src/components/ui/MetricCard.vue`
- Create: `frontend/src/components/ui/DataState.vue`
- Create: `frontend/src/components/ui/StatusBadge.vue`
- Create: `frontend/src/components/ui/ResponsiveTable.vue`
- Test: `frontend/src/components/ui/*.test.ts`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`

- [ ] **Step 1: Define the shared component contracts.**

Use these interfaces and props as the stable public contracts:

```ts
// MetricCard.vue
interface Props {
  label: string;
  value: string;
  delta?: string;
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger';
  loading?: boolean;
}

// DataState.vue
interface Props {
  loading?: boolean;
  error?: string | null;
  empty?: boolean;
  emptyDescription?: string;
  stale?: boolean;
}
```

`DataState` must expose `#default`, `#loading`, `#error`, and `#empty` slots and an `@retry` event. When `error` and `stale` are both present, render the existing content slot and a warning banner rather than replacing the last successful data.

- [ ] **Step 2: Write failing component tests.**

Test these exact behaviors:

```ts
it('renders a metric value and semantic delta')
it('renders an accessible retry button for an error')
it('preserves slot content while showing stale data')
it('renders localized status text with a non-color indicator')
it('renders a page heading with an action slot')
it('marks a narrow table wrapper with an accessible scroll label')
```

- [ ] **Step 3: Implement the primitives with tokens only.**

Use `SectionCard` for surface, header, description, and action slots. Use `StatusBadge` with a text label and a small status dot/icon. Do not embed page-specific labels, API fields, or business logic in these components.

- [ ] **Step 4: Add shared state and status translations.**

Add keys under `common` for loading, retry, empty, stale, connected, disconnected, running, stopped, starting, error, unknown, and scrollable-table instructions in both locale files. Every new component string must call `t()` or receive a translated prop.

- [ ] **Step 5: Run focused primitive tests.**

```bash
npm --prefix frontend exec -- vitest run src/components/ui
```

Expected: all primitive tests pass before any page migration begins.

### Task 3: Split and restyle the application shell

**Files:**
- Create: `frontend/src/components/layout/AppSidebar.vue`
- Create: `frontend/src/components/layout/AppHeader.vue`
- Test: `frontend/src/components/layout/AppSidebar.test.ts`, `AppHeader.test.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`

- [ ] **Step 1: Extract navigation data into one shared structure.**

Use one typed list in `AppSidebar.vue` for the six existing routes:

```ts
const navigationItems = [
  { route: '/', labelKey: 'nav.dashboard' },
  { route: '/strategies', labelKey: 'nav.strategies' },
  { route: '/backtest', labelKey: 'nav.backtest' },
  { route: '/market', labelKey: 'nav.market' },
  { route: '/trades', labelKey: 'nav.trades' },
  { route: '/settings', labelKey: 'nav.settings' },
] as const;
```

Render the same component in the desktop sidebar and mobile drawer so active state and labels cannot diverge.

- [ ] **Step 2: Write shell tests before extraction.**

Assert that:

- each route is rendered exactly once in the sidebar;
- the current route receives the active class and `aria-current="page"`;
- the mobile menu button has the localized accessible name;
- closing the drawer restores focus to the menu button;
- changing locale updates the visible language label without changing route state.

- [ ] **Step 3: Implement the shell extraction.**

`AppHeader.vue` owns the menu trigger, page/app title, locale selector, and a status slot. `AppSidebar.vue` owns navigation only. `App.vue` retains WebSocket connection lifecycle, route watcher, locale persistence, and `<router-view />`.

- [ ] **Step 4: Apply the target shell layout.**

Use the Figma direction:

- desktop sidebar: `220px`, dark navy background, white brand, blue active item;
- header: `64px` minimum height, white surface, bottom border;
- content: `24px` desktop padding, `16px` tablet padding, `12px` mobile padding;
- main content max width: `var(--ui-content-max-width)` with centered wide layouts;
- mobile breakpoint: `767px`, with drawer navigation and compact header.

- [ ] **Step 5: Run shell tests and build.**

```bash
npm --prefix frontend exec -- vitest run src/components/layout
npm --prefix frontend run type-check
npm --prefix frontend run build
```

### Task 4: Rebuild Dashboard around hierarchy and progressive disclosure

**Files:**
- Create: `frontend/src/components/dashboard/AccountOverview.vue`
- Create: `frontend/src/components/dashboard/StrategyPerformanceTable.vue`
- Create: `frontend/src/components/dashboard/DashboardActivity.vue`
- Test: `frontend/src/components/dashboard/*.test.ts`
- Modify: `frontend/src/views/Dashboard.vue`
- Modify: `frontend/src/utils/dashboard.ts`
- Modify: `frontend/src/utils/strategyPerformance.ts`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`

- [ ] **Step 1: Define Dashboard component props from existing store types.**

Keep store ownership in `Dashboard.vue`; pass view models into components. The strategy table must receive the already-merged rows from `enrichStrategyPerformanceRows`, and `AccountOverview` must receive `account`, `assetRows`, and chart data without calling services itself.

- [ ] **Step 2: Write Dashboard component tests.**

Cover these exact cases:

```ts
it('renders six account metrics with — for missing values')
it('renders account allocation empty state without initializing an empty chart')
it('renders strategy rows in runtime-first order')
it('keeps a historical-only strategy visible with unknown status')
it('renders nullable return and win rate as —')
it('keeps the last successful performance table visible beside a refresh warning')
it('caps recent orders at twenty rows')
it('renders disconnected WebSocket status with text and icon')
```

- [ ] **Step 3: Implement AccountOverview.vue.**

Move the ECharts pie lifecycle and asset table into the component. Preserve `echarts.use`, `ResizeObserver`/window resize cleanup, positive-asset filtering, and the current `AssetBalance` field names. Render `DataState` for loading, error, and no-assets states.

- [ ] **Step 4: Implement StrategyPerformanceTable.vue.**

Use `ResponsiveTable` and `StatusBadge`. Keep first-level columns limited to strategy/status, equity, return, realized PnL, unrealized PnL, exposure, closed trades, and win rate. Put fees, order counts, last order, positions, and recent orders in the expanded row. Do not reintroduce full order history into the dashboard.

- [ ] **Step 5: Implement DashboardActivity.vue.**

Group recent orders, positions, runtime strategy status, and WebSocket messages into visually distinct sections. Long payload previews must use a two-line clamp plus an accessible full-value tooltip; retain the existing monospace treatment for raw payloads.

- [ ] **Step 6: Compose the new Dashboard layout.**

The page order must be:

1. `AppPageHeader` with last-updated text, WebSocket status, and refresh action;
2. error/stale banners;
3. six `MetricCard` instances;
4. `AccountOverview`;
5. `StrategyPerformanceTable`;
6. `DashboardActivity`.

Use `el-row` only for the metric grid and use stacked `SectionCard` blocks for the lower page to improve hierarchy.

- [ ] **Step 7: Run Dashboard tests and browser checks.**

```bash
npm --prefix frontend exec -- vitest run src/components/dashboard src/views/Dashboard.test.ts
npm --prefix frontend run type-check
```

At `1440x1120`, confirm all primary metrics and the strategy table fit without accidental horizontal overflow. At `390x844`, confirm cards stack and the table exposes an accessible horizontal-scroll region.

### Task 5: Rework Strategies into a clear list/editor workspace

**Files:**
- Create: `frontend/src/components/strategy/StrategyList.vue`
- Create: `frontend/src/components/strategy/StrategyEditorPanel.vue`
- Test: `frontend/src/components/strategy/*.test.ts`
- Modify: `frontend/src/views/Strategy.vue`
- Modify: `frontend/src/components/StrategyForm.vue`
- Modify: `frontend/src/components/editor/CodeEditor.vue`
- Modify: `frontend/src/composables/useDirtyGuard.ts`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`

- [ ] **Step 1: Preserve the existing strategy state machine.**

Keep `mode`, `selectedName`, `cloneSourceName`, draft baselines, validation sequence tokens, and active operation protection in `Strategy.vue` or a dedicated composable only after tests pass. The visual refactor must not change create/edit/clone/delete/start/stop semantics.

- [ ] **Step 2: Write list/editor regression tests.**

Extend `frontend/src/views/Strategy.test.ts` and add component tests for:

- identical action availability in desktop and mobile renderers;
- running strategies showing stop rather than start;
- editing a non-stopped strategy rendering the read-only state;
- discard confirmation before close, route leave, and switching selected strategy;
- stale YAML validation not overwriting a newer editor session;
- structured and advanced editors preserving the same payload.

- [ ] **Step 3: Implement StrategyList.vue with one row model.**

Create a view-model type containing name, runtime status, safety state, error text, and action callbacks. Render the same fields and callbacks in desktop table rows and mobile cards; only the layout wrapper differs. Use `StatusBadge` and explicit text labels for all status colors.

- [ ] **Step 4: Implement StrategyEditorPanel.vue.**

Move the editor heading, selected strategy metadata, structured/advanced toggle, validation summary, save/cancel controls, and dirty state into one panel. Keep `StrategyForm` and `CodeEditor` as the field/editor implementations.

- [ ] **Step 5: Implement the shared dirty guard.**

Create `useDirtyGuard.ts` with this contract:

```ts
export function useDirtyGuard(isDirty: () => boolean, confirmDiscard: () => Promise<boolean>): {
  confirmIfDirty: () => Promise<boolean>;
}
```

Use it for editor close, switching strategies, route leave, and settings reload. Do not add a browser `beforeunload` handler unless the existing app has a real unsaved persistence path.

- [ ] **Step 6: Improve editor responsive behavior and accessibility.**

At widths below `900px`, stack list and editor panels. At widths below `767px`, keep editor actions sticky at the bottom of the viewport, ensure the Monaco container has a minimum height of `360px`, and provide visible labels for the structured/advanced mode switch.

- [ ] **Step 7: Run strategy regression checks.**

```bash
npm --prefix frontend exec -- vitest run src/views/Strategy.test.ts src/components/strategy src/components/StrategyForm.test.ts src/components/editor/CodeEditor.test.ts
npm --prefix frontend run type-check
```

### Task 6: Make Market a responsive query-and-chart workspace

**Files:**
- Create: `frontend/src/components/market/MarketQueryPanel.vue`
- Create: `frontend/src/components/market/MarketChartPanel.vue`
- Create: `frontend/src/utils/market.ts`
- Test: `frontend/src/components/market/*.test.ts`
- Test: `frontend/src/utils/market.test.ts`
- Modify: `frontend/src/views/Market.vue`
- Modify: `frontend/src/components/charts/Candlestick.vue`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`

- [ ] **Step 1: Extract pure market query construction.**

Create this function and test it before moving template code:

```ts
export function buildMarketKlineQuery(input: {
  symbol: string;
  timeframe: string;
  limit: number;
  startTime: Date | null;
  endTime: Date | null;
  marketType: string;
}): { query: KlineQuery; rangeQuery: boolean } | { error: 'symbolRequired' | 'incompleteRange' | 'invalidRange' };
```

It must preserve current behavior: trim symbol, reject only one range endpoint, reject `endTime <= startTime`, include `market_type`, and omit range fields when both dates are absent.

- [ ] **Step 2: Add a regression test for custom symbols.**

When market type changes, reset to the first fallback symbol only if the current symbol is empty or belongs to the previous fallback list. Preserve a user-created symbol such as `DOGE-USDT` instead of overwriting it.

- [ ] **Step 3: Implement MarketQueryPanel.vue.**

Use a responsive grid rather than one `inline` form. Desktop fields use consistent minimum widths; tablet uses two columns; mobile uses one column with a full-width submit button. Display the active market type, symbol, timeframe, and range summary above the chart after submission.

- [ ] **Step 4: Implement MarketChartPanel.vue.**

Render `DataState` around `Candlestick`. Reserve chart height during loading, show a localized no-data description for cached-range and default queries, and expose retry through the existing load action. Keep request cancellation/stale-response protection in `Market.vue`.

- [ ] **Step 5: Stabilize Candlestick visual states.**

Keep the current `Kline[]` prop and chart query contract. Add a stable empty container height, token-based axis/grid colors, and cleanup for `ResizeObserver` and ECharts disposal. Do not change candle or volume calculations.

- [ ] **Step 6: Run Market tests.**

```bash
npm --prefix frontend exec -- vitest run src/utils/market.test.ts src/components/market
npm --prefix frontend run type-check
```

Verify at `390x844` that every field is reachable without clipping and at `1024x900` that the query controls do not overlap the chart.

### Task 7: Make Backtest readable as a run-to-result workflow

**Files:**
- Create: `frontend/src/components/backtest/BacktestForm.vue`
- Create: `frontend/src/components/backtest/BacktestMetrics.vue`
- Create: `frontend/src/components/backtest/BacktestResultsTable.vue`
- Create: `frontend/src/components/backtest/BacktestResultDetail.vue`
- Test: `frontend/src/components/backtest/*.test.ts`
- Modify: `frontend/src/views/Backtest.vue`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`

- [ ] **Step 1: Define the backtest display contracts.**

Pass existing `BacktestRequest`, `BacktestMetrics`, `BacktestResult`, and `BacktestResultDetail` types into components. Components must emit `run`, `refresh`, and `select-result` events rather than importing services.

- [ ] **Step 2: Write failing component tests.**

Cover:

```ts
it('disables run while a backtest is running')
it('renders validation feedback without submitting invalid dates')
it('renders metric cards with formatted percentages and numbers')
it('selects a historical result and emits its id')
it('keeps the selected-result panel in loading state until detail arrives')
it('shows a retryable detail error without clearing the result table')
it('clears the selected result when refresh removes it from the result list')
```

- [ ] **Step 3: Implement BacktestForm.vue.**

Use the shared page/form spacing and group inputs into strategy, instrument, period, and capital sections. Keep `getBacktestValidationError` as the source of truth and use localized inline errors where possible; retain `ElMessage` only for request-level success/failure.

- [ ] **Step 4: Implement BacktestMetrics.vue and ResultsTable.vue.**

Use `MetricCard` for total return, Sharpe ratio, max drawdown, win rate, and total trades. The results table must show selected state, formatted timestamps, and an explicit empty state.

- [ ] **Step 5: Implement BacktestResultDetail.vue.**

Use a two-column desktop layout with summary metadata and chart; stack on mobile. Preserve the existing request-token rule so an old detail response cannot overwrite a newer selection.

- [ ] **Step 6: Compose and verify Backtest.vue.**

Page order: page header, run form, latest metrics, result history, selected detail. Keep refresh independent from run state. Run:

```bash
npm --prefix frontend exec -- vitest run src/components/backtest src/utils/backtest.test.ts
npm --prefix frontend run type-check
```

### Task 8: Rework Trades into a usable history view without a backend change

**Files:**
- Create: `frontend/src/components/trades/TradeFilters.vue`
- Create: `frontend/src/components/trades/TradeSummary.vue`
- Create: `frontend/src/components/trades/TradesTable.vue`
- Create: `frontend/src/utils/trades.ts`
- Test: `frontend/src/components/trades/*.test.ts`
- Test: `frontend/src/utils/trades.test.ts`
- Modify: `frontend/src/views/Trades.vue`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`

- [ ] **Step 1: Define client-side filter state.**

Use only fields already present in the loaded trade records:

```ts
interface TradeFilters {
  strategy: string;
  symbol: string;
  side: string;
  search: string;
}
```

Do not add query parameters or change the trades API.

- [ ] **Step 2: Write pure filter and summary tests.**

Test exact behavior for all trades, strategy filter, symbol filter, side filter, case-insensitive search, no matching rows, total notional, and positive/negative PnL counts.

- [ ] **Step 3: Implement TradeFilters.vue and TradeSummary.vue.**

Render filters in a responsive grid, provide a clear-filters action, and show result count plus compact aggregate values above the table. All labels come from locales.

- [ ] **Step 4: Implement TradesTable.vue.**

Use `ResponsiveTable`, semantic side/status badges, right-aligned numeric columns, localized timestamps, and an accessible empty state. On mobile, expose the table as a labeled horizontal-scroll region rather than hiding important trade fields.

- [ ] **Step 5: Run Trades tests and build.**

```bash
npm --prefix frontend exec -- vitest run src/components/trades src/utils/trades.test.ts
npm --prefix frontend run type-check
```

### Task 9: Reorganize Settings into safe, scannable sections

**Files:**
- Create: `frontend/src/components/settings/SettingsSection.vue`
- Create: `frontend/src/components/settings/SecretField.vue`
- Test: `frontend/src/components/settings/*.test.ts`
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/composables/useDirtyGuard.ts`
- Modify: `frontend/src/stores/settings.ts` only for explicit dirty/loading/error state if required by existing behavior
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`

- [ ] **Step 1: Write settings safety tests.**

Cover:

```ts
it('does not overwrite dirty values when reload is canceled')
it('asks for confirmation before reload with dirty values')
it('shows configured state while secret input remains blank')
it('submits a newly entered secret but preserves an unchanged secret')
it('keeps save actions disabled while saving')
it('shows section-level validation without losing other sections')
```

- [ ] **Step 2: Implement SettingsSection.vue.**

Use a consistent section title, description, content slot, and optional status/action slot. Keep the six existing domains: runtime, exchange, backtest defaults, risk limits, notifications, and web server.

- [ ] **Step 3: Implement SecretField.vue.**

The component accepts `modelValue`, `configured`, `label`, and `hint`; it emits `update:modelValue`. Render an empty password input for configured secrets, a localized “configured” indicator, and no secret value in DOM text or error messages.

- [ ] **Step 4: Add dirty guard to reload and navigation.**

Use the shared `useDirtyGuard` for reload and route leave. Keep the existing save serialization and secret-clearing behavior; only improve the presentation of unchanged secrets and confirmation flow.

- [ ] **Step 5: Add sticky action behavior.**

Keep save/reload actions visible at the top of the form and add a mobile bottom action bar that respects safe-area padding. Ensure the action bar does not cover the last field or keyboard focus.

- [ ] **Step 6: Run Settings tests.**

```bash
npm --prefix frontend exec -- vitest run src/components/settings src/views/Settings.test.ts
npm --prefix frontend run type-check
```

### Task 10: Finish cross-page responsive, accessibility, and internationalization work

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: all files under `frontend/src/views/`, `frontend/src/components/`, and `frontend/src/styles/` that still contain hard-coded layout tokens
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`
- Test: `frontend/src/i18n.test.ts`
- Test: page/component accessibility tests

- [ ] **Step 1: Replace remaining hard-coded visual values with tokens.**

Search and migrate repeated colors, borders, radii, and spacing in SFC styles:

```bash
rg -n "#[0-9a-fA-F]{6}|border-radius:|padding:|margin:|gap:" frontend/src --glob '*.vue' --glob '*.css'
```

Keep one-off chart configuration values local when they represent data visualization rather than UI chrome.

- [ ] **Step 2: Define and test responsive targets.**

Use these viewport targets in browser verification:

- `1440x1120`: full sidebar, multi-column metric grids, full tables;
- `1024x900`: compact sidebar/header, two-column forms, no overlapping cards;
- `767x900`: mobile navigation drawer threshold;
- `390x844`: stacked cards/forms, readable actions, labeled scrollable tables.

Add tests for the mobile strategy card/list behavior and ensure no page uses two competing sources of truth for labels or actions.

- [ ] **Step 3: Add keyboard and semantic checks.**

Verify every page has one `h2`, all buttons have accessible names, tables have captions or adjacent headings, status colors have text labels, modal/drawer close returns focus, and expand/collapse controls expose `aria-expanded`.

- [ ] **Step 4: Complete locale parity.**

Run a test that recursively compares the key structure of `en` and `zh-CN` for every newly added UI namespace. Add translations for page descriptions, state messages, filters, actions, tooltips, chart labels, and accessibility instructions.

- [ ] **Step 5: Run the full frontend test suite.**

```bash
npm --prefix frontend test -- --run
npm --prefix frontend run type-check
npm --prefix frontend run build
```

Expected: all tests, type checking, and production build pass with both locales.

### Task 11: Integrate the Figma prototype and perform browser verification

**Files:**
- No additional production files beyond Tasks 1–10
- Review/update: `https://www.figma.com/design/grNdEXy7HezGxAsm2t5Vfg`
- Temporary visual evidence: Playwright screenshots outside the repository, grouped by route and viewport

- [ ] **Step 0: Prepare the Figma and browser verification surfaces.**

Use the existing Figma file and create the named Foundations, App Shell, Dashboard, Strategies, Market, Backtest, Trades, and Settings desktop/mobile frames listed above. Do not create a second Figma file. Start the frontend with:

```bash
npm --prefix frontend run dev -- --host 127.0.0.1
```

Use Playwright to capture these exact viewport pairs for each route:

```text
Desktop: 1440x1120
Mobile: 390x844
Routes: /, /strategies, /market, /backtest, /trades, /settings
```

Use the screenshots to compare implementation and Figma section-by-section. The browser capture is a validation aid; it does not replace the named Figma frames.

- [ ] **Step 1: Start the backend and frontend with the repository commands.**

Run the backend on `127.0.0.1:8080` and the frontend on `127.0.0.1:3000` using the repository's existing development commands. Confirm:

```bash
curl -sS http://127.0.0.1:8080/api/health
curl -sS http://127.0.0.1:3000/
```

Expected: backend health is successful and Vite serves the application HTML.

- [ ] **Step 2: Verify every route at desktop width.**

Open `/`, `/strategies`, `/market`, `/backtest`, `/trades`, and `/settings` at `1440x1120`. Check page heading hierarchy, visual token consistency, loading/empty/error states, table alignment, chart sizing, and action placement.

- [ ] **Step 3: Verify every route at mobile width.**

Repeat at `390x844`. Check drawer navigation, page header truncation, form stacking, strategy cards, editor action visibility, table scroll labeling, chart minimum height, settings action bar, and no clipped text.

- [ ] **Step 4: Verify golden-path interactions.**

Exercise:

- Dashboard refresh and WebSocket connected/disconnected display;
- Strategy create/edit/clone/start/stop/delete and dirty discard;
- Market type switch, custom symbol, date-range validation, and chart retry;
- Backtest invalid form, successful run, history selection, stale detail response, and detail retry;
- Trades filtering and clear filters;
- Settings reload cancellation, secret preservation, save, and error display;
- English/Chinese locale switching on every route.

- [ ] **Step 5: Compare implementation against Figma and record deviations.**

Match the prototype's hierarchy, navigation tone, metric-card treatment, strategy table emphasis, semantic colors, and spacing. Keep deviations that are required by real data density or existing Element Plus behavior; do not introduce unrelated backend or trading changes.

- [ ] **Step 6: Run final regression commands.**

```bash
npm --prefix frontend test -- --run
npm --prefix frontend run type-check
npm --prefix frontend run build
uv run pytest tests/unit tests/integration -q
```

Expected: frontend checks pass, backend regression tests remain green, and no REST/WebSocket contract changes are required.

## Self-review checklist

- Every route in `frontend/src/views/` has a dedicated migration task.
- Shared tokens and primitives are created before page migrations.
- Dashboard, Strategy, Market, Backtest, Trades, and Settings all have explicit tests and browser verification.
- Loading, error, empty, stale, disconnected, and dirty states are defined rather than implied.
- Responsive behavior is specified at four concrete viewport sizes.
- Accessibility requirements include focus, labels, status text, tables, drawer focus restoration, and expand/collapse semantics.
- Locale parity is tested for English and Simplified Chinese.
- Existing API and WebSocket contracts remain unchanged.
- The Figma prototype is treated as visual direction, not as permission to invent unsupported financial data.
- The Figma file contains the complete named frame set for Foundations, App Shell, Dashboard, Strategies, Market, Backtest, Trades, and Settings in desktop/mobile pairs before the visual deliverable is called complete.
- Playwright screenshots cover every route at `1440x1120` and `390x844`; they are used to verify real rendered behavior when Figma MCP is unavailable.
- When Figma and live data presentation conflict, data readability and operational reliability win while hierarchy and semantic visual language remain consistent.
- No commits or pushes are part of this plan unless separately requested by the user.
