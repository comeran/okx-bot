# Exact sync input validation implementation plan

Base: `origin/main` (`8ff444a`). Branch: `codex/exact-received-at-validation`.

## Tasks

- [ ] Add RED tests covering invalid `received_at` for config, balance, instruments, and fill-page collectors with zero provider/repository calls.
- [ ] Add a shared validator and invoke it before repository/provider access in `src/ops/exact_private_sync.py`.
- [ ] Run focused exact-sync tests, full backend suite, Ruff, whitespace checks, independent review, then commit, push, create and merge the PR.

## Constraints

Keep the existing error type/message and all successful call parameters unchanged. Preserve read/write scope and do not add trading authority.
