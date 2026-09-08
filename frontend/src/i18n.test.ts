import { beforeEach, describe, expect, it, vi } from 'vitest';

import en from './locales/en';
import zhCN from './locales/zh-CN';
import { createI18nInstance, getSavedLocale, setLocale } from './i18n';

function collectKeyPaths(value: unknown, prefix = ''): string[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];

  return Object.entries(value as Record<string, unknown>).flatMap(([key, nextValue]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof nextValue === 'object' && nextValue !== null && !Array.isArray(nextValue)
      ? [path, ...collectKeyPaths(nextValue, path)]
      : [path];
  });
}

describe('i18n', () => {
  const storage = new Map<string, string>();
  const requiredBranches = ['app', 'common', 'dashboard', 'strategies', 'market', 'backtest', 'trades', 'settings'] as const;

  beforeEach(() => {
    storage.clear();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    });
  });

  it('keeps locale key structure aligned between English and Simplified Chinese', () => {
    const englishKeys = collectKeyPaths(en);
    const chineseKeys = collectKeyPaths(zhCN);

    for (const branch of requiredBranches) {
      expect(englishKeys.some((key) => key === branch || key.startsWith(`${branch}.`))).toBe(true);
      expect(chineseKeys.some((key) => key === branch || key.startsWith(`${branch}.`))).toBe(true);
    }

    expect(chineseKeys).toEqual(englishKeys);
  });

  it('defaults to English and switches to Simplified Chinese with persistence', () => {
    const i18n = createI18nInstance();

    expect(i18n.global.t('nav.dashboard')).toBe('Dashboard');
    expect(i18n.global.t('strategies.description')).toBe(
      'Create and manage persisted strategy instances and their runtime state.',
    );
    expect(i18n.global.t('strategies.actions.edit', { name: 'desk:btc' })).toBe(
      'Edit strategy desk:btc',
    );

    setLocale(i18n, 'zh-CN');

    expect(i18n.global.t('nav.dashboard')).toBe('仪表盘');
    expect(i18n.global.t('strategies.description')).toBe(
      '创建并管理持久化策略实例及其运行状态。',
    );
    expect(i18n.global.t('strategies.actions.edit', { name: 'desk:btc' })).toBe(
      '编辑策略 desk:btc',
    );
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
