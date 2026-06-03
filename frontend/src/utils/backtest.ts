export type BacktestValidationError = 'timeRequired' | 'endAfterStart' | 'initialCapitalPositive';

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
