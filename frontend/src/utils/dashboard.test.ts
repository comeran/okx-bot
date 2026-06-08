import { describe, expect, it } from 'vitest';

import {
  EMPTY_RUNTIME_VALUE,
  formatRuntimeCurrency,
  formatRuntimeNumber,
  formatRuntimePayloadPreview,
  formatRuntimeText,
  formatRuntimeTime,
  formatTickerPrice,
  getDashboardStrategyStatusTagType,
} from './dashboard';


describe('dashboard runtime UI helpers', () => {
  it('renders missing runtime values as an em dash', () => {
    expect(formatRuntimeCurrency(undefined)).toBe(EMPTY_RUNTIME_VALUE);
    expect(formatRuntimeNumber(undefined)).toBe(EMPTY_RUNTIME_VALUE);
    expect(formatRuntimeText('')).toBe(EMPTY_RUNTIME_VALUE);
    expect(formatRuntimeTime(undefined)).toBe(EMPTY_RUNTIME_VALUE);
    expect(formatTickerPrice('')).toBe(EMPTY_RUNTIME_VALUE);
  });

  it('formats finite numeric runtime values', () => {
    expect(formatRuntimeCurrency(1234.5)).toBe('$1,234.50');
    expect(formatRuntimeNumber(0.123456789)).toBe('0.12345679');
    expect(formatTickerPrice('68000.12345')).toBe('68,000.1235');
  });

  it('builds short payload previews without received metadata', () => {
    expect(formatRuntimePayloadPreview({
      type: 'strategy_error',
      strategy: 'ma_cross_btc',
      error: 'boom',
      received_at: 1700000000000,
    })).toBe('{"strategy":"ma_cross_btc","error":"boom"}');
  });

  it('truncates long payload previews', () => {
    const preview = formatRuntimePayloadPreview({ type: 'raw', data: 'x'.repeat(150) });

    expect(preview.endsWith('…')).toBe(true);
    expect(preview.length).toBe(121);
  });

  it('maps strategy statuses to Element Plus tag types', () => {
    expect(getDashboardStrategyStatusTagType('running')).toBe('success');
    expect(getDashboardStrategyStatusTagType('stopped')).toBe('info');
    expect(getDashboardStrategyStatusTagType('error')).toBe('danger');
    expect(getDashboardStrategyStatusTagType('starting')).toBe('warning');
  });
});
