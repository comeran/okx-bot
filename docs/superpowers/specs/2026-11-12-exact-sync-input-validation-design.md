# Exact sync input validation design

## Intent

Make every exact private-facts collector validate its caller-supplied local observation timestamp before touching a repository or provider. The trade-fee collector already has this fail-before-I/O boundary; configuration, balance, instrument, and fill-page collectors must match it.

## Scope

Add one shared bounded integer timestamp validator and call it at the first side-effecting boundary of the four collectors. Valid values are an actual `int` (not `bool`) from `0` through `2**63 - 1`. Keep existing provider, parser, persistence, UID, and cursor behavior unchanged.

## Non-goals

No provider retries, schema changes, API routes, account binding changes, receipt changes, order behavior, ledger mutations, or data migration.

## Verification

Regression tests use injected fake providers and repositories to prove every invalid value fails before any provider or repository call. Existing exact-facts, fill catch-up, and full backend tests must remain green.
