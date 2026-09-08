import axios from 'axios';

import type { StrategyPerformance } from '@/types/strategyPerformance';

export async function fetchStrategyPerformance(): Promise<StrategyPerformance[]> {
  const response = await axios.get<StrategyPerformance[]>('/api/trading/strategy-performance');
  return response.data;
}
