import type { ExecutionContext } from './executionContext';

export interface AllocationContext {
  available: boolean;
  reason: 'credentials_unavailable' | 'unsupported_market' | null;
  scope_id: string | null;
  exchange: 'okx';
  environment: 'sandbox' | 'live';
  account_fingerprint: string | null;
  identity_verified: false;
  market_type: string;
  execution_mode: string;
  draft_only: true;
  execution_ready: false;
}

export interface StrategyAllocationDraft {
  strategy: string;
  symbol: string;
  currency: string;
  budget: number;
  revision: number;
  status: 'draft';
  scope_id: string;
  environment: 'sandbox' | 'live';
  account_fingerprint: string;
  market_type: string;
  created_at: number;
  updated_at: number;
}

export interface StrategyAllocationEnvelope {
  context: AllocationContext;
  execution_context: ExecutionContext;
  draft: StrategyAllocationDraft | null;
  strategy: string;
  symbol: string;
  currency: string;
  lifecycle_status: string;
  can_edit: boolean;
}

export interface StrategyAllocationUpdate {
  budget: number;
  expected_revision: number;
  expected_scope_id: string;
  expected_symbol: string;
}

export interface StrategyAllocationReadinessRequest {
  expected_account_scope_id: string;
  expected_scope_id: string;
  expected_revision: number;
  expected_symbol: string;
}

export interface PreparedSpotLedger {
  strategy: string;
  symbol: string;
  currency: string;
  base_currency: string;
  allocation_scope_id: string;
  draft_revision: number;
  initial_quote: string;
  status: 'prepared';
  execution_ready: false;
  quote_balance: string;
  quote_reserved: string;
  quote_available: string;
  base_balance: string;
  base_reserved: string;
  base_available: string;
  cost_basis_quote: string;
  realized_pnl_quote: string;
  fees: Record<string, string>;
  version: number;
  updated_at: number;
}

export type AllocationReadinessCheckCode =
  | 'strategy_stopped'
  | 'account_snapshot'
  | 'provider_timestamp'
  | 'global_kill'
  | 'account_kill'
  | 'observed_open_orders'
  | 'quote_capacity'
  | 'ledger_state'
  | 'identity_verified'
  | 'account_mode'
  | 'instrument_metadata'
  | 'sync_completeness'
  | 'reconciliation'
  | 'activation_implemented';

export interface StrategyAllocationReadiness {
  execution_context: ExecutionContext;
  allocation_scope_id: string;
  draft_revision: number;
  strategy: string;
  symbol: string;
  currency: string;
  budget: string;
  evaluated_at: number;
  capacity: {
    observed_quote_available: string | null;
    proposed_total: string;
    comparison: 'within_observed' | 'exceeds_observed' | 'unavailable';
    trusted: false;
  };
  ledger: PreparedSpotLedger | null;
  checks: { code: AllocationReadinessCheckCode; status: 'passed' | 'blocked' | 'unknown' }[];
  can_activate: false;
  execution_ready: false;
}
