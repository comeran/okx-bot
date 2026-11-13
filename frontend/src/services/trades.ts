import axios from 'axios';

import type { TradeRecord, TradesQuery } from '@/types/trades';

export async function fetchTrades(query?: TradesQuery, accountScopeId?: string | null): Promise<TradeRecord[]> {
  const params = accountScopeId ? { ...query, expected_account_scope_id: accountScopeId } : query;
  if (query?.strategy || !accountScopeId) {
    const response = await axios.get<TradeRecord[]>('/api/trading/trades', {
      params,
    });
    return response.data;
  }
  const [strategyResponse, exchangeResponse] = await Promise.all([
    axios.get<TradeRecord[]>('/api/trading/trades', { params }),
    axios.get<TradeRecord[]>('/api/trading/trades', {
      params: { ...params, scope: 'exchange' },
    }),
  ]);
  return [...strategyResponse.data, ...exchangeResponse.data].sort(
    (left, right) => right.timestamp - left.timestamp,
  );
}
