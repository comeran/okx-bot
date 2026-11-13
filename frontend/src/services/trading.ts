import axios from 'axios';

import type { StrategyPerformance } from '@/types/strategyPerformance';
import type { ExchangeSnapshot, ExecutionContext } from '@/types/executionContext';

export interface ExchangeAccountSyncResult {
  status: 'ok';
  context: ExecutionContext;
  account_upserted: number;
  orders_upserted: number;
  trades_upserted: number;
  trades_status: 'available' | 'unavailable';
  risk_events_saved: number;
  kill_switch_engaged: boolean;
  divergences: unknown[];
}

export async function syncExchangeAccount(accountScopeId: string): Promise<ExchangeAccountSyncResult> {
  return (await axios.post<ExchangeAccountSyncResult>('/api/ops/sync/private', {}, {
    params: { expected_account_scope_id: accountScopeId },
  })).data;
}

export async function fetchTradingContext(): Promise<ExecutionContext> {
  return (await axios.get<ExecutionContext>('/api/trading/context')).data;
}

export async function fetchExchangeSnapshot(accountScopeId: string): Promise<ExchangeSnapshot> {
  return (await axios.get<ExchangeSnapshot>('/api/trading/snapshot', {
    params: { expected_account_scope_id: accountScopeId },
  })).data;
}

export async function fetchStrategyPerformance(accountScopeId?: string | null): Promise<StrategyPerformance[]> {
  const response = accountScopeId
    ? await axios.get<StrategyPerformance[]>('/api/trading/strategy-performance', { params: { expected_account_scope_id: accountScopeId } })
    : await axios.get<StrategyPerformance[]>('/api/trading/strategy-performance');
  return response.data;
}
