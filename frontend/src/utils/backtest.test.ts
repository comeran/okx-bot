import { describe, expect, it } from 'vitest';

import { getBacktestValidationError } from './backtest';

describe('backtest validation', () => {
  it('rejects missing start or end time', () => {
    const startTime = new Date('2026-01-01T00:00:00Z');
    const endTime = new Date('2026-01-02T00:00:00Z');

    expect(getBacktestValidationError(null, endTime, 100000)).toBe('timeRequired');
    expect(getBacktestValidationError(startTime, null, 100000)).toBe('timeRequired');
  });

  it('rejects invalid start or end time', () => {
    const startTime = new Date('2026-01-01T00:00:00Z');
    const endTime = new Date('2026-01-02T00:00:00Z');

    expect(getBacktestValidationError(new Date('invalid'), endTime, 100000)).toBe('timeRequired');
    expect(getBacktestValidationError(startTime, new Date('invalid'), 100000)).toBe('timeRequired');
  });

  it('rejects end time before start time', () => {
    expect(
      getBacktestValidationError(
        new Date('2026-01-02T00:00:00Z'),
        new Date('2026-01-01T00:00:00Z'),
        100000,
      ),
    ).toBe('endAfterStart');
  });

  it('rejects missing or non-positive initial capital', () => {
    const startTime = new Date('2026-01-01T00:00:00Z');
    const endTime = new Date('2026-01-02T00:00:00Z');

    expect(getBacktestValidationError(startTime, endTime, null)).toBe('initialCapitalPositive');
    expect(getBacktestValidationError(startTime, endTime, Number.NaN)).toBe('initialCapitalPositive');
    expect(getBacktestValidationError(startTime, endTime, 0)).toBe('initialCapitalPositive');
  });

  it('accepts valid backtest inputs', () => {
    expect(
      getBacktestValidationError(
        new Date('2026-01-01T00:00:00Z'),
        new Date('2026-01-02T00:00:00Z'),
        100000,
      ),
    ).toBeNull();
  });
});
