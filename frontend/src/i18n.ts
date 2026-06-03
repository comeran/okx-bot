import { createI18n } from 'vue-i18n';

import en from './locales/en';
import zhCN from './locales/zh-CN';

export const locales = ['en', 'zh-CN'] as const;
export type Locale = (typeof locales)[number];

const DEFAULT_LOCALE: Locale = 'en';
const LOCALE_STORAGE_KEY = 'okx-bot-locale';

const messages = {
  en,
  'zh-CN': zhCN,
};

function isLocale(value: string | null): value is Locale {
  return locales.some((locale) => locale === value);
}

function readStoredLocale(): string | null {
  try {
    return globalThis.localStorage.getItem(LOCALE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredLocale(locale: Locale): void {
  try {
    globalThis.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    return;
  }
}

export function getSavedLocale(): Locale {
  const locale = readStoredLocale();
  return isLocale(locale) ? locale : DEFAULT_LOCALE;
}

export function createI18nInstance() {
  return createI18n({
    legacy: false,
    locale: getSavedLocale(),
    fallbackLocale: DEFAULT_LOCALE,
    messages,
  });
}

export type AppI18n = ReturnType<typeof createI18nInstance>;

export function saveLocale(locale: Locale): void {
  writeStoredLocale(locale);
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale;
  }
}

export function setLocale(i18n: AppI18n, locale: Locale): void {
  i18n.global.locale.value = locale;
  saveLocale(locale);
}
