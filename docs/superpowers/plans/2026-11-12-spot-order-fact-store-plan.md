# Account-scoped exact SPOT order fact store implementation plan

Base: `origin/main` (`9838a92`). Branch: `codex/spot-order-fact-store`.

## Tasks

- [x] Add `PrivateSpotOrderRecord` and append-only SQLite constraints/triggers.
- [x] Add transaction/repository commit/read methods with raw/hash/typed verification and account/UID isolation.
- [x] Add `sync_exact_spot_order` to the exact private sync module with captured one-shot adapter and pre-I/O timestamp/UID validation.
- [x] Add focused store/sync tests, then run full backend, Ruff and whitespace checks.
- [ ] Dispatch independent review, fix any Critical/Important findings, commit, push, open and merge the PR, then verify `origin/main`.

## Constraints

Do not expose an API route or alter compatibility orders, fills, ledgers, receipts, reconciliation, activation, or strategy behavior. Preserve append-only semantics and exact raw strings. Keep opening parentheses on the construct line.
