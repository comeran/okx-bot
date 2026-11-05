import axios from 'axios';

import type { TradeRecord, TradesQuery } from '@/types/trades';

export async function fetchTrades(query?: TradesQuery): Promise<TradeRecord[]> {
  if (query?.strategy) {
    const response = await axios.get<TradeRecord[]>('/api/trading/trades', {
      params: query,
    });
    return response.data;
  }
  const [strategyResponse, exchangeResponse] = await Promise.all([
    axios.get<TradeRecord[]>('/api/trading/trades', { params: query }),
    axios.get<TradeRecord[]>('/api/trading/trades', {
      params: { ...query, scope: 'exchange' },
    }),
  ]);
  return [...strategyResponse.data, ...exchangeResponse.data].sort(
    (left, right) => right.timestamp - left.timestamp,
  );
}
