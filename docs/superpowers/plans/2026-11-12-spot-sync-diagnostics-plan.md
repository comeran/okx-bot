# Spot synchronization diagnostics implementation plan

Base: `origin/main` (`debcbb9`). Branch: `codex/spot-sync-diagnostics`.

## Tasks

- [ ] Add `src/ops/spot_sync_diagnostic.py` with detached bounded snapshot DTOs, four conservative checks, strict local-time/policy validation, and no raw JSON.
- [ ] Extend the accounting transaction and `Repository` with a read-only `get_private_sync_status` path that validates scope and returns detached records in the same read transaction used by spot preflight evidence.
- [ ] Extend `build_stored_spot_preflight` and `POST /api/ops/spot/preflight` to include `sync_diagnostic`, preserving the existing `evidence` dictionary and execution gates.
- [ ] Run focused RED/GREEN tests, then the complete backend suite, Ruff, and whitespace checks.
- [ ] Dispatch an independent review of the complete diff, fix any Critical/Important findings, commit, push, open and merge the PR through the GitHub API, then verify `origin/main` and a clean worktree.

## Constraints

The response is read-only and account-bound. Never call an exchange adapter from preflight. Never let client JSON override server evidence. Never turn receipt state, part coverage, provider timestamps, or compatibility cursor flags into `sync_completeness` or execution authority. Keep opening parentheses on the construct line.
