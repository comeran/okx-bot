import type { StrategyRuntimeSummary } from '@/types/strategy';

export interface StrategyActionState {
  startDisabled: boolean;
  stopDisabled: boolean;
  actionLoading: boolean;
}

export function getStrategyActionState(
  strategy: StrategyRuntimeSummary,
  actionLoading: boolean,
): StrategyActionState {
  return {
    startDisabled: strategy.status === 'running',
    stopDisabled: strategy.status !== 'running',
    actionLoading,
  };
}

export function getStrategyStatusTagType(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'running') return 'success';
  if (status === 'stopped') return 'info';
  if (status === 'error') return 'danger';
  return 'warning';
}
