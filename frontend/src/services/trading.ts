import axios from 'axios';

import type { MarketType } from '@/utils/market';
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

export interface ManualOrderRequest {
  symbol: string;
  market_type?: MarketType;
  side: 'buy' | 'sell';
  order_type: 'market' | 'limit';
  amount: number;
  price?: number | null;
}

export interface RestingOrder {
  id?: string;
  order_id?: string;
  symbol?: string;
  side?: string;
  type?: string;
  amount?: number;
  price?: number | null;
  status?: string;
}

export async function fetchTradingOrders(): Promise<RestingOrder[]> {
  return (await axios.get<RestingOrder[]>('/api/trading/orders')).data;
}

export async function submitManualOrder(body: ManualOrderRequest): Promise<Record<string, unknown>> {
  return (await axios.post<Record<string, unknown>>('/api/trading/orders', body)).data;
}

export async function cancelTradingOrder(orderId: string, symbol: string): Promise<Record<string, unknown>> {
  return (await axios.post<Record<string, unknown>>(
    `/api/trading/orders/${encodeURIComponent(orderId)}/cancel`,
    null,
    { params: { symbol } },
  )).data;
}

export async function amendTradingOrder(orderId: string, symbol: string, price: number): Promise<Record<string, unknown>> {
  return (await axios.post<Record<string, unknown>>(
    `/api/trading/orders/${encodeURIComponent(orderId)}/amend`,
    { symbol, price },
  )).data;
}

export async function refreshLiveState(): Promise<Record<string, unknown>> {
  return (await axios.post<Record<string, unknown>>('/api/trading/live-state/refresh', null, {
    params: { strategy: '__exchange__' },
  })).data;
}

export async function fetchStrategyPerformance(accountScopeId?: string | null): Promise<StrategyPerformance[]> {
  const response = accountScopeId
    ? await axios.get<StrategyPerformance[]>('/api/trading/strategy-performance', { params: { expected_account_scope_id: accountScopeId } })
    : await axios.get<StrategyPerformance[]>('/api/trading/strategy-performance');
  return response.data;
}
