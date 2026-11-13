# Account-scoped exact SPOT order fact store design

## Intent

Persist the raw, request-bound SPOT order facts produced by the exact order-detail adapter so later recovery can compare observations over time. The store is an append-only audit journal, not an order-state authority.

## Goals

- Bind each observation to the validated account scope and existing private UID.
- Retain the lookup identity (`ordId` or `clOrdId`), requested native symbol, local `received_at`, raw JSON, deterministic payload hash, and bounded projections needed for lookup/audit.
- Make an identical lookup/response replay idempotent while allowing later observations of the same order to append.
- Verify raw JSON against the typed `SpotOrderFact` and stored projections on both write and read.
- Keep reads detached, account-bound, and free of provider calls or operational writes.
- Add a one-shot `sync_exact_spot_order` helper that validates local input before repository/provider access and commits the captured response atomically.

## Non-goals

No automatic polling, retries, cancellation, resubmission, terminal-state inference, legacy order mutation, ledger/reserve changes, reconciliation, activation, or completeness claim. The journal does not prove exchange UID ownership beyond the caller's existing local UID binding.

## Approach

Add an append-only `PrivateSpotOrderRecord` table with composite identity `(account_scope_id, uid, requested_inst_id, lookup_kind, lookup_value, payload_hash)`, raw JSON, receipt time, and exact order identity/state projections. Repository commit validates account scope/UID, canonical payload/hash, request context, and typed parser equality in one transaction. Repository reads scan all rows for foreign scope/UID contamination, re-parse raw JSON with the stored lookup context, and return detached records.

The sync helper follows existing exact collectors: capture the raw adapter response, yield once for cancellation, and call the repository's atomic commit. It never updates compatibility tables or sync receipts.

## Safety rules

- Lookup kind is exactly `ordId` or `clOrdId`; lookup value is bounded raw text.
- Stored `inst_type` is SPOT and requested/returned symbols and identities must agree.
- Invalid timestamps, UID conflicts, malformed/raw mismatches, credential-bearing payloads, or foreign rows fail closed before any operational write.
- Duplicate payloads return the existing detached record; a different payload hash is a new observation and cannot overwrite history.

## Verification

Tests cover account/UID binding, request identity, same-response replay, later-state append, raw/hash/typed mismatch, foreign-row contamination, malformed/credential payloads, rollback, detached reads, invalid timestamps before I/O, and zero legacy/ledger side effects.
