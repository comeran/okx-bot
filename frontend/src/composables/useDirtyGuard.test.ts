import { describe, expect, it, vi } from 'vitest';

import { useDirtyGuard } from './useDirtyGuard';

describe('useDirtyGuard', () => {
  it('returns true immediately when the editor is clean', async () => {
    const confirmDiscard = vi.fn(async () => false);
    const { confirmIfDirty } = useDirtyGuard(() => false, confirmDiscard);

    await expect(confirmIfDirty()).resolves.toBe(true);
    expect(confirmDiscard).not.toHaveBeenCalled();
  });

  it('delegates to the discard confirmation when dirty', async () => {
    const confirmDiscard = vi.fn(async () => true);
    const { confirmIfDirty } = useDirtyGuard(() => true, confirmDiscard);

    await expect(confirmIfDirty()).resolves.toBe(true);
    expect(confirmDiscard).toHaveBeenCalledTimes(1);
  });
});
