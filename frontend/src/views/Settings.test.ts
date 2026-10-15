import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';

import { defineHostComponent, mount, textContent, type TestHostNode } from '@/test-utils/mount';
import type { AppSettingsUpdate, AppSettingsView } from '@/types/settings';
import Settings from './Settings.vue';

const mocks = vi.hoisted(() => ({
  confirm: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  getSettings: vi.fn(),
  updateSettings: vi.fn(),
  routeGuard: null as null | (() => Promise<boolean>),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function createSettingsView(overrides: Partial<AppSettingsView> = {}): AppSettingsView {
  return {
    mode: 'backtest',
    exchange: {
      api_key: 'hidden-api-key',
      api_key_set: true,
      secret: 'hidden-secret',
      secret_set: true,
      passphrase: 'hidden-passphrase',
      passphrase_set: true,
      market_type: 'spot',
      demo: true,
    },
    backtest: {
      initial_capital: 100000,
      fee_rate: 0.0005,
      slippage: 0.001,
      data_cache_dir: './data',
    },
    risk: {
      max_daily_loss_pct: 0.05,
      max_drawdown_pct: 0.15,
      max_total_position_pct: 0.8,
      allow_live_open_orders: false,
      live_max_order_notional: 0,
    },
    notify: {
      telegram_bot_token: 'hidden-bot-token',
      telegram_bot_token_set: true,
      telegram_chat_id: '123456789',
    },
    web: {
      host: '0.0.0.0',
      port: 8080,
    },
    ...overrides,
  };
}

function createUpdatePayload(overrides: Partial<AppSettingsUpdate> = {}): AppSettingsUpdate {
  return {
    mode: 'backtest',
    exchange: {
      api_key: '',
      secret: '',
      passphrase: '',
      market_type: 'spot',
      demo: true,
    },
    backtest: {
      initial_capital: 100000,
      fee_rate: 0.0005,
      slippage: 0.001,
      data_cache_dir: './data',
    },
    risk: {
      max_daily_loss_pct: 0.05,
      max_drawdown_pct: 0.15,
      max_total_position_pct: 0.8,
      allow_live_open_orders: false,
      live_max_order_notional: 0,
    },
    notify: {
      telegram_bot_token: '',
      telegram_chat_id: '123456789',
    },
    web: {
      host: '0.0.0.0',
      port: 8080,
    },
    ...overrides,
  };
}

const components = {
  ElButton: defineHostComponent('el-button'),
  ElForm: defineHostComponent('el-form'),
  ElFormItem: defineHostComponent('el-form-item'),
  ElRow: defineHostComponent('el-row'),
  ElCol: defineHostComponent('el-col'),
  ElSelect: defineHostComponent('el-select'),
  ElOption: defineHostComponent('el-option'),
  ElSwitch: defineHostComponent('el-switch'),
  ElInput: defineHostComponent('el-input'),
  ElInputNumber: defineHostComponent('el-input-number'),
};

vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error },
  ElMessageBox: { confirm: mocks.confirm },
}));

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: { value: 'en' },
    t: (key: string, params?: Record<string, unknown>) => (params ? `${key}:${JSON.stringify(params)}` : key),
  }),
}));

vi.mock('vue-router', () => ({
  onBeforeRouteLeave: (guard: () => Promise<boolean>) => {
    mocks.routeGuard = guard;
  },
}));

const settingsSource = readFileSync(fileURLToPath(new URL('./Settings.vue', import.meta.url)), 'utf8');
const tokensSource = readFileSync(fileURLToPath(new URL('../styles/tokens.css', import.meta.url)), 'utf8');
const enLocaleSource = readFileSync(fileURLToPath(new URL('../locales/en.ts', import.meta.url)), 'utf8');
const zhLocaleSource = readFileSync(fileURLToPath(new URL('../locales/zh-CN.ts', import.meta.url)), 'utf8');

vi.mock('@/services/settings', () => ({
  getSettings: mocks.getSettings,
  updateSettings: mocks.updateSettings,
}));

