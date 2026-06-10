import axios from 'axios';

export type BacktestValidationError = 'timeRequired' | 'endAfterStart' | 'initialCapitalPositive';

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
