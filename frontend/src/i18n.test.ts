import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createI18nInstance, getSavedLocale, setLocale } from './i18n';

describe('i18n', () => {
  const storage = new Map<string, string>();

  beforeEach(() => {
    storage.clear();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    });
  });

  it('defaults to English and switches to Simplified Chinese with persistence', () => {
    const i18n = createI18nInstance();

    expect(i18n.global.t('nav.dashboard')).toBe('Dashboard');

    setLocale(i18n, 'zh-CN');

    expect(i18n.global.t('nav.dashboard')).toBe('仪表盘');
    expect(getSavedLocale()).toBe('zh-CN');
  });

  it('falls back when locale persistence is unavailable', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new DOMException('blocked', 'SecurityError');
      },
      setItem: () => {
        throw new DOMException('blocked', 'SecurityError');
      },
    });

    const i18n = createI18nInstance();

    expect(i18n.global.t('nav.dashboard')).toBe('Dashboard');
    expect(() => setLocale(i18n, 'zh-CN')).not.toThrow();
    expect(i18n.global.t('nav.dashboard')).toBe('仪表盘');
  });
});
