import { defineComponent, h, reactive, type Component } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent, type TestHostNode } from '@/test-utils/mount';
import type { StrategyConfig, StrategyConfigPayload, StrategyValidationIssue } from '@/types/strategy';
import Strategy from './Strategy.vue';

const mocks = vi.hoisted(() => ({
  confirm: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  validateConfig: vi.fn(),
  validateYaml: vi.fn(),
  routeGuard: null as null | (() => Promise<boolean>),
}));

let store: Record<string, any>;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error },
  ElMessageBox: { confirm: mocks.confirm },
}));

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => params?.name ? `${key}:${params.name}` : key,
  }),
}));

vi.mock('vue-router', () => ({
  onBeforeRouteLeave: (guard: () => Promise<boolean>) => { mocks.routeGuard = guard; },
}));

vi.mock('@/services/strategies', () => ({
  validateStrategyConfig: mocks.validateConfig,
  validateStrategyConfigYaml: mocks.validateYaml,
}));

vi.mock('@/stores/strategies', () => ({
  useStrategiesStore: () => store,
}));

vi.mock('@/components/StrategyForm.vue', async () => {
  const { defineComponent, h } = await import('vue');
  return {
    default: defineComponent({
      name: 'StrategyForm',
      inheritAttrs: false,
      props: ['modelValue', 'mode', 'issues', 'readonly', 'dirty', 'definitions'],
      emits: ['update:modelValue'],
      setup(props, { attrs, emit }) {
        return () => h('strategy-form-stub', {
          ...attrs,
          ...props,
          'onUpdate:modelValue': (value: StrategyConfigPayload) => emit('update:modelValue', value),
        });
      },
    }),
  };
});

vi.mock('@/components/editor/CodeEditor.vue', async () => {
  const { defineComponent, h } = await import('vue');
  return {
    default: defineComponent({
      name: 'CodeEditor',
      inheritAttrs: false,
      props: ['modelValue', 'modelUri', 'label', 'description', 'issues', 'readonly'],
      emits: ['update:modelValue'],
      setup(props, { attrs }) {
        return () => h('code-editor-stub', { ...attrs, ...props });
      },
    }),
  };
});

const stopped: StrategyConfig = {
  name: 'desk:btc',
  strategy_type: 'ma_cross',
  symbol: 'BTC-USDT-SWAP',
  timeframe: '5m',
  enabled: true,
  params: { fast_window: 10, slow_window: 30 },
  created_at: 1,
  updated_at: 2,
};

const running: StrategyConfig = {
  ...stopped,
  name: 'desk:eth',
  symbol: 'ETH-USDT-SWAP',
};

const definition = {
  strategy_type: 'ma_cross',
  label: 'MA Cross',
  description: 'Cross',
  params: [],
};

const tableRows = reactive<{ value: StrategyConfig[] }>({ value: [] });

const ElTable = defineComponent({
  name: 'ElTable',
  props: { data: { type: Array, default: () => [] } },
  setup(props, { slots, attrs }) {
    return () => {
      tableRows.value = props.data as StrategyConfig[];
      return h('el-table', attrs, slots.default?.());
    };
  },
});

const ElTableColumn = defineComponent({
  name: 'ElTableColumn',
  setup(_props, { slots, attrs }) {
    return () => h('el-table-column', attrs, slots.default
      ? tableRows.value.map((row) => slots.default?.({ row }))
      : undefined);
  },
});

const ElCard = defineComponent({
  name: 'ElCard',
  setup(_props, { slots, attrs }) {
    return () => h('el-card', attrs, [slots.header?.(), slots.default?.()]);
  },
});

const components: Record<string, Component> = {
  ElTable,
  ElTableColumn,
  ElCard,
  ElButton: defineHostComponent('el-button'),
  ElAlert: defineHostComponent('el-alert'),
  ElTag: defineHostComponent('el-tag'),
  ElEmpty: defineHostComponent('el-empty'),
};

