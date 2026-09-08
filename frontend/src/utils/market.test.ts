import { describe, expect, it } from 'vitest';

import { buildMarketKlineQuery } from './market';

describe('buildMarketKlineQuery', () => {
  const baseInput = {
    symbol: 'BTC-USDT',
    timeframe: '1h',
    limit: 100,
    startTime: null,
    endTime: null,
    marketType: 'spot',
  };

  it('builds a query without range fields when no dates are provided', () => {
    expect(buildMarketKlineQuery(baseInput)).toEqual({
      query: {
        symbol: 'BTC-USDT',
        timeframe: '1h',
        limit: 100,
        market_type: 'spot',
      },
      rangeQuery: false,
    });
  });

  it('builds a query with millisecond range fields when both dates are valid', () => {
    const startTime = new Date('2026-08-01T00:00:00.000Z');
    const endTime = new Date('2026-08-02T00:00:00.000Z');

    expect(buildMarketKlineQuery({ ...baseInput, startTime, endTime, marketType: 'swap' })).toEqual({
      query: {
        symbol: 'BTC-USDT',
        timeframe: '1h',
        limit: 100,
        start_time: startTime.getTime(),
        end_time: endTime.getTime(),
        market_type: 'swap',
      },
      rangeQuery: true,
    });
  });

  it('trims the symbol before building the query', () => {
    const result = buildMarketKlineQuery({ ...baseInput, symbol: '  ETH-USDT  ' });

    expect('query' in result ? result.query.symbol : '').toBe('ETH-USDT');
  });

  it('returns symbolRequired for blank symbols', () => {
    expect(buildMarketKlineQuery({ ...baseInput, symbol: '   ' })).toEqual({ error: 'symbolRequired' });
  });

  it('returns incompleteRange when only one range date is present', () => {
    expect(buildMarketKlineQuery({
      ...baseInput,
      startTime: new Date('2026-08-01T00:00:00.000Z'),
      endTime: null,
    })).toEqual({ error: 'incompleteRange' });
  });

  it('returns invalidRange when end is not after start', () => {
    expect(buildMarketKlineQuery({
      ...baseInput,
      startTime: new Date('2026-08-02T00:00:00.000Z'),
      endTime: new Date('2026-08-02T00:00:00.000Z'),
    })).toEqual({ error: 'invalidRange' });
  });
});
