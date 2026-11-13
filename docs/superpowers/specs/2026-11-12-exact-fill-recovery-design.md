# Verified exact-fill recovery

Continue the authorized spot-first synchronization/recovery roadmap. Before resuming a durable fill cursor, verify that the account's exact raw page journals, retained fill projections and cursors form one consistent committed state. This is local recovery integrity, not provider history completeness, reconciliation of account equity, or execution authority. No live provider requests, financial initialization, repair or ledger writes are performed by this task.

## Current gap and approach

Current cursor reads validate only a subset of the account binding. Prior-page reconstruction on append uses ordinary json.loads without checking saved raw hashes, page identities or the cursor's complete chain. Exact fill reads trust typed rows. A corrupt/missing page, altered projection or drifted cursor can therefore be used to resume before it is detected.

Reconstruct the existing exact store before any exact read or append/replay. In one read transaction load all page rows in insertion rowid order, all cursors and all fill rows. Validate the immutable store scope and existing UID binding; reject foreign/orphan rows. Reuse the bounded duplicate-key/credential-rejecting canonical raw decoder and strict OKX page parser. Validate each page's hash, receipt integer, SPOT type, recomputed traversal ID and page ID, filters, next_after and conservative coverage. Each traversal starts with requested_after=None; each later request must equal its predecessor's nonempty tail. Parse with all earlier pages from that traversal so cycles, repeated pages and financial overlaps remain authoritative. An empty page terminates that local chain but proves no completeness.

Each traversal must have exactly one cursor with matching context, version equal to committed page count, tail equal to the final parsed tail and complete=False. Reject missing and orphan cursors. Do not silently rebuild or advance a corrupted cursor.

Build the expected fill projection from the first occurrence of each billId in global journal insertion order. Validate the full retained projection, including symbol, record timestamp and optional client/fee states. Later observations may change record timestamp/client tag as the existing parser permits; retain the first observation and require exact immutable financial fields to agree. Require exact sets of projected and journal-observed billIds, rejecting missing/orphan fills. No cross-currency aggregation or float conversion.

Use the same verifier for get_exact_spot_fills/pages/cursor and at the start of commit_exact_spot_fill_page, including its idempotent replay path. A failed verification returns no resumable cursor, makes no row changes and happens before the orchestration helper's next provider call. Verify again within append's existing write transaction to catch corruption/concurrent drift after capture. Keep existing CAS/replay and rollback semantics. Enforce initial requested_after=None for new traversals so new writes cannot create states that recovery would reject. Endpoint remains explicit validated request context; do not merge different endpoint/instrument/before traversals.

## Tradeoffs and scope

Replaying journals is preferred over trusting cursor/version fields or automatically repairing them: repair requires an explicit recovery decision and retained source evidence. This adds history-dependent read/append cost (the existing parser rechecks prior pages); no speculative checkpoints or migration are added in this slice. An account's corrupt exact traversal blocks all exact recovery reads rather than presenting a healthy subset; unrelated compatibility/config/balance data remains untouched. A fully coordinated malicious rewrite/removal of all evidence cannot be authenticated by an unkeyed local hash and is outside this integrity guarantee.

No new HTTP endpoint, background worker, retry, completeness/fee/liability/reconciliation attestation or activation behavior. Existing exact orchestration automatically receives verified cursors through its existing repository interface. Empty tails, short pages and bounded catch-up remain unknown/partial.

## Acceptance

TDD reproduces hash/raw/duplicate-key corruption, bad page/traversal identities and filters, missing page/fill/cursor, cursor version/tail/context drift, optional/financial projection changes and foreign rows. Reads and fresh/replayed commits fail closed with unchanged rows; reopened sync/catch-up makes zero fake-provider requests on corruption. Valid multiple/interleaved traversals, first-seen metadata, overlaps, reordered receipt timestamps, empty tails and advanced replays remain compatible. All recovery SELECTs share a SQLite transaction, including in-memory tests. Full backend, Ruff, diff checks and independent actual-code review precede authorized PR/merge delivery.
