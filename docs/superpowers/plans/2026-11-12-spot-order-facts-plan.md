# Exact SPOT order facts implementation plan

Base: `origin/main` (`f66a229`). Branch: `codex/spot-order-facts`.

## Tasks

- [ ] Define immutable `SpotOrderFact`, bounded field/state constants, strict parser, and injected one-request `OKXSpotOrderFactsAdapter` in `src/exchange/okx_order_facts.py`.
- [ ] Add focused unit tests for parser, request identity, raw parameter shape, one-call/no-retry behavior, and all malformed/unknown boundaries.
- [ ] Run focused tests, full backend suite, Ruff, and whitespace checks.
- [ ] Dispatch independent review, fix any Critical/Important findings, commit, push, open and merge the PR, then verify `origin/main` and the worktree.

## Constraints

This slice is read-only and provider-transport injectable. Do not add database tables, repository methods, API routes, sync receipt fields, order-manager behavior, ledger/reconciliation changes, or live trading paths. Preserve the project convention of opening parentheses on the construct line.
