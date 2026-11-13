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

export const exchangeAuthenticationReasons = [
  'invalid_credentials', 'environment_mismatch', 'permission_denied', 'timestamp_invalid',
  'authentication_failed', 'provider_unavailable', 'malformed_response',
  'credentials_unavailable', 'unsupported_market', 'account_scope_changed',
] as const;

export type ExchangeAuthenticationReason = typeof exchangeAuthenticationReasons[number];

export const exchangeAuthenticationProviderCodes: readonly string[] = [
  '50101', '50102', '50103', '50104', '50105', '50110', '50111',
  '50112', '50113', '50114', '50119', '50120',
];

export interface ExchangeAuthentication {
  account_scope_id: string | null;
  environment: 'sandbox' | 'live';
  status: 'authenticated' | 'failed' | 'unavailable' | 'unconfigured';
  reason: ExchangeAuthenticationReason | null;
  provider_code: string | null;
  checked_at: number;
}

export interface ExchangeSnapshot {
  type: 'snapshot';
  scope: 'exchange';
  context: ExecutionContext;
  data: DashboardSnapshot & { scope: 'exchange'; strategy: '__exchange__' };
}
