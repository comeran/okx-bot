# Accurate OKX Authentication Feedback Implementation Plan

> **For agentic workers:** Use subagent-driven development with independent backend and frontend ownership, followed by a fresh whole-change review.

**Goal:** Never present a local socket connection as successful OKX authentication, and surface a real read-only authentication failure.

**Architecture:** A bounded saved-credential probe returns a sanitized observation. An epoch-scoped frontend store drives shared account badges and error notices independently of local transport.

**Tech Stack:** FastAPI, ccxt, pytest; Vue, Pinia, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-13-exchange-auth-status-design.md`

## Global constraints

- No changes to credentials, account facts, trading permissions, strategy state, ledgers, or execution readiness.
- Probe only saved credentials, make one raw account/config request, bound I/O, and close transport.
- Never return/log raw provider errors or secrets.
- Existing context/snapshot GETs keep their no-provider-I/O semantics.

## Shared contract

`POST /api/settings/exchange-auth?expected_account_scope_id=<current scope>` has no body. Its JSON contains `account_scope_id: string|null`, `environment: sandbox|live`, `status: authenticated|failed|unavailable|unconfigured`, `reason: string|null`, `provider_code: string|null`, `checked_at: integer`. Scope/settings races return HTTP 409 with `detail.code=account_scope_changed`. Missing credentials never contact OKX. Successful auth does not change identity or readiness.

## Tasks

- [x] Backend: write RED integration/unit tests for probe, redaction, scope checks, request bounding, cleanup, and races; implement helper/route; make focused tests green.
- [x] Frontend: write RED store/component tests for failed auth with connected local socket, stale epochs, deduplication, retry and settings save; implement service/types/store, shared badge/notice, automatic probe and settings action; make focused tests green.
- [x] Integrate and independently review secret handling, same-key credential changes, cancellation, stale frontend work and truthful save feedback.
- [x] Run full backend/frontend tests, Ruff, TypeScript/build and whitespace checks.
- [x] Restart backend only; verify current credentials produce a visible failed-auth status on the running app while local service remains connected. Keep code reviewable in the worktree.

## Verification evidence

- Independent review: no Critical or Important findings.
- Backend: 4,119 tests passed; Ruff passed. One existing aiohttp deprecation warning.
- Frontend: 60 test files / 569 tests passed; TypeScript and production build passed. Existing Node/dependency/bundle-size warnings remain non-blocking.
- Runtime probe: sandbox returned `failed / invalid_credentials / 50119`; context identity and execution readiness remained false.
- Browser: at approximately 300 px wide, local service connected and OKX authentication failed are separately readable, with a persistent safe error notice containing 50119.
- Settings checksum and strategy statuses unchanged; the configured strategy remains stopped. No strategy was started or exchange order submitted.

### Follow-up during UI acceptance

- After the user updated saved credentials, the runtime probe returned authenticated in sandbox. The earlier 50119 result above records the original failed-credential check and remains valid historical evidence.
- Successful OKX responses include Boolean account flags. The auth probe now validates the envelope and UID without requiring unrelated full-facts fields to match parser types; facts parsing/storage and trading readiness are unchanged.
- Latest combined validation: 4,127 backend tests and 579 frontend tests passed, along with Ruff, TypeScript/build and whitespace checks. Independent incremental auth review passed 45 API tests.
- The current settings file differs from the later UI baseline and was preserved; the configured strategy remains stopped. Authentication success does not mark account facts identity-verified or execution-ready.

## Review focus

- A successful old probe must not verify replacement credentials or a new environment.
- Raw provider text can contain credentials and must never enter responses, notices, or logs.
- A timeout is unavailable, not a false bad-password claim or success.
- A healthy local socket cannot override failed/unverified OKX state.
- Saving unrelated settings and deduplicated callers must not falsely report authenticated or retain a stale success.
