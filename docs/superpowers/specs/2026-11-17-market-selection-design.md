# Market selection independent of API credentials

The user approved separating API credentials from market selection and asked to include the configuration pages. One saved OKX credential set/environment serves spot, swap, future and option operations. Account capabilities and provider permission errors remain authoritative.

## Approach

Use each instrument symbol as the durable market identity. Add market selectors to strategy editing and manual orders, filter instrument choices by that market, and validate the selected market against the instrument at request boundaries. Existing strategy symbols already encode their market, so no strategy schema migration is needed. A cosmetic settings-only removal leaves execution coupled; adding duplicate market identity to every persisted strategy risks inconsistent symbol/market pairs. Operation routing plus symbol inference avoids both.

## Behavior

- Settings expose credentials and demo/live environment without a market selector. Legacy saved market_type remains readable for compatibility but no longer determines account access or operation routing; newly saved settings omit it.
- Account context and authentication describe the credential/environment, independent of a global market. Account context reports market_type=all and market_types=[spot,swap,future,option]; account identity and stored account history do not change.
- Symbol inference supports native OKX and existing supported CCXT symbols. Invalid/contradictory market-symbol requests fail before provider calls.
- Strategy execution and allocation scope derive their market from the strategy symbol. Spot and derivative strategies can use the same credentials concurrently. Existing allocation hashes and facts remain intact.
- Manual submit/cancel/amend route by their instrument, with an optional explicit market selection validated against it. UI switches clear incompatible instrument choices rather than silently trading a different market.
- Account-wide private sync/refresh reads account-wide facts across markets, keeping timestamp, completeness and stale-position reconciliation guarantees. Market-scoped adapters remain available for specific operations.
- Account synchronization scopes provider requests with the appropriate instrument type before parsing, reads the balance once, and bounds stale-position cleanup by the oldest covered market snapshot. Recent-trade aggregation preserves every fetched market page for order reconciliation and keeps incomplete-history markers.
- The shared market-data client uses CCXT's valid spot default; exact native/CCXT instrument identifiers select the actual market. The account adapter's auto label is an internal routing marker and is never passed as a CCXT default type.
- Preserve existing UI components/styles and all pre-existing working tree edits. No live orders, strategy starts, credential changes, deployment, push or merge are part of verification.

## Verification

Regressions cover shared credentials with two markets, symbol/market mismatch rejection, independent allocation scopes, market-neutral account/auth context, legacy settings, settings save payload, strategy instrument filtering and manual order routing. Run relevant backend/frontend tests, then complete suites and frontend build. Review the integrated diff independently.
