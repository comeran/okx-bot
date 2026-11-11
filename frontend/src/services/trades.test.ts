import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';

import { fetchTrades } from './trades';

vi.mock('axios');

const mockedAxios = vi.mocked(axios);

describe('trades service', () => {
  beforeEach(() => {
    mockedAxios.get.mockReset();
  });

  it('loads strategy and exchange trade history from the trading API', async () => {
    const strategyTrades = [
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
    const exchangeTrades = [
      {
        id: 2,
        strategy: '__exchange__',
        symbol: 'ETH-USDT-SWAP',
        side: 'sell',
        amount: 1,
        price: 3000,
        fee: 0.5,
        timestamp: 1700000600000,
      },
    ];
    mockedAxios.get
      .mockResolvedValueOnce({ data: strategyTrades })
      .mockResolvedValueOnce({ data: exchangeTrades });

    const result = await fetchTrades(undefined, 'scope-A');

    expect(mockedAxios.get).toHaveBeenCalledWith('/api/trading/trades', {
      params: { expected_account_scope_id: 'scope-A' },
    });
    expect(mockedAxios.get).toHaveBeenCalledWith('/api/trading/trades', {
      params: { scope: 'exchange', expected_account_scope_id: 'scope-A' },
    });
    expect(result).toEqual([...exchangeTrades, ...strategyTrades]);
  });

  it('forwards the strategy filter to the trading API', async () => {
    mockedAxios.get.mockResolvedValueOnce({ data: [] });

    await fetchTrades({ strategy: 'ma_cross' });

    expect(mockedAxios.get).toHaveBeenCalledTimes(1);
    expect(mockedAxios.get).toHaveBeenCalledWith('/api/trading/trades', {
      params: { strategy: 'ma_cross' },
    });
  });
  it('keeps paper history available without configured exchange credentials', async () => {
    mockedAxios.get.mockResolvedValueOnce({ data: [{ strategy: 'paper', timestamp: 1 }] });
    await expect(fetchTrades(undefined, null)).resolves.toEqual([{ strategy: 'paper', timestamp: 1 }]);
    expect(mockedAxios.get).toHaveBeenCalledTimes(1);
    expect(mockedAxios.get).toHaveBeenCalledWith('/api/trading/trades', { params: undefined });
  });
});
