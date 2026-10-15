import axios from 'axios';
import { describe, expect, it, vi } from 'vitest';

import { fetchStrategyPerformance } from './trading';

vi.mock('axios');

const mockedAxios = vi.mocked(axios);

describe('trading service', () => {
  it('fetches strategy performance from the trading API', async () => {
    const rows = [
      {
        strategy: 'ma_cross_btc',
        initial_equity: 1000,
        equity: 1042.5,
        return_pct: 0.0425,
        realized_pnl: 30,
        unrealized_pnl: 12.5,
        fees_paid: 1.5,
        position_notional: 250,
        open_positions: 1,
        order_count: 4,
        filled_order_count: 3,
        trade_count: 5,
        closed_trade_count: 2,
        winning_trade_count: 1,
        losing_trade_count: 1,
        win_rate: 0.5,
        last_order_at: 1700000000000,
      },
    ];
    mockedAxios.get.mockResolvedValueOnce({ data: rows });

    await expect(fetchStrategyPerformance()).resolves.toEqual(rows);

    expect(mockedAxios.get).toHaveBeenCalledWith('/api/trading/strategy-performance');
  });
});
