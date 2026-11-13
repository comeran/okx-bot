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

export interface PaperReservation {
  strategy: string;
  symbol: string;
  budget: number;
  cash: number;
  holdings: number;
  revision: number;
  scope_id: string;
  status: 'active';
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
  draft: StrategyAllocationDraft | null;
  reservation?: PaperReservation | null;
  other_scope_reservation?: boolean;
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
