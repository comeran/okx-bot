# Review Bug Fixes

**Goal:** Fix the five reproduced review findings without changing trading modes,
strategy ownership rules, or calling external trading APIs.

**Scope:** Runtime credential persistence, pytest collection isolation, Dashboard
REST/WebSocket ordering, backtest cancellation, and limit-order gap matching.
Changes stay uncommitted in the current checkout; preserve `.workbuddy/`.

## Tasks

- [x] Preserve environment references when saving credentials, and verify that
  saving ordinary settings then reloading retains all four credentials without
  writing their values to disk. Keep explicitly configured local credentials.
- [x] Establish temporary database/settings paths before pytest collection.
  Verify in a subprocess that collection neither creates nor migrates a caller's
  database, including when the environment points at an existing database.
- [x] Prevent older Dashboard REST responses from replacing newer exchange
  WebSocket account, position, or order updates. Preserve unaffected REST fields,
  loading/error behavior, and request-disposal protection.
- [x] Make backtest cancellation remove queued/resting orders and report failure
  for missing/already completed orders; verify the order never fills afterward.
- [x] Match marketable buy/sell limits across price gaps at the better opening
  price; preserve nonmarketable orders and ordinary intrabar limit fills.

## Verification

For each fix, add a regression test and observe its failure before implementation,
then run the affected tests. Before collection isolation is fixed, explicitly set
temporary `OKX_BOT_DB_PATH` and `OKX_BOT_SETTINGS_PATH` before launching pytest.
Finish with the full backend/frontend suites, Ruff, frontend type checking and
production build, followed by an independent review of the complete diff.

## Review Focus

- Environment credentials remain references and are reread after an environment change.
- Local credential values survive save/load without being overwritten by unrelated environment values.
- Collection is safe even when callers supply a database path.
- A push for one Dashboard field does not suppress unrelated REST fields.
- Cancellation during callbacks cannot requeue an order; buy/sell gap matching is symmetric.

## Completed Verification

- Credential and collection regressions: reproduced failures before the fix;
  21 focused tests pass afterward, including recovery from legacy empty settings.
- Backtest regressions: 11 new failing cases reproduced before implementation;
  44 related engine, matcher, strategy, and integration tests pass afterward.
- Dashboard regressions: nine failing cases reproduced before implementation;
  43 store tests pass afterward, including two additional ordering boundaries.
- Full backend: 1,945 passed; one dependency deprecation warning.
- Full frontend: 54 files, 480 passed; one Node environment warning.
- Ruff, TypeScript checking, production build, and diff whitespace checks pass.
  Build reports dependency annotation and large-chunk warnings.
- Independent review: scope compliance and code quality pass; no blocking findings.
- No external exchange calls, commits, or pushes performed.
