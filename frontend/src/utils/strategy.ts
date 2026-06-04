import type { StrategySummary } from '@/types/strategy';

export interface StrategyActionState {
  startDisabled: boolean;
  stopDisabled: boolean;
  actionLoading: boolean;
}

export function getStrategyActionState(strategy: StrategySummary, actionName: string): StrategyActionState {
  return {
    startDisabled: strategy.status === 'running',
    stopDisabled: strategy.status !== 'running',
    actionLoading: actionName === strategy.name,
  };
}

export function getStrategyStatusTagType(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'running') return 'success';
  if (status === 'stopped') return 'info';
  if (status === 'error') return 'danger';
  return 'warning';
}
