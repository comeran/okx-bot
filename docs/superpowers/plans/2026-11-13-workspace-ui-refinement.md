# Quant Workspace UI Refinement Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent presentation scopes, then review the integrated result. The user selected direct execution of the recommended design.

**Goal:** Make all six pages coherent, readable, compact, and responsive without changing financial or authentication behavior.

**Architecture:** Refine the existing CSS tokens and shared Vue primitives, then adapt shell and route-specific layouts. Keep stores, services, and backend contracts intact. The only new route state is a persistent backtest history loading error.

**Tech Stack:** Vue, Element Plus, CSS variables, Vue I18n, Vitest, TypeScript, Vite.

**Spec:** `docs/superpowers/specs/2026-11-13-workspace-ui-refinement-design.md`

## Global constraints

- Preserve existing uncommitted authentication changes and account/credential protections.
- No backend edits, new dependencies, remote fonts, real trades, credential changes, commits, or pushes.
- Use existing token names; new visual tokens are optional, not required dependencies between parallel tasks.
- UI text must exist in both locales. Missing financial data stays `—`.

## Review focus

- Long English status labels and Chinese text must fit 320/390px screens without hiding an authentication failure.
- Keyboard navigation must reach main content and mobile navigation with a visible focus state.
- Dense table/form layouts must preserve labels, all fields, numeric semantics, and dirty-form guards.
- Failed history retrieval must be distinguishable from a legitimate empty history and support retry.
- Sticky navigation/actions must not obstruct content or focused inputs; reduced-motion settings disable decorative motion.

## Tasks

### 1. Foundation and authentication strip (coordinator)

Files: `frontend/src/styles/tokens.css`, `global.css`, `locales/en.ts`, `zh-CN.ts`, `components/dashboard/ExchangeAuthNotice.vue` and its focused test.

- [x] Define the teal/forest palette, local font stack, control/focus defaults, tabular figures, and reduced motion.
- [x] Keep a persistent named alert with environment/code and a semantic Settings link; add a focused regression for failed/unavailable/redacted states before changing its markup.

### 2. Shell and navigation

Files: `frontend/src/App.vue`, `components/layout/AppHeader.vue`, `AppSidebar.vue`, their tests, and `App.test.ts` if needed.

- [x] Preserve existing sockets/auth watchers; refine layout, route icons and active state, mobile header and drawer.
- [x] Add a main-content skip target and truthful expanded state on the menu button; prove keyboard/navigation contracts with focused tests.

### 3. Shared data components

Files: `frontend/src/components/ui/*.vue`, dashboard presentation components excluding ExchangeAuthNotice/ExchangeAuthBadge.

- [x] Tighten cards, metrics, tables and empty states; preserve props/slots and data behavior.
- [x] Keep existing component regressions passing; check numerical alignment, small screens, and reduced motion.

### 4. Six-route refinement

Files: `frontend/src/views/*.vue`, `components/backtest/*`, `market/*`, `strategy/*`, `trades/*`, `settings/*`, with relevant tests.

- [x] Adapt all six pages to the common visual system and compact form/metric layouts while retaining every field/action.
- [x] Write a failing history-error/retry regression, implement persistent history error presentation, and make it pass.
- [x] Run focused route/form tests and check dirty/save/strategy action behavior stays intact.

### 5. Integrated acceptance

- [x] Run all frontend tests, TypeScript, production build, and `git diff --check`.
- [x] Review the integrated diff independently, then fix material findings.
- [x] Browser-check all six routes on desktop and representative mobile/tablet layouts; do not save credentials, start strategies, or submit trades. Reset temporary viewport overrides.
- [x] Verify runtime state and preserve the user's current settings; leave the frontend running for manual testing. The settings file changed after the UI baseline during manual testing, so unchanged settings are not claimed.

## Verification evidence

- Frontend: 61 files / 579 tests passed; TypeScript and production build passed.
- Backend: 4,127 tests passed; Ruff and whitespace checks passed. Existing dependency and bundle-size warnings remain non-blocking.
- Independent UI review: no Critical or Important findings; 18 files / 125 tests passed independently. Incremental auth review: no blockers; 45 API tests passed independently.
- Browser: all six routes reviewed on desktop; representative 320/390 px phone and 900 px tablet layouts have one main heading and no overflowing controls. Market candles and volume visibly rendered. Mobile menu semantics and focus restoration verified. Temporary viewport overrides were reset.
- Runtime: local health returns 200/ok, ports 3000 and 8080 remain available, the configured strategy remains stopped, and sandbox authentication returns authenticated with no error reason/code.
- Settings baseline was captured at 12:14:57; the current file was modified at 12:17:42. Its hash differs; current settings were preserved without a save or restore by the UI acceptance work.
- Screenshot: `/Users/zane/.codex/visualizations/2026/11/09/01a1be4b-8fcb-7f73-917c-6d11f8ef4f92/quant-workspace-ui-final.jpg`.

## Rulings

- Direct execution was explicitly selected by the user; no further design approval checkpoint is needed.
- Preserve the existing worktree and branch so the live authentication fix stays available throughout refinement.
- New UI work takes precedence over the old blue accent recommendation in the historical August UI plan; existing data and interaction safeguards continue to apply.
- Acceptance found an auth-probe compatibility issue: OKX returns Boolean account flags, while the probe reused a stricter full-facts parser. A narrow exception to the original no-backend-edit scope validates only the successful envelope and UID for authentication. Facts parsers/stores, identity verification, and trading readiness remain unchanged. Regression coverage includes the real Boolean response shape.