function createStore() {
  return reactive({
    configs: [stopped, running],
    definitions: [definition],
    statuses: {
      'desk:btc': { name: 'desk:btc', status: 'mystery' },
      'desk:eth': { name: 'desk:eth', status: 'running' },
    },
    errors: {},
    loadingInitial: false,
    error: '',
    reconciliationError: '',
    loadInitialData: vi.fn().mockResolvedValue(undefined),
    createConfig: vi.fn(async (payload: StrategyConfigPayload) => ({ ...payload, created_at: 3, updated_at: 3 })),
    updateConfig: vi.fn(async (_name: string, payload: StrategyConfigPayload) => ({ ...payload, created_at: 1, updated_at: 3 })),
    cloneConfig: vi.fn(async (_name: string, request: any) => ({ ...stopped, name: request.target_name, enabled: false })),
    deleteConfig: vi.fn().mockResolvedValue(undefined),
    start: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    mutationError: vi.fn(() => undefined),
    isMutationLoading: vi.fn((name: string, action: string) => name === 'desk:btc' && action === 'delete'),
    isActionLoading: vi.fn((name: string, action: string) => name === 'desk:eth' && action === 'stop'),
  });
}

function buttons(wrapper: Awaited<ReturnType<typeof mount>>, label: string): TestHostNode[] {
  return wrapper.findAll((node) => node.type === 'el-button' && node.props['aria-label'] === label);
}

function card(wrapper: Awaited<ReturnType<typeof mount>>, name: string): TestHostNode {
  return wrapper.find((node) => node.type === 'article' && textContent(node).includes(name));
}

async function mountPage() {
  const wrapper = await mount(Strategy, { components });
  await wrapper.flush();
  return wrapper;
}

