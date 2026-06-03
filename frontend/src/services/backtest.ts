import axios from 'axios';

import type { BacktestMetrics, BacktestRequest, BacktestResult } from '@/types/backtest';

export async function runBacktest(request: BacktestRequest): Promise<BacktestMetrics> {
  const response = await axios.post<BacktestMetrics>('/api/backtest/run', request);
  return response.data;
}

export async function fetchBacktestResults(): Promise<BacktestResult[]> {
  const response = await axios.get<BacktestResult[]>('/api/backtest/results');
  return response.data;
}
