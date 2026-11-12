# Persisted spot preflight diagnostics

Continue the approved spot-first roadmap with a read-only bridge from exact stored observations to the pure preflight evaluator. No exchange requests, allocation funding, reservations, strategy activation or order submission.

## Approach

Select and validate evidence inside one repository transaction, then evaluate it in a separate diagnostic helper and expose `POST /api/ops/spot/preflight`. Reusing the legacy float account projection would lose precision; accepting client-supplied facts or readiness flags would bypass provenance. The server owns receipt-age policy (30,000 ms) and evaluation time.

The request accepts only native `symbol`, exact `side`, `amount` and `price` strings. Require the existing `expected_account_scope_id` handshake. Extra fields (including fee or readiness attestations) are rejected. Existing context remains execution_ready=false.

## Evidence contract

- Validate the immutable store header against the expected scope and UID binding. Read config, balance and instrument response in one consistent transaction. Return detached immutable facts.
- Select latest by `received_at DESC, rowid DESC`, including equal-time observations. Replays retain their original time. Do not search backwards for a passing observation.
- Use the latest instrument response as a whole. If it is empty or contains only another symbol, the requested instrument is unavailable. This may conservatively invalidate another symbol after a filtered response; request scope is not persisted yet, so omission cannot certify its rules.
- Reparse bounded credential-free raw JSON, recompute canonical hash, and compare all typed fields and missing/null/value markers. Balance details must match raw details; the instrument response's complete typed row set must match raw rows, hashes, scope and receipt time. Corruption fails with a sanitized 503, without falling back.
- Expose only per-source hash/receipt/presence, account binding and evaluator checks. Do not serialize raw JSON, private configuration values or full balances. The hash is integrity/provenance evidence, not proof of provider authenticity.
- Missing facts remain unknown. Stale/future receipts remain unknown. Fee reserve, liability freedom, complete reconciliation and full synchronization stay unknown, so can_submit and can_activate remain false.

## Acceptance

Real temporary scoped-store tests cover exact reconstruction, latest/empty/tied/replayed selection, corruption and cross-scope refusal, and unchanged database rows. HTTP tests cover the scope handshake, strict request contract, sanitized output/failures, freshness and unknown blockers. Full backend suite, Ruff, diff check and independent branch review precede delivery. No live provider or trading acceptance is claimed.
