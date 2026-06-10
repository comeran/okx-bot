import { describe, expect, it } from 'vitest';

import { getBacktestApiErrorMessage, getBacktestValidationError } from './backtest';

function axiosErrorWithDetail(detail?: unknown): unknown {
  return {
    isAxiosError: true,
    response: {
      data: detail === undefined ? {} : { detail },
    },
  };
}

describe('backtest API error messages', () => {
  it('returns a trimmed FastAPI detail string from an Axios error', () => {
    expect(getBacktestApiErrorMessage(axiosErrorWithDetail(' insufficient historical data '))).toBe(
      'insufficient historical data',
    );
  });

  it('returns null for a non-Axios error', () => {
    expect(getBacktestApiErrorMessage(new Error('failed'))).toBeNull();
  });

  it('returns null when detail is missing or not a string', () => {
    expect(getBacktestApiErrorMessage(axiosErrorWithDetail())).toBeNull();
    expect(getBacktestApiErrorMessage(axiosErrorWithDetail({ message: 'failed' }))).toBeNull();
  });

  it('returns null when detail is empty or whitespace', () => {
    expect(getBacktestApiErrorMessage(axiosErrorWithDetail(''))).toBeNull();
    expect(getBacktestApiErrorMessage(axiosErrorWithDetail('   '))).toBeNull();
  });
});

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
