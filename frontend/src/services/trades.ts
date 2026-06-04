import axios from 'axios';

import type { TradeRecord, TradesQuery } from '@/types/trades';

export async function fetchTrades(query?: TradesQuery): Promise<TradeRecord[]> {
  const response = await axios.get<TradeRecord[]>('/api/trading/trades', {
    params: query,
  });
  return response.data;
}
