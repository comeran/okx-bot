# Exact account balance facts implementation

## Delivered

- [x] Identity-free immutable account/asset DTOs and a strict one-row raw parser.
- [x] Preserve precise USD equity and per-currency strings, with missing/null/empty
  states, duplicate-currency rejection and no funds or safety inference.
- [x] Existing scoped UID binding required for balance storage; optional explicit
  UID must match. Balance data cannot bind a new identity.
- [x] Append-only durable observations with original JSON, payload hash, receive
  time, exact top-level states and complete details JSON. Replays are idempotent;
  changed observations remain available after repository restart.
- [x] Reuse bounded credential-free raw payload validation and atomic raw/typed
  equality checks; preserve all compatibility, ledger and activation tables.
- [x] One-response raw adapter and sync helper, with cancellation checkpoint and
  atomic rollback. No real network, frontend or activation integration.

## Verification

- TDD RED: all 128 new parser/store/sync cases failed against the missing module
  and helper before production implementation.
- Focused GREEN: 128 balance cases pass; the combined balance/config/exact-store/
  existing-orchestration regression suite passes 311 cases.
- Ruff across `src` and `tests`, and `git diff --check`: passed.
- Full backend suite: 2859 passed in 35.54s, with one existing aiohttp
  deprecation warning.

The full suite blocks external network through the shared test fixture. Coverage
includes exact precision beyond float, USD-versus-USDT semantics, raw states,
duplicate currencies, malformed envelopes, credential/size rejection, UID/scope
isolation, replay/change retention, original JSON/restart readback, SQL append-only
enforcement, transactional rollback, pending cancellation and provider failures.

## Deferred

Available-funds policy, source freshness, bills reconciliation, instrument rules,
automatic catch-up, scheduling and funded allocation/activation remain future
slices. Stored balance observations alone authorize none of these actions.
