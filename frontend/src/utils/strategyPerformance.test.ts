import { describe, expect, it } from 'vitest';

import {
  enrichStrategyPerformanceRows,
  mergeStrategyPerformanceRows,
} from './strategyPerformance';

describe('mergeStrategyPerformanceRows', () => {
  it('merges runtime and metric rows with runtime status authority', () => {
    const runtimeSummaries = [
      { name: 'zeta', status: 'running' },
      { name: 'alpha', status: 'stopped' },
      { name: 'delta', status: 'error' },
    ];
    const performanceRows = [
      {
        name: 'alpha',
        status: 'running',
        equity: 1250,
        return_pct: 0.125,
        realized_pnl: 120,
        unrealized_pnl: -12,
        position_notional: 450,
        closed_trade_count: 8,
        win_rate: 0.63,
        fees_paid: 4.5,
        order_count: 9,
        filled_order_count: 7,
        last_order_at: 1700000000000,
        open_positions: 1,
              },
      {
        name: 'gamma',
        status: 'error',
        equity: 0,
        return_pct: 0.07,
        realized_pnl: 30,
        unrealized_pnl: 2,
        position_notional: 0,
        closed_trade_count: 1,
        win_rate: 0.4,
        fees_paid: 0.75,
        order_count: 3,
        filled_order_count: 2,
        last_order_at: null,
      },
      {
        name: 'zeta',
        equity: 900,
        return_pct: 0.05,
        realized_pnl: 15,
        unrealized_pnl: 3,
        position_notional: 210,
        closed_trade_count: 4,
        win_rate: 0.5,
        fees_paid: 2.1,
        order_count: 5,
        filled_order_count: 4,
        last_order_at: 1700000100000,
      },
    ];

    expect(mergeStrategyPerformanceRows(runtimeSummaries, performanceRows)).toEqual([
      {
        name: 'zeta',
        status: 'running',
        equity: 900,
        return_pct: 0.05,
        realized_pnl: 15,
        unrealized_pnl: 3,
        position_notional: 210,
        closed_trade_count: 4,
        win_rate: 0.5,
        fees_paid: 2.1,
        order_count: 5,
        filled_order_count: 4,
        last_order_at: 1700000100000,
        open_positions: 0,
      },
      {
        name: 'alpha',
        status: 'stopped',
        equity: 1250,
        return_pct: 0.125,
        realized_pnl: 120,
        unrealized_pnl: -12,
        position_notional: 450,
        closed_trade_count: 8,
        win_rate: 0.63,
        fees_paid: 4.5,
        order_count: 9,
        filled_order_count: 7,
        last_order_at: 1700000000000,
        open_positions: 1,
              },
      {
        name: 'delta',
        status: 'error',
        equity: 0,
        return_pct: null,
        realized_pnl: 0,
        unrealized_pnl: 0,
        position_notional: 0,
        closed_trade_count: 0,
        win_rate: null,
        fees_paid: 0,
        order_count: 0,
        filled_order_count: 0,
        last_order_at: null,
        open_positions: 0,
      },
      {
        name: 'gamma',
        status: 'unknown',
        equity: 0,
        return_pct: 0.07,
        realized_pnl: 30,
        unrealized_pnl: 2,
        position_notional: 0,
        closed_trade_count: 1,
        win_rate: 0.4,
        fees_paid: 0.75,
        order_count: 3,
        filled_order_count: 2,
        last_order_at: null,
        open_positions: 0,
      },
    ]);
  });

  it('enriches rows with current strategy details and caps newest orders at 20', () => {
    const orders = Array.from({ length: 21 }, (_, index) => ({
      strategy: 'alpha',
      symbol: `BTC-${index}`,
      timestamp: 1700000000000 + index,
    }));

    const rows = enrichStrategyPerformanceRows(
      [{ name: 'alpha', status: 'running' }],
      [
        { name: 'alpha', position_notional: 450, open_positions: 2 },
        { name: 'historical', position_notional: 90, open_positions: 1 },
      ],
      [
        { strategy: 'alpha', symbol: 'BTC-USDT-SWAP' },
        { strategy: 'other', symbol: 'ETH-USDT-SWAP' },
      ],
      [...orders, { strategy: 'other', symbol: 'ETH-USDT-SWAP', timestamp: 1800000000000 }],
    );

    expect(rows[0]).toMatchObject({
      name: 'alpha',
      position_notional: 450,
      open_positions: 2,
      positions: [{ strategy: 'alpha', symbol: 'BTC-USDT-SWAP' }],
    });
    expect(rows[0].recent_orders).toHaveLength(20);
    expect(rows[0].recent_orders[0]).toMatchObject({ symbol: 'BTC-20', timestamp: 1700000000020 });
    expect(rows[0].recent_orders.at(-1)).toMatchObject({ symbol: 'BTC-1', timestamp: 1700000000001 });
    expect(rows[1]).toMatchObject({
      name: 'historical',
      position_notional: 90,
      open_positions: 1,
      positions: [],
      recent_orders: [],
    });
  });

  it('returns an empty list when both inputs are empty', () => {
    expect(mergeStrategyPerformanceRows([], [])).toEqual([]);
  });
});
