import { describe, expect, it, vi } from 'vitest';
import axios from 'axios';

import { fetchKlines, fetchTickers } from './market';

vi.mock('axios');

const mockedAxios = vi.mocked(axios);

describe('market service', () => {
  it('passes market type when fetching klines', async () => {
    const query = {
      symbol: 'BTC-USDT-SWAP',
      timeframe: '1h',
      limit: 100,
      market_type: 'swap',
    };
    mockedAxios.get.mockResolvedValueOnce({ data: [] });

    const result = await fetchKlines(query);

    expect(mockedAxios.get).toHaveBeenCalledWith('/api/market/klines', { params: query });
    expect(result).toEqual([]);
  });

  it('passes market type and symbols when fetching tickers', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: [{ instId: 'BTC-USDT-260626-100000-C', last: '1.0' }],
    });

    const result = await fetchTickers('option', ['BTC-USDT-260626-100000-C']);

    expect(mockedAxios.get).toHaveBeenCalledWith('/api/market/tickers', {
      params: {
        market_type: 'option',
        symbols: ['BTC-USDT-260626-100000-C'],
      },
    });
    expect(result[0].symbol).toBe('BTC-USDT-260626-100000-C');
  });
});
