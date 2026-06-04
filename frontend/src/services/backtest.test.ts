import { describe, expect, it, vi } from 'vitest';
import axios from 'axios';

import { fetchBacktestResults, runBacktest } from './backtest';
import type { BacktestRequest } from '@/types/backtest';

vi.mock('axios');

const mockedAxios = vi.mocked(axios);

describe('backtest service', () => {
  it('runs a backtest through the backtest API', async () => {
    const request: BacktestRequest = {
      strategy: 'ma_cross',
      symbol: 'BTC-USDT',
      timeframe: '1h',
      start_time: 1700000000000,
      end_time: 1700100000000,
      initial_capital: 100000,
    };
    const metrics = {
      total_return: 0.12,
      sharpe_ratio: 1.8,
      max_drawdown: 0.03,
      win_rate: 0.58,
      total_trades: 24,
    };
    mockedAxios.post.mockResolvedValueOnce({ data: metrics });

    const result = await runBacktest(request);

    expect(mockedAxios.post).toHaveBeenCalledWith('/api/backtest/run', request);
    expect(result).toEqual(metrics);
  });

  it('loads historical backtest results from the backtest API', async () => {
    const results = [
      {
        id: 'bt-new',
        strategy: 'ma_cross',
        symbol: 'ETH-USDT',
        timeframe: '4h',
        start_time: 1700000000000,
        end_time: 1700100000000,
        initial_capital: 100000,
        total_return: 0.08,
        sharpe_ratio: 1.2,
        max_drawdown: 0.04,
        win_rate: 0.52,
        total_trades: 12,
        created_at: 1700100000000,
      },
    ];
    mockedAxios.get.mockResolvedValueOnce({ data: results });

    const result = await fetchBacktestResults();

    expect(mockedAxios.get).toHaveBeenCalledWith('/api/backtest/results');
    expect(result).toEqual(results);
  });
});
