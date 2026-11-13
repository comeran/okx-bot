import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getApiToken, setApiToken, withApiToken } from './apiToken';

describe('apiToken', () => {
  const storage = new Map<string, string>();

  beforeEach(() => {
    storage.clear();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => {
        storage.set(key, value);
      },
      removeItem: (key: string) => {
        storage.delete(key);
      },
    });
  });

  it('attaches a saved token to websocket paths and omits it when empty', () => {
    expect(withApiToken('/ws?scope=exchange')).toBe('/ws?scope=exchange');

    setApiToken(' desk-token ');

    expect(getApiToken()).toBe('desk-token');
    expect(withApiToken('/ws?scope=exchange')).toBe('/ws?scope=exchange&token=desk-token');
  });
});