function control(wrapper: Awaited<ReturnType<typeof mount>>, type: string, index = 0): TestHostNode {
  const nodes = wrapper.findAll((node) => node.type === type);
  if (!nodes[index]) {
    throw new Error(`Control ${type} at index ${index} not found`);
  }
  return nodes[index];
}

function textNodes(wrapper: Awaited<ReturnType<typeof mount>>, type: string): string[] {
  return wrapper.findAll((node) => node.type === type).map((node) => textContent(node));
}

function validationText(key: string, field: string): string {
  return `${key}:${JSON.stringify({ field })}`;
}

let serverSettings = createSettingsView();

describe('Settings view', () => {
  it('keeps the sticky header and mobile action style contract in the SFC stylesheet', () => {
    expect(settingsSource).toContain('.settings-page__header {');
    expect(settingsSource).toContain('.settings-page__header-actions {');
    expect(settingsSource).toContain('position: sticky;');
    expect(settingsSource).toContain('top: var(--ui-space-16);');
    expect(settingsSource).toContain('z-index: 20;');
    expect(settingsSource).toContain('background: linear-gradient(180deg');
    expect(settingsSource).toContain('var(--ui-color-surface)');
    expect(settingsSource).toContain('var(--ui-radius-10)');
    expect(settingsSource).toContain('@media (max-width: 767px)');
    expect(settingsSource).toMatch(/@media \(max-width: 767px\)[\s\S]*?\.settings-page__header :deep\(\.app-page-header__actions\) \{[\s\S]*?display: none;[\s\S]*?\}/);
    expect(settingsSource).toMatch(/@media \(max-width: 767px\)[\s\S]*?\.settings-page__header-actions \{[\s\S]*?display: none;[\s\S]*?\}/);
    expect(settingsSource).toMatch(/@media \(max-width: 767px\)[\s\S]*?\.settings-page__header \{[\s\S]*?top: 0;[\s\S]*?\}/);
    expect(settingsSource).toMatch(/@media \(max-width: 767px\)[\s\S]*?\.settings-page__mobile-actions \{[\s\S]*?display: flex;[\s\S]*?padding: var\(--ui-space-12\) var\(--ui-space-16\) calc\(var\(--ui-space-12\) \+ env\(safe-area-inset-bottom\)\);[\s\S]*?\}/);
    expect(settingsSource).not.toContain('--ui-color-bg');
    expect(settingsSource).not.toContain('--ui-radius-12');
    expect(settingsSource).not.toContain(':style="headerStyle"');
    expect(tokensSource).toContain('--ui-color-surface:');
    expect(tokensSource).toContain('--ui-radius-10:');
    expect(tokensSource).not.toContain('--ui-color-bg:');
    expect(tokensSource).not.toContain('--ui-radius-12:');
  });

  beforeEach(() => {
    mocks.confirm.mockReset();
    mocks.confirm.mockResolvedValue(undefined);
    mocks.success.mockReset();
    mocks.error.mockReset();
    mocks.getSettings.mockReset();
    mocks.updateSettings.mockReset();
    mocks.routeGuard = null;
    serverSettings = createSettingsView();
    mocks.getSettings.mockResolvedValue(serverSettings);
    mocks.updateSettings.mockImplementation(async (settings: AppSettingsUpdate) => {
      serverSettings = {
        ...serverSettings,
        mode: settings.mode,
        exchange: {
          api_key: settings.exchange.api_key || serverSettings.exchange.api_key,
          api_key_set: Boolean(settings.exchange.api_key || serverSettings.exchange.api_key),
          secret: settings.exchange.secret || serverSettings.exchange.secret,
          secret_set: Boolean(settings.exchange.secret || serverSettings.exchange.secret),
          passphrase: settings.exchange.passphrase || serverSettings.exchange.passphrase,
          passphrase_set: Boolean(settings.exchange.passphrase || serverSettings.exchange.passphrase),
          market_type: settings.exchange.market_type,
          demo: settings.exchange.demo,
        },
        backtest: settings.backtest,
        risk: settings.risk,
        notify: {
          telegram_bot_token: settings.notify.telegram_bot_token || serverSettings.notify.telegram_bot_token,
          telegram_bot_token_set: Boolean(settings.notify.telegram_bot_token || serverSettings.notify.telegram_bot_token),
          telegram_chat_id: settings.notify.telegram_chat_id,
        },
        web: settings.web,
      };
      return serverSettings;
    });
  });

  it('loads settings and renders the six stacked sections with configured secret indicators', async () => {
    const wrapper = await mount(Settings, { components });
    await wrapper.flush();

    expect(mocks.getSettings).toHaveBeenCalledTimes(1);
    expect(wrapper.findAll((node) => node.type === 'h2')).toHaveLength(1);
    expect(wrapper.find((node) => node.type === 'header' && String(node.props.class).includes('settings-page__header')).props.style).toBeUndefined();
    expect(wrapper.find((node) => node.props.class === 'settings-page__mobile-actions').props.style).toMatchObject({
      position: 'fixed',
      bottom: '0',
    });
    expect(textNodes(wrapper, 'h3')).toEqual([
      'settings.runtime',
      'settings.okxExchange',
      'settings.backtestDefaults',
      'settings.riskLimits',
      'settings.notifications',
      'settings.webServer',
    ]);
    expect(wrapper.text()).toContain('settings.secretConfigured');
    expect(wrapper.text()).not.toContain('hidden-api-key');
    expect(wrapper.text()).not.toContain('hidden-secret');
    expect(wrapper.text()).not.toContain('hidden-bot-token');
    expect(control(wrapper, 'el-select', 0).props.modelValue).toBe('backtest');
    expect(control(wrapper, 'el-input', 0).props.modelValue ?? control(wrapper, 'el-input', 0).props.value ?? '').toBe('');
    expect(control(wrapper, 'el-input', 1).props.modelValue ?? control(wrapper, 'el-input', 1).props.value ?? '').toBe('');
    expect(control(wrapper, 'el-input', 2).props.modelValue ?? control(wrapper, 'el-input', 2).props.value ?? '').toBe('');
  });

  it('keeps dirty edits when a reload confirmation is canceled', async () => {
    mocks.confirm.mockRejectedValueOnce(new Error('cancel'));
    const wrapper = await mount(Settings, { components });
    await wrapper.flush();

    await wrapper.invoke(control(wrapper, 'el-select', 0), 'onUpdate:modelValue', 'paper');
    await wrapper.trigger(control(wrapper, 'el-button', 0), 'click');

    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    expect(mocks.getSettings).toHaveBeenCalledTimes(1);
    expect(control(wrapper, 'el-select', 0).props.modelValue).toBe('paper');
  });

  it('blocks route leave when settings are dirty', async () => {
    mocks.confirm.mockRejectedValueOnce(new Error('cancel'));
    const wrapper = await mount(Settings, { components });
    await wrapper.flush();

    await wrapper.invoke(control(wrapper, 'el-select', 0), 'onUpdate:modelValue', 'paper');
    if (!mocks.routeGuard) throw new Error('Route guard not captured');

    await expect(mocks.routeGuard()).resolves.toBe(false);
    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    expect(control(wrapper, 'el-select', 0).props.modelValue).toBe('paper');
  });

  it('disables saving while a save request is in flight and preserves other sections after failure', async () => {
    const save = deferred<AppSettingsView>();
    mocks.updateSettings.mockReturnValueOnce(save.promise);
    const wrapper = await mount(Settings, { components });
    await wrapper.flush();

    await wrapper.invoke(control(wrapper, 'el-select', 0), 'onUpdate:modelValue', 'live');
    await wrapper.invoke(control(wrapper, 'el-input-number', 0), 'onUpdate:modelValue', 250000);
    void (control(wrapper, 'el-button', 1).props.onClick as () => Promise<void>)();
    await wrapper.flush();

    expect(mocks.updateSettings).toHaveBeenCalledTimes(1);
    expect(control(wrapper, 'el-button', 1).props.loading).toBe(true);
    expect(control(wrapper, 'el-button', 1).props.disabled).toBe(true);
    expect(control(wrapper, 'el-form').props.disabled).toBe(true);
    expect(control(wrapper, 'el-input', 0).props.disabled).toBe(true);

    save.reject(new Error('save failed'));
    await wrapper.flush();

    expect(mocks.error).toHaveBeenCalledWith('settings.saveError');
    expect(control(wrapper, 'el-select', 0).props.modelValue).toBe('live');
    expect(control(wrapper, 'el-input-number', 0).props.modelValue).toBe(250000);
  });

  it('shows stale data after a reload failure and refreshes successfully on retry', async () => {
    const wrapper = await mount(Settings, { components });
    await wrapper.flush();

    const reload = control(wrapper, 'el-button', 0);
    mocks.getSettings.mockRejectedValueOnce(new Error('unavailable'));
    await wrapper.trigger(reload, 'click');

    expect(mocks.confirm).toHaveBeenCalledTimes(0);
    expect(mocks.error).toHaveBeenCalledWith('settings.loadError');
    expect(wrapper.text()).toContain('common.stale');
    expect(textNodes(wrapper, 'h3')).toHaveLength(6);

    mocks.getSettings.mockResolvedValueOnce(createSettingsView({ mode: 'live' }));
    const retryButton = wrapper.find((node) => node.props.class === 'data-state__retry');
    await wrapper.trigger(retryButton, 'click');

    expect(mocks.getSettings).toHaveBeenCalledTimes(3);
    expect(control(wrapper, 'el-select', 0).props.modelValue).toBe('live');
    expect(wrapper.text()).not.toContain('common.stale');
  });

  it('ignores stale reload responses while a newer settings load is pending', async () => {
    const initialLoad = deferred<AppSettingsView>();
    const reloadLoad = deferred<AppSettingsView>();
    mocks.getSettings
      .mockImplementationOnce(() => initialLoad.promise)
      .mockImplementationOnce(() => reloadLoad.promise);

    const wrapper = await mount(Settings, { components });
    await wrapper.flush();

    expect(control(wrapper, 'el-button', 0).props.loading).toBe(true);

    void (control(wrapper, 'el-button', 0).props.onClick as () => Promise<void>)();
    await wrapper.flush();
    expect(control(wrapper, 'el-button', 0).props.loading).toBe(true);

    initialLoad.resolve(createSettingsView({ mode: 'paper' }));
    await wrapper.flush();

    expect(control(wrapper, 'el-button', 0).props.loading).toBe(true);
    expect(wrapper.findAll((node) => node.type === 'el-select')).toHaveLength(0);
    expect(mocks.error).not.toHaveBeenCalled();

    reloadLoad.resolve(createSettingsView({ mode: 'live' }));
    await wrapper.flush();

    expect(control(wrapper, 'el-button', 0).props.loading).toBe(false);
    expect(control(wrapper, 'el-select', 0).props.modelValue).toBe('live');
  });

  it.each([
    {
      name: 'rejects an invalid runtime mode',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-select', 0), 'onUpdate:modelValue', 'invalid');
      },
      expected: validationText('settings.validation.requiredField', 'settings.mode'),
    },
    {
      name: 'rejects an invalid exchange market type',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-select', 1), 'onUpdate:modelValue', 'invalid');
      },
      expected: validationText('settings.validation.requiredField', 'settings.marketType'),
    },
    {
      name: 'rejects a negative initial capital',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-input-number', 0), 'onUpdate:modelValue', -1);
      },
      expected: validationText('settings.validation.nonNegative', 'settings.initialCapital'),
    },
    {
      name: 'rejects a negative fee rate',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-input-number', 1), 'onUpdate:modelValue', -0.001);
      },
      expected: validationText('settings.validation.nonNegative', 'settings.feeRate'),
    },
    {
      name: 'rejects a negative slippage',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-input-number', 2), 'onUpdate:modelValue', -0.001);
      },
      expected: validationText('settings.validation.nonNegative', 'settings.slippage'),
    },
    {
      name: 'rejects a daily loss percentage above one',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-input-number', 3), 'onUpdate:modelValue', 1.1);
      },
      expected: validationText('settings.validation.percentageRange', 'settings.maxDailyLossPct'),
    },
    {
      name: 'rejects a drawdown percentage below zero',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-input-number', 4), 'onUpdate:modelValue', -0.1);
      },
      expected: validationText('settings.validation.percentageRange', 'settings.maxDrawdownPct'),
    },
    {
      name: 'rejects a total position percentage above one',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-input-number', 5), 'onUpdate:modelValue', 1.1);
      },
      expected: validationText('settings.validation.percentageRange', 'settings.maxTotalPositionPct'),
    },
    {
      name: 'rejects a negative live order notional',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-input-number', 6), 'onUpdate:modelValue', -1);
      },
      expected: validationText('settings.validation.nonNegative', 'settings.liveMaxOrderNotional'),
    },
    {
      name: 'rejects an out-of-range web port',
      mutate: async (wrapper: Awaited<ReturnType<typeof mount>>) => {
        await wrapper.invoke(control(wrapper, 'el-input-number', 7), 'onUpdate:modelValue', 70000);
      },
      expected: validationText('settings.validation.portRange', 'settings.port'),
    },
  ])('$name', async ({ mutate, expected }) => {
    const wrapper = await mount(Settings, { components });
    await wrapper.flush();

    await mutate(wrapper);
    await wrapper.flush();

    expect(wrapper.text()).toContain(expected);
    expect(textNodes(wrapper, 'h3')).toHaveLength(6);
    expect(control(wrapper, 'el-button', 1).props.disabled).toBe(true);

    await wrapper.trigger(control(wrapper, 'el-button', 1), 'click');
    expect(mocks.updateSettings).not.toHaveBeenCalled();
  });

  it('keeps other section errors visible while fixing a field and then saves', async () => {
    const wrapper = await mount(Settings, { components });
    await wrapper.flush();

    await wrapper.invoke(control(wrapper, 'el-select', 0), 'onUpdate:modelValue', 'invalid');
    await wrapper.invoke(control(wrapper, 'el-input-number', 7), 'onUpdate:modelValue', 70000);
    await wrapper.flush();

    const runtimeError = validationText('settings.validation.requiredField', 'settings.mode');
    const portError = validationText('settings.validation.portRange', 'settings.port');
    expect(wrapper.text()).toContain(runtimeError);
    expect(wrapper.text()).toContain(portError);
    expect(textNodes(wrapper, 'h3')).toHaveLength(6);
    expect(control(wrapper, 'el-button', 1).props.disabled).toBe(true);

    await wrapper.invoke(control(wrapper, 'el-select', 0), 'onUpdate:modelValue', 'backtest');
    await wrapper.flush();
    expect(wrapper.text()).not.toContain(runtimeError);
    expect(wrapper.text()).toContain(portError);

    await wrapper.invoke(control(wrapper, 'el-input-number', 7), 'onUpdate:modelValue', 8081);
    await wrapper.flush();
    expect(wrapper.text()).not.toContain(portError);
    expect(control(wrapper, 'el-button', 1).props.disabled).toBe(false);

    await wrapper.trigger(control(wrapper, 'el-button', 1), 'click');
    expect(mocks.updateSettings).toHaveBeenCalledTimes(1);
    expect(mocks.updateSettings).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'backtest',
      web: { host: '0.0.0.0', port: 8081 },
    }));
  });

  it('contains localized validation copy in both locales', () => {
    for (const source of [enLocaleSource, zhLocaleSource]) {
      expect(source).toContain('validation: {');
      expect(source).toContain('requiredField:');
      expect(source).toContain('nonNegative:');
      expect(source).toContain('percentageRange:');
      expect(source).toContain('portRange:');
    }
    expect(enLocaleSource).toContain("requiredField: '{field} is required'");
    expect(enLocaleSource).toContain("nonNegative: '{field} must be greater than or equal to 0'");
    expect(enLocaleSource).toContain("percentageRange: '{field} must be between 0 and 1'");
    expect(enLocaleSource).toContain("portRange: '{field} must be an integer between 1 and 65535'");
    expect(zhLocaleSource).toContain("requiredField: '{field} 为必填项'");
    expect(zhLocaleSource).toContain("nonNegative: '{field} 必须大于或等于 0'");
    expect(zhLocaleSource).toContain("percentageRange: '{field} 必须在 0 和 1 之间'");
    expect(zhLocaleSource).toContain("portRange: '{field} 必须是 1 到 65535 之间的整数'");
  });
});
