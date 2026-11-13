# Accurate OKX authentication feedback

## Problem and intent

The current badge treats an accepted local account WebSocket as a connected OKX account. Saving credentials never contacts OKX, so an invalid key can display Connected and no authentication error. Make local transport health and observed provider authentication separate, visible states.

## Approach

Add a one-shot read-only `POST /api/settings/exchange-auth` probe of the saved credentials against raw OKX account/config. The request carries `expected_account_scope_id`; return only account scope, environment, status, reason, provider code, and checked time. Do not return credentials, raw provider errors, or UID. No database writes, retries, notifications, strategy start, or trading authority.

Statuses are `authenticated`, `failed`, `unavailable`, and `unconfigured`. Reasons are bounded stable codes: `invalid_credentials`, `environment_mismatch`, `permission_denied`, `timestamp_invalid`, `authentication_failed`, `provider_unavailable`, `malformed_response`, `credentials_unavailable`, `unsupported_market`, and `account_scope_changed`. A successful probe is an observation, not execution readiness or durable account identity.

Use a bounded request timeout and always close the client. Reject scope mismatches before provider access. Re-read settings after the await and reject results if any credential, environment, or market changed, including same-key secret/passphrase changes. Never echo provider exception text.

Frontend account context owns the last probe and checking state, deduplicates concurrent checks, and discards results from old epochs. Check once when a configured context loads and again after saving settings; offer a manual retry of saved credentials. Clear old verification immediately on invalidation/recheck. Show a persistent translated failure/unavailable notice with a safe provider code. Header identifies local service transport separately from the OKX verification badge; dashboard uses the same verification badge. Credential save success must not imply authentication success.

## Alternatives

Changing the Connected label alone would leave actual invalid credentials undetected. Probing on every GET context or cached snapshot would add hidden I/O and break existing read semantics. A dedicated bounded probe plus explicit state avoids both problems.

## Boundaries and verification

Keep `identity_verified` and `execution_ready` false and preserve current account-store/facts semantics. Tests cover no credentials, successful raw config, 50119, environment/permission/network/malformed failures, secret redaction, cancellation/cleanup, scope and in-flight settings changes, stale frontend responses, retries, save feedback, and badges on a healthy local socket with failed provider auth. Run backend/frontend suites, lint/build, and manually verify the current failing sandbox key is visibly reported.
