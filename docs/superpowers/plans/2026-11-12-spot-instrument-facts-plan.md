# Exact SPOT instrument facts implementation

## Delivered

- [x] Immutable exact SPOT instrument DTO and strict raw success-envelope parser.
- [x] Native instrument/currency consistency, positive bounded tick/lot/minimum
  decimal strings and missing/null/empty optional provider states.
- [x] Derivative, malformed, duplicate and conflicting observation rejection.
- [x] Injected single-response public adapter with exact raw SPOT parameters and
  requested-instrument validation before transport and after response parsing.
- [x] Keep acquisition isolated from persistence, trading authorization and orders.
- [x] Add append-only account/environment-scoped response and instrument tables,
  storing raw JSON once and retaining exact strings and all optional value states.
- [x] Add atomic raw/typed repository commit validation, detached reads,
  canonical-payload replay idempotence and changed-observation history.
- [x] Add single-response raw instrument sync with cancellation before commit,
  exact SPOT/filter parameters and no private UID requirement.
- [x] Cover scope isolation, restart, direct SQL immutability, response integrity,
  credential/size guards and provider/parser/storage failure rollback.

## Verification

- TDD RED: 214 cases failed against the missing module before production code.
- Focused GREEN: all 214 instrument parser/adapter cases pass.
- Ruff across `src` and `tests`, and `git diff --check`: passed.
- Full backend suite: 3073 passed in 36.56s, with one existing aiohttp
  deprecation warning.

Tests cover precision beyond float, unchanged tick/lot/minimum strings, immutable
facts, missing/null/empty optional states, bounded malformed/negative/zero values,
SPOT/native identity, duplicate/conflict handling, exact raw parameters, request
filter matching, provider/parser errors and no retries. A transport that rejects
every other method and a storage guard verify that acquisition does not load
markets, call unified/private/order APIs, open storage or mutate raw input. The
shared test fixture blocks external network.

## Deferred

Freshness policy, order constraint enforcement, automatic refresh, reconciliation
and funded allocation/activation remain future work. These slices make no real
exchange requests.

## Durable extension verification

- TDD RED: 61 new persistence/sync cases failed against absent interfaces before
  production changes. Additional direct-insert tests demonstrated orphan response
  links were initially accepted; the response integrity trigger closes that gap.
- Focused GREEN: 343 instrument and exact config/balance/fill storage/sync cases
  passed, including 65 new durable instrument cases.
- Ruff across `src` and `tests`: passed.
- Full backend suite: 3138 passed in 41.01s, with the existing aiohttp
  deprecation warning. `git diff --check`: passed.
