import { describe, expect, it } from 'vitest';

import { getStrategyActionState, getStrategyStatusTagType } from './strategy';

describe('strategy UI helpers', () => {
  it('disables invalid actions for running strategies', () => {
    expect(getStrategyActionState({ name: 'ma_cross', status: 'running' }, false)).toEqual({
      startDisabled: true,
      stopDisabled: false,
      actionLoading: false,
    });
  });

  it('disables invalid actions for stopped strategies', () => {
    expect(getStrategyActionState({ name: 'ma_cross', status: 'stopped' }, false)).toEqual({
      startDisabled: false,
      stopDisabled: true,
      actionLoading: false,
    });
  });

  it('uses the action-specific loading state supplied by the strategy store', () => {
    expect(getStrategyActionState({ name: 'ma_cross', status: 'running' }, true).actionLoading).toBe(true);
    expect(getStrategyActionState({ name: 'other', status: 'running' }, false).actionLoading).toBe(false);
  });

  it('maps strategy statuses to tag types', () => {
    expect(getStrategyStatusTagType('running')).toBe('success');
    expect(getStrategyStatusTagType('stopped')).toBe('info');
    expect(getStrategyStatusTagType('error')).toBe('danger');
    expect(getStrategyStatusTagType('starting')).toBe('warning');
  });
});
