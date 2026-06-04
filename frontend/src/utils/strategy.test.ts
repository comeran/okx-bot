import { describe, expect, it } from 'vitest';

import { getStrategyActionState, getStrategyStatusTagType } from './strategy';

describe('strategy UI helpers', () => {
  it('disables invalid actions for running strategies', () => {
    expect(getStrategyActionState({ name: 'ma_cross', status: 'running' }, '')).toEqual({
      startDisabled: true,
      stopDisabled: false,
      actionLoading: false,
    });
  });

  it('disables invalid actions for stopped strategies', () => {
    expect(getStrategyActionState({ name: 'ma_cross', status: 'stopped' }, '')).toEqual({
      startDisabled: false,
      stopDisabled: true,
      actionLoading: false,
    });
  });

  it('sets row loading only for the active strategy action', () => {
    expect(getStrategyActionState({ name: 'ma_cross', status: 'running' }, 'ma_cross').actionLoading).toBe(true);
    expect(getStrategyActionState({ name: 'other', status: 'running' }, 'ma_cross').actionLoading).toBe(false);
  });

  it('maps strategy statuses to tag types', () => {
    expect(getStrategyStatusTagType('running')).toBe('success');
    expect(getStrategyStatusTagType('stopped')).toBe('info');
    expect(getStrategyStatusTagType('error')).toBe('danger');
    expect(getStrategyStatusTagType('starting')).toBe('warning');
  });
});
