import axios from 'axios';

export type BacktestValidationError = 'timeRequired' | 'endAfterStart' | 'initialCapitalPositive';

export const EMPTY_BACKTEST_VALUE = '—';

export function getBacktestApiErrorMessage(error: unknown): string | null {
  if (!axios.isAxiosError(error)) {
    return null;
  }

  const detail = error.response?.data?.detail;
  if (typeof detail !== 'string') {
    return null;
  }

  const message = detail.trim();
  return message.length > 0 ? message : null;
}

export function getBacktestValidationError(
  startTime: Date | null,
  endTime: Date | null,
  initialCapital: number | null | undefined,
): BacktestValidationError | null {
  if (!startTime || !endTime || !Number.isFinite(startTime.getTime()) || !Number.isFinite(endTime.getTime())) {
    return 'timeRequired';
  }

  if (endTime.getTime() <= startTime.getTime()) {
    return 'endAfterStart';
  }

  if (typeof initialCapital !== 'number' || !Number.isFinite(initialCapital) || initialCapital <= 0) {
    return 'initialCapitalPositive';
  }

  return null;
}

export function formatBacktestNumber(value?: number | null, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_BACKTEST_VALUE;
  }

  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatBacktestPercent(value?: number | null, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_BACKTEST_VALUE;
  }

  return `${(value * 100).toFixed(digits)}%`;
}

export function formatBacktestTime(timestamp?: number | null, locale = 'en'): string {
  if (timestamp === null || timestamp === undefined || !Number.isFinite(timestamp)) {
    return EMPTY_BACKTEST_VALUE;
  }

  return new Date(timestamp).toLocaleString(locale);
}

export function formatBacktestTimestamp(timestamp?: number | null, locale = 'en'): string {
  return formatBacktestTime(timestamp, locale);
}