describe('Strategy page', () => {
  beforeEach(() => {
    store = createStore();
    mocks.confirm.mockReset();
    mocks.confirm.mockResolvedValue(undefined);
    mocks.success.mockReset();
    mocks.error.mockReset();
    mocks.validateConfig.mockReset();
    mocks.validateYaml.mockReset();
    mocks.validateConfig.mockImplementation(async (payload: StrategyConfigPayload) => ({ config: payload, yaml: 'name: yaml' }));
    mocks.validateYaml.mockImplementation(async () => ({ config: stopped, yaml: 'name: canonical' }));
    mocks.routeGuard = null;
  });

  it('mounts both list branches with localized instance action names and unknown status fallback', async () => {
    const wrapper = await mountPage();

    expect(store.loadInitialData).toHaveBeenCalledTimes(1);
    expect(wrapper.getByTestId('strategy-desktop-table')).toBeTruthy();
    expect(wrapper.getByTestId('strategy-mobile-cards')).toBeTruthy();
    expect(wrapper.text()).toContain('strategies.status.unknown');
    expect(wrapper.text()).not.toContain('strategies.status.mystery');

    for (const action of ['edit', 'clone', 'delete', 'start', 'stop']) {
      expect(buttons(wrapper, `strategies.actions.${action}:desk:btc`)).toHaveLength(2);
    }
    expect(card(wrapper, 'desk:btc').props).not.toHaveProperty('role');
    expect(card(wrapper, 'desk:btc').props).not.toHaveProperty('tabindex');
    expect(card(wrapper, 'desk:btc').props).not.toHaveProperty('aria-pressed');
    const selectors = buttons(wrapper, 'strategies.actions.select:desk:btc');
    expect(selectors).toHaveLength(1);
    expect(selectors[0].props['aria-pressed']).toBe('false');
  });

  it('runs create, edit, clone, delete, start, and stop interactions with per-action loading', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();

    const create = wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'strategies.create');
    await wrapper.trigger(create, 'click');
    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.mode).toBe('create');

    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:btc')[0], 'click');
    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.mode).toBe('edit');

    await wrapper.trigger(buttons(wrapper, 'strategies.actions.clone:desk:btc')[0], 'click');
    const cloneForm = wrapper.find((node) => node.type === 'strategy-form-stub');
    expect(cloneForm.props).toMatchObject({ mode: 'clone', modelValue: expect.objectContaining({ name: '', enabled: false }) });

    expect(buttons(wrapper, 'strategies.actions.delete:desk:btc')[0].props.loading).toBe(true);
    expect(buttons(wrapper, 'strategies.actions.stop:desk:eth')[0].props.loading).toBe(true);
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.delete:desk:btc')[0], 'click');
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.start:desk:btc')[0], 'click');
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.stop:desk:eth')[0], 'click');
    expect(store.deleteConfig).toHaveBeenCalledWith('desk:btc');
    expect(store.start).toHaveBeenCalledWith('desk:btc');
    expect(store.stop).toHaveBeenCalledWith('desk:eth');
  });

  it('selects mobile cards through a dedicated native button and marks the selected instance', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();
    const selectedCard = card(wrapper, 'desk:btc');
    const selector = buttons(wrapper, 'strategies.actions.select:desk:btc')[0];

    expect(selectedCard.props).not.toHaveProperty('onClick');
    expect(selectedCard.props).not.toHaveProperty('onKeydown');
    expect(selector.type).toBe('el-button');
    await wrapper.trigger(selector, 'click');
    expect(buttons(wrapper, 'strategies.actions.select:desk:btc')[0].props['aria-pressed']).toBe('true');
    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.mode).toBe('edit');

    await wrapper.trigger(buttons(wrapper, 'strategies.actions.select:desk:eth')[0], 'click');
    expect(buttons(wrapper, 'strategies.actions.select:desk:eth')[0].props['aria-pressed']).toBe('true');
  });

  it('keeps card actions instance-specific and honors the dirty discard guard', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.select:desk:btc')[0], 'click');
    const form = wrapper.find((node) => node.type === 'strategy-form-stub');
    await wrapper.invoke(form, 'onUpdate:modelValue', { ...stopped, symbol: 'CHANGED' });

    mocks.confirm.mockRejectedValueOnce('cancel');
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.select:desk:eth')[0], 'click');
    expect(buttons(wrapper, 'strategies.actions.select:desk:btc')[0].props['aria-pressed']).toBe('true');

    for (const action of ['edit', 'clone', 'delete', 'start', 'stop']) {
      expect(buttons(wrapper, `strategies.actions.${action}:desk:btc`)).toHaveLength(2);
    }
    mocks.confirm.mockRejectedValueOnce('cancel');
    expect(await mocks.routeGuard?.()).toBe(false);
  });

  it('opens persisted config edits without marking runtime fields dirty', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();

    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:btc')[0], 'click');

    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.dirty).toBe(false);
  });

  it('omits persisted runtime fields when validating an existing config for advanced editing', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();

    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:btc')[0], 'click');
    const advanced = wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'strategies.editor.advanced');
    await wrapper.trigger(advanced, 'click');
    await wrapper.flush();

    expect(mocks.validateConfig).toHaveBeenCalledTimes(1);
    const [payload, expectedName] = mocks.validateConfig.mock.calls[0];
    expect(expectedName).toBe('desk:btc');
    expect(payload).toEqual({
      name: 'desk:btc',
      strategy_type: 'ma_cross',
      symbol: 'BTC-USDT-SWAP',
      timeframe: '5m',
      enabled: true,
      params: { fast_window: 10, slow_window: 30 },
    });
    expect(payload).not.toHaveProperty('created_at');
    expect(payload).not.toHaveProperty('updated_at');
  });

  it('keeps active edits readonly and separates positioned YAML markers from external diagnostics', async () => {
    const wrapper = await mountPage();
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.select:desk:eth')[0], 'click');
    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.readonly).toBe(true);

    const advanced = wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'strategies.editor.advanced');
    await wrapper.trigger(advanced, 'click');
    const positioned: StrategyValidationIssue = { path: 'symbol', code: 'bad', message: 'Marker only', line: 2, column: 3 };
    const external: StrategyValidationIssue = { path: 'name', code: 'bad', message: 'External only', line: null, column: null };
    mocks.validateYaml.mockRejectedValueOnce({ response: { data: { detail: { issues: [positioned, external] } } } });
    const apply = wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'strategies.editor.applyYaml');
    await wrapper.trigger(apply, 'click');
    await wrapper.flush();

    const editor = wrapper.find((node) => node.type === 'code-editor-stub');
    expect(editor.props.label).toBe('strategies.editor.yamlLabel');
    expect(editor.props.issues).toEqual([positioned, external]);
    const diagnosticTitles = wrapper.findAll((node) => node.type === 'el-alert').map((node) => node.props.title);
    expect(diagnosticTitles).toContain('External only');
    expect(diagnosticTitles).not.toContain('Marker only');
  });

  it('ignores advanced validation from A after switching the editor to B', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    store.statuses['desk:eth'].status = 'stopped';
    const validation = deferred<{ config: StrategyConfigPayload; yaml: string }>();
    mocks.validateConfig.mockReturnValueOnce(validation.promise);
    const wrapper = await mountPage();

    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:btc')[0], 'click');
    const pending = wrapper.trigger(
      wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'strategies.editor.advanced'),
      'click',
    );
    await wrapper.flush();
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:eth')[0], 'click');

    validation.resolve({ config: stopped, yaml: 'name: stale-a' });
    await pending;
    await wrapper.flush();

    const form = wrapper.find((node) => node.type === 'strategy-form-stub');
    expect(form.props.modelValue).toMatchObject({ name: 'desk:eth', symbol: 'ETH-USDT-SWAP' });
    expect(wrapper.findAll((node) => node.type === 'code-editor-stub')).toHaveLength(0);
    expect(mocks.success).not.toHaveBeenCalled();
    expect(mocks.error).not.toHaveBeenCalled();
  });

  it('keeps the editor closed when pending validation resolves', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const validation = deferred<{ config: StrategyConfigPayload; yaml: string }>();
    mocks.validateConfig.mockReturnValueOnce(validation.promise);
    const wrapper = await mountPage();

    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:btc')[0], 'click');
    const pending = wrapper.trigger(
      wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'strategies.editor.advanced'),
      'click',
    );
    await wrapper.flush();
    await wrapper.trigger(
      wrapper.find((node) => node.type === 'el-button' && node.props['aria-label'] === 'common.close'),
      'click',
    );

    validation.resolve({ config: stopped, yaml: 'name: stale-a' });
    await pending;
    await wrapper.flush();

    expect(wrapper.findAll((node) => node.type === 'strategy-form-stub')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.type === 'code-editor-stub')).toHaveLength(0);
    expect(mocks.error).not.toHaveBeenCalled();
  });

  it('does not send a save mutation when validation becomes stale after switching targets', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    store.statuses['desk:eth'].status = 'stopped';
    const validation = deferred<{ config: StrategyConfigPayload; yaml: string }>();
    mocks.validateConfig.mockReturnValueOnce(validation.promise);
    const wrapper = await mountPage();

    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:btc')[0], 'click');
    const pending = wrapper.trigger(
      wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'common.save'),
      'click',
    );
    await wrapper.flush();
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:eth')[0], 'click');

    validation.resolve({ config: stopped, yaml: 'name: desk:btc' });
    await pending;
    await wrapper.flush();

    expect(store.updateConfig).not.toHaveBeenCalled();
    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.modelValue).toMatchObject({ name: 'desk:eth' });
  });

  it('uses captured clone source and target while a completed mutation cannot overwrite a newer editor', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    store.statuses['desk:eth'].status = 'stopped';
    const mutation = deferred<StrategyConfig>();
    store.cloneConfig.mockReturnValueOnce(mutation.promise);
    const wrapper = await mountPage();

    await wrapper.trigger(buttons(wrapper, 'strategies.actions.clone:desk:btc')[0], 'click');
    const cloneForm = wrapper.find((node) => node.type === 'strategy-form-stub');
    const cloneDraft = { ...cloneForm.props.modelValue as StrategyConfigPayload, name: 'desk:btc-copy' };
    await wrapper.invoke(cloneForm, 'onUpdate:modelValue', cloneDraft);
    const pending = wrapper.trigger(
      wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'common.save'),
      'click',
    );
    await wrapper.flush();

    expect(store.cloneConfig).toHaveBeenCalledWith('desk:btc', expect.objectContaining({ target_name: 'desk:btc-copy' }));
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.edit:desk:eth')[0], 'click');
    mutation.resolve({ ...stopped, ...cloneDraft, created_at: 3, updated_at: 3 });
    await pending;
    await wrapper.flush();

    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.modelValue).toMatchObject({ name: 'desk:eth' });
    expect(mocks.success).not.toHaveBeenCalledWith('strategies.saved:desk:btc-copy');
  });

  it('renders reconciliation separately and reports mutation errors through messages', async () => {
    store.error = 'load failed';
    store.reconciliationError = 'refresh failed';
    store.statuses['desk:btc'].status = 'stopped';
    store.start.mockRejectedValueOnce(new Error('start failed'));
    store.mutationError.mockReturnValueOnce('backend start failure');
    const wrapper = await mountPage();

    const alerts = wrapper.findAll((node) => node.type === 'el-alert');
    expect(alerts.map((node) => node.props.type)).toEqual(expect.arrayContaining(['error', 'warning']));
    await wrapper.trigger(buttons(wrapper, 'strategies.actions.start:desk:btc')[0], 'click');
    expect(mocks.error).toHaveBeenCalledWith('backend start failure');
  });
});
