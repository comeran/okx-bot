# Account isolation — Phase 2a

User intent: continue the incremental roadmap after merging Phase 1, targeting personal, single-node OKX spot use. This slice makes private facts attributable to the configured credential/environment and prevents account switches from mixing data. It does not activate strategy allocations or trading.

## Storage decision

Use a separate SQLite facts database for each immutable execution account scope. The master database retains configuration, allocation drafts, local paper/backtest facts, global kill state, and ambiguous legacy live audit. An execution scope hashes exchange (`okx`), environment (`sandbox` or `live`) and the SHA-256 fingerprint of the configured API key; market type is a capability, not account identity. Credential binding is not a verified exchange UID. Key rotation creates a new history; no automatic cross-key merging or legacy imports.

The alternative was adding scope columns and replacing every uniqueness, migration, merge and query path in the legacy repository. For this single-node application, separate facts files reuse tested matching logic while preventing old startup migrations from deduplicating across accounts. Costs: backup must include the facts directory, there is no cross-file transaction, and eventual execution must check global OR account-local kill state. This slice performs no cross-file financial mutation.

Paths derive exclusively from the master SQLite database directory, master filename and server-generated scope hash: `account-facts/<master-filename>/<scope>.db`. A metadata header binds each facts file to its immutable scope. Initialize a new header atomically and validate an existing header before constructing Repository or running migrations. Missing, corrupt or mismatching headers fail closed, never fall back to master, and never stamp old data as current-account facts. Header validation uses normal read-only SQLite transactions, which may create SQLite-managed sidecars but never mutate financial rows; bootstrap locking uses a separate lock file. No environment-variable mutation. No network requests during context resolution or snapshot reads.

## Server contract

`build_execution_context(exchange) -> (context, ExecutionScope | None)` produces `account_scope_id`, exchange, environment, market_type, `identity_verified:false`, available, reason, `execution_ready:false`. Missing credentials and unsupported markets produce unavailable contexts. Scope ID excludes market type; the Phase 1 allocation scope and its persisted IDs are unchanged.

`open_exchange_repository(scope, *, master_engine) -> Repository` opens only the validated facts file. Request handlers capture settings once, copy the exchange config, derive context and repository once, then use that binding through network awaits, serialization and broadcasts.

`GET /api/trading/context` is authoritative and has no precondition. `GET /api/trading/snapshot?expected_account_scope_id=...` returns `{type:'snapshot', scope:'exchange', context, data:{scope:'exchange', strategy:'__exchange__', account:null|AccountSummary, positions:[], orders:[]}}`. A configured context requires a matching account precondition; unavailable context returns empty facts. Missing snapshots are null, not fabricated balances. Existing exchange array/account routes retain shape but require the same precondition and read only this facts file. Live aggregate, strategy and performance views remain empty until strategy ownership/accounting is implemented; paper/backtest views retain existing behavior.

Existing explicit private-sync/refresh endpoints bind adapter, facts repository and returned context to the same captured exchange configuration. No new automatic private fetch or sync is introduced. Manual private sync remains sandbox-only. Exchange websocket connections require the matching `expected_account_scope_id`; initial snapshots and financial broadcasts carry context. The manager filters by exact scope ID and environment; unbound exchange risk events are rejected. A settings-save `context_changed` control event triggers authoritative reload, never directly chooses an account from an event payload. Global kill writes remain master-only; expose global/account/effective kill states in snapshots, where effective is OR. No manager receives this facts repository or new ownership hooks in this slice.

## Client contract

Resolve authoritative context before requesting financial data or connecting exchange websocket. Maintain a monotonically increasing account epoch, independent of account ID and per-field websocket versions. Invalidate pending financial requests, errors/finally handlers, performance and trade requests when context reload starts; clear account, positions, orders, performance and visible financial message history. A→B→A does not revive the first A request. Validate account binding before recording a financial message or triggering derived refreshes. Closed sockets ignore normal and malformed late messages.

Settings save and server context_changed trigger reload and reconnect. A delayed account event cannot redefine the active account. Show unavailable/unverified context and missing snapshots explicitly. Strategy configuration and paper lifecycle status remain available.

## Acceptance

1. Distinct keys or sandbox/live contexts can persist identical exchange order/trade IDs independently and survive reopen.
2. Legacy master live rows never appear in normal REST/WS; invalid headers fail before repository initialization and do not modify the file.
3. Changing settings while sync waits does not change the target database or broadcast context.
4. Context mismatch/missing binding fails closed, including empty arrays and exchange risk events.
5. A→B and A→B→A requests, late socket messages and stale errors/finally cannot overwrite the active context or message history.
6. Global/account kill combinations produce the correct effective state. Clearing global kill cannot clear an account divergence.
7. Phase 1 drafts still grant no ownership or execution permission. No strategy starts or external trading operations are part of development verification.
