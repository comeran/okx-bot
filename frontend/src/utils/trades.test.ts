import { describe, expect, it } from 'vitest';

import type { TradeRecord } from '@/types/trades';
import {
  buildTradeFilterOptions,
  createTradeFilters,
  filterTrades,
  formatTradeNumber,
  summarizeTrades,
} from './trades';

const trades: TradeRecord[] = [
  {
    id: 1,
    strategy: 'MA_Cross',
    symbol: 'BTC-USDT',
    side: 'buy',
    amount: 0.1,
    price: 60000,
    fee: 1.2,
    timestamp: 1700000000000,
  },
  {
    id: 2,
    strategy: 'rsi_mean_reversion',
    symbol: 'ETH-USDT',
    side: 'sell',
    amount: 2,
    price: 3000,
    fee: 0.9,
    timestamp: 1700003600000,
  },
  {
    id: 3,
    strategy: 'donchian_breakout',
    symbol: 'BTC-USDT-SWAP',
    side: 'buy',
    amount: 0.05,
    price: 61000,
    fee: 0.5,
    timestamp: 1700007200000,
  },
];

describe('trades utils', () => {
  it('returns all loaded trades when filters are clear', () => {
    expect(filterTrades(trades, createTradeFilters())).toEqual(trades);
  });

  it('filters by strategy case-insensitively', () => {
    expect(filterTrades(trades, { ...createTradeFilters(), strategy: 'ma_cross' }).map((trade) => trade.id)).toEqual([1]);
  });

  it('filters by symbol case-insensitively', () => {
    expect(filterTrades(trades, { ...createTradeFilters(), symbol: 'eth-usdt' }).map((trade) => trade.id)).toEqual([2]);
  });

  it('filters by side case-insensitively', () => {
    expect(filterTrades(trades, { ...createTradeFilters(), side: 'BUY' }).map((trade) => trade.id)).toEqual([1, 3]);
  });

  it('filters by search term across strategy, symbol, and side', () => {
    expect(filterTrades(trades, { ...createTradeFilters(), search: 'donchian' }).map((trade) => trade.id)).toEqual([3]);
    expect(filterTrades(trades, { ...createTradeFilters(), search: 'swap' }).map((trade) => trade.id)).toEqual([3]);
    expect(filterTrades(trades, { ...createTradeFilters(), search: 'sell' }).map((trade) => trade.id)).toEqual([2]);
  });

  it('returns no matches when the loaded records do not satisfy the filters', () => {
    expect(filterTrades(trades, { ...createTradeFilters(), strategy: 'missing' })).toEqual([]);
  });

  it('summarizes total notional and fees while marking PnL counts unavailable', () => {
    expect(summarizeTrades(trades)).toEqual({
      totalTrades: 3,
      totalNotional: 15050,
      totalFees: 2.6,
      positivePnlCount: null,
      negativePnlCount: null,
    });
  });

  it('keeps nullable financial values as missing instead of fabricating zeros', () => {
    const nullableTrade = {
      id: 4,
      strategy: 'nullable_strategy',
      symbol: 'SOL-USDT',
      side: 'buy',
      amount: null,
      price: null,
      fee: null,
      timestamp: 1700010000000,
    } as unknown as TradeRecord;

    expect(summarizeTrades([nullableTrade])).toEqual({
      totalTrades: 1,
      totalNotional: null,
      totalFees: null,
      positivePnlCount: null,
      negativePnlCount: null,
    });
    expect(formatTradeNumber(null)).toBe('—');
  });

  it('builds stable options from loaded records', () => {
    expect(buildTradeFilterOptions(trades as TradeRecord[])).toEqual({
      strategies: ['donchian_breakout', 'MA_Cross', 'rsi_mean_reversion'],
      symbols: ['BTC-USDT', 'BTC-USDT-SWAP', 'ETH-USDT'],
    });
  });
});
