import type { DashboardSnapshot } from './dashboard';

export interface ExecutionContext {
  account_scope_id: string | null;
  exchange: 'okx';
  environment: 'sandbox' | 'live';
  market_type: string;
  identity_verified: false;
  available: boolean;
  reason: string | null;
  execution_ready: false;
}

export interface ExchangeSnapshot {
  type: 'snapshot';
  scope: 'exchange';
  context: ExecutionContext;
  data: DashboardSnapshot & { scope: 'exchange'; strategy: '__exchange__' };
}
