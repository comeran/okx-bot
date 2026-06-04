import { describe, expect, it, vi } from 'vitest';
import axios from 'axios';

import { fetchTrades } from './trades';

vi.mock('axios');

const mockedAxios = vi.mocked(axios);

describe('trades service', () => {
  it('loads trade history from the trading API', async () => {
    const trades = [
      {
        id: 1,
        strategy: 'ma_cross',
        symbol: 'BTC-USDT',
        side: 'buy',
        amount: 0.1,
        price: 68000,
        fee: 1.2,
        timestamp: 1700000000000,
      },
    ];
    mockedAxios.get.mockResolvedValueOnce({ data: trades });

    const result = await fetchTrades();

    expect(mockedAxios.get).toHaveBeenCalledWith('/api/trading/trades', {
      params: undefined,
    });
    expect(result).toEqual(trades);
  });

  it('forwards the strategy filter to the trading API', async () => {
    mockedAxios.get.mockResolvedValueOnce({ data: [] });

    await fetchTrades({ strategy: 'ma_cross' });

    expect(mockedAxios.get).toHaveBeenCalledWith('/api/trading/trades', {
      params: { strategy: 'ma_cross' },
    });
  });
});
