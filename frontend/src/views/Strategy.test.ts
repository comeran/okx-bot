import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

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

const strategySource = readFileSync(fileURLToPath(new URL('./Strategy.vue', import.meta.url)), 'utf8');
const strategyEditorPanelSource = readFileSync(fileURLToPath(new URL('../components/strategy/StrategyEditorPanel.vue', import.meta.url)), 'utf8');
const codeEditorSource = readFileSync(fileURLToPath(new URL('../components/editor/CodeEditor.vue', import.meta.url)), 'utf8');

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
  ElButton: defineHostComponent('el-button'),
  ElAlert: defineHostComponent('el-alert'),
  ElCard: defineHostComponent('el-card'),
  ElEmpty: defineHostComponent('el-empty'),
  ElTag: defineHostComponent('el-tag'),
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

vi.mock('@/components/strategy/StrategyList.vue', async () => {
  const { defineComponent, h } = await import('vue');
  return {
    default: defineComponent({
      name: 'StrategyList',
      inheritAttrs: false,
      props: ['title', 'description', 'rows', 'loading', 'emptyDescription', 'onSelect', 'onEdit', 'onClone', 'onDelete', 'onStart', 'onStop'],
      setup(props, { attrs }) {
        return () => h('strategy-list-stub', [
          h('div', { ...attrs, 'data-testid': 'strategy-desktop-table' }, (props.rows as Array<Record<string, any>>).map((row) => h('div', {
            key: row.name,
            class: 'strategy-row',
            'aria-selected': row.selected ? 'true' : 'false',
            onClick: () => props.onSelect?.(row),
          }, [
            h('span', row.name),
            h('span', row.statusLabel),
            h('el-button', { 'aria-label': row.actionLabels.edit, onClick: () => props.onEdit?.(row) }, row.actionLabels.edit),
            h('el-button', { 'aria-label': row.actionLabels.clone, onClick: () => props.onClone?.(row) }, row.actionLabels.clone),
            row.canDelete ? h('el-button', { 'aria-label': row.actionLabels.delete, loading: row.isDeleting, onClick: () => props.onDelete?.(row) }, row.actionLabels.delete) : null,
            row.canStart ? h('el-button', { 'aria-label': row.actionLabels.start, loading: row.isStarting, onClick: () => props.onStart?.(row) }, row.actionLabels.start) : null,
            row.canStop ? h('el-button', { 'aria-label': row.actionLabels.stop, loading: row.isStopping, onClick: () => props.onStop?.(row) }, row.actionLabels.stop) : null,
          ]))),
          h('div', { ...attrs, 'data-testid': 'strategy-mobile-cards' }, (props.rows as Array<Record<string, any>>).map((row) => h('article', {
            key: `${row.name}-mobile`,
            class: 'strategy-card',
          }, [
            h('el-button', {
              'aria-label': row.actionLabels.select,
              'aria-pressed': row.selected ? 'true' : 'false',
              onClick: () => props.onSelect?.(row),
            }, row.name),
            h('span', row.statusLabel),
            h('span', row.runtimeError ?? ''),
            h('el-button', { 'aria-label': row.actionLabels.edit, onClick: () => props.onEdit?.(row) }, row.actionLabels.edit),
            h('el-button', { 'aria-label': row.actionLabels.clone, onClick: () => props.onClone?.(row) }, row.actionLabels.clone),
            row.canDelete ? h('el-button', { 'aria-label': row.actionLabels.delete, loading: row.isDeleting, onClick: () => props.onDelete?.(row) }, row.actionLabels.delete) : null,
            row.canStart ? h('el-button', { 'aria-label': row.actionLabels.start, loading: row.isStarting, onClick: () => props.onStart?.(row) }, row.actionLabels.start) : null,
            row.canStop ? h('el-button', { 'aria-label': row.actionLabels.stop, loading: row.isStopping, onClick: () => props.onStop?.(row) }, row.actionLabels.stop) : null,
          ]))),
        ]);
      },
    }),
  };
});

vi.mock('@/components/strategy/StrategyEditorPanel.vue', async () => {
  const { defineComponent, h } = await import('vue');
  return {
    default: defineComponent({
      name: 'StrategyEditorPanel',
      inheritAttrs: false,
      props: ['modelValue', 'yaml', 'title', 'mode', 'definitions', 'advanced', 'readonly', 'dirty', 'busy', 'saveLoading', 'modelUri', 'validationSummary', 'issues', 'selectedName', 'cloneSourceName'],
      emits: ['update:modelValue', 'update:yaml', 'close', 'cancel', 'save', 'enterAdvanced', 'leaveAdvanced'],
      setup(props, { attrs, emit }) {
        return () => h('strategy-editor-panel-stub', [
          h('div', { class: 'strategy-editor__header' }, [
            h('strong', String(props.title ?? '')),
            h('el-button', { 'aria-label': 'common.close', onClick: () => emit('close') }, 'common.close'),
          ]),
          ...(((props.validationSummary as string[] | undefined) ?? []).map((message) => h('el-alert', { title: message }))),
          !props.advanced
            ? h('strategy-form-stub', {
                ...attrs,
                modelValue: props.modelValue,
                mode: props.mode,
                issues: props.issues,
                readonly: props.readonly,
                dirty: props.dirty,
                definitions: props.definitions,
                'onUpdate:modelValue': (value: StrategyConfigPayload) => emit('update:modelValue', value),
              })
            : h('code-editor-stub', {
                ...attrs,
                modelValue: props.yaml,
                modelUri: props.modelUri,
                label: 'strategies.editor.yamlLabel',
                description: 'strategies.editor.yamlDescription',
                issues: props.issues,
                readonly: props.readonly,
                'onUpdate:modelValue': (value: string) => emit('update:yaml', value),
              }),
          h('div', { class: 'strategy-editor__toggle' }, [
            h('el-button', { 'aria-pressed': props.advanced ? 'false' : 'true', onClick: () => emit('leaveAdvanced') }, 'strategies.editor.structured'),
            h('el-button', { 'aria-pressed': props.advanced ? 'true' : 'false', onClick: () => emit('enterAdvanced') }, 'strategies.editor.advanced'),
          ]),
          h('el-button', { loading: props.saveLoading, onClick: () => emit('save') }, 'common.save'),
          h('el-button', { onClick: () => emit('cancel') }, 'common.cancel'),
        ]);
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
  return wrapper.findAll((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === label);
}

function selectButton(wrapper: Awaited<ReturnType<typeof mount>>, label: string): TestHostNode {
  return wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === label);
}

function card(wrapper: Awaited<ReturnType<typeof mount>>, name: string): TestHostNode {
  return wrapper.find((node) => node.type === 'article' && String(node.props.class).includes('strategy-list__card') && textContent(node).includes(name));
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

  it('uses a single-column content layout until the editor is open', () => {
    expect(strategySource).toContain('strategy-page__content--editor-open');
    expect(strategySource).toContain(':class="{ \'strategy-page__content--editor-open\': editorOpen }"');
    expect(strategySource).toMatch(/\.strategy-page__content \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\);[\s\S]*?\}/);
    expect(strategySource).toMatch(/\.strategy-page__content > \* \{[\s\S]*?min-width: 0;[\s\S]*?width: 100%;[\s\S]*?\}/);
    expect(strategySource).toMatch(/\.strategy-page__content--editor-open \{[\s\S]*?grid-template-columns: minmax\(0, 1.35fr\) minmax\(0, 1fr\);[\s\S]*?\}/);
    expect(strategySource).toMatch(/@media \(max-width: 1023px\)[\s\S]*?\.strategy-page__content \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\);[\s\S]*?\}/);
    expect(strategySource).not.toContain('@media (max-width: 900px)');
    expect(strategyEditorPanelSource).toMatch(/@media \(max-width: 767px\)[\s\S]*?\.strategy-editor-panel__actions \{[\s\S]*?position: sticky;[\s\S]*?bottom: 0;[\s\S]*?\}/);
    expect(codeEditorSource).toMatch(/@media \(max-width: 767px\)[\s\S]*?\.code-editor__surface \{[\s\S]*?min-height: 360px;[\s\S]*?\}/);
  });

  it('mounts both list branches with localized instance action names and unknown status fallback', async () => {
    const wrapper = await mountPage();

    expect(store.loadInitialData).toHaveBeenCalledTimes(1);
    expect(wrapper.getByTestId('strategy-desktop-table')).toBeTruthy();
    expect(wrapper.getByTestId('strategy-mobile-cards')).toBeTruthy();
    expect(wrapper.text()).toContain('strategies.status.unknown');
    expect(wrapper.text()).not.toContain('strategies.status.mystery');

    expect(wrapper.text()).toContain('desk:btc');
    expect(wrapper.text()).toContain('desk:eth');
    expect(selectButton(wrapper, 'strategies.actions.select:desk:btc').props['aria-pressed']).toBe('false');
    expect(selectButton(wrapper, 'strategies.actions.select:desk:eth').props['aria-pressed']).toBe('false');
  });

  it('runs create, edit, clone, delete, start, and stop interactions with per-action loading', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();

    const create = wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'strategies.create');
    await wrapper.trigger(create, 'click');
    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.mode).toBe('create');

    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:btc'), 'click');
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
    const selector = selectButton(wrapper, 'strategies.actions.select:desk:btc');

    expect(selector.props['aria-pressed']).toBe('false');
    await wrapper.trigger(selector, 'click');
    expect(selectButton(wrapper, 'strategies.actions.select:desk:btc').props['aria-pressed']).toBe('true');
    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.mode).toBe('edit');

    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:eth'), 'click');
    expect(selectButton(wrapper, 'strategies.actions.select:desk:eth').props['aria-pressed']).toBe('true');
  });

  it('keeps card actions instance-specific and honors the dirty discard guard', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();
    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:btc'), 'click');
    const form = wrapper.find((node) => node.type === 'strategy-form-stub');
    await wrapper.invoke(form, 'onUpdate:modelValue', { ...stopped, symbol: 'CHANGED' });

    mocks.confirm.mockRejectedValueOnce('cancel');
    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:eth'), 'click');
    expect(selectButton(wrapper, 'strategies.actions.select:desk:btc').props['aria-pressed']).toBe('true');
    mocks.confirm.mockRejectedValueOnce('cancel');
    expect(await mocks.routeGuard?.()).toBe(false);
  });

  it('blocks dirty editor close through the shared discard guard', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();

    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:btc'), 'click');
    const form = wrapper.find((node) => node.type === 'strategy-form-stub');
    await wrapper.invoke(form, 'onUpdate:modelValue', { ...stopped, timeframe: '15m' });

    mocks.confirm.mockRejectedValueOnce('cancel');
    await wrapper.trigger(wrapper.find((node) => node.type === 'el-button' && node.props['aria-label'] === 'common.close'), 'click');

    expect(wrapper.find((node) => node.type === 'strategy-form-stub')).toBeTruthy();
    expect(mocks.confirm).toHaveBeenCalledTimes(1);
  });

  it('opens persisted config edits without marking runtime fields dirty', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();

    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:btc'), 'click');

    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.dirty).toBe(false);
  });

  it('omits persisted runtime fields when validating an existing config for advanced editing', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const wrapper = await mountPage();

    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:btc'), 'click');
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

  it('keeps active edits readonly', async () => {
    const wrapper = await mountPage();
    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:eth'), 'click');
    expect(wrapper.find((node) => node.type === 'strategy-form-stub').props.readonly).toBe(true);
  });

  it('ignores advanced validation from A after switching the editor to B', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    store.statuses['desk:eth'].status = 'stopped';
    const validation = deferred<{ config: StrategyConfigPayload; yaml: string }>();
    mocks.validateConfig.mockReturnValueOnce(validation.promise);
    const wrapper = await mountPage();

    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:btc'), 'click');
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

    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:btc'), 'click');
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

  it('invalidates in-flight save work when the clean editor unmounts', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    const validation = deferred<{ config: StrategyConfigPayload; yaml: string }>();
    mocks.validateConfig.mockReturnValueOnce(validation.promise);
    const wrapper = await mountPage();

    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:btc'), 'click');
    const pending = wrapper.trigger(
      wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'common.save'),
      'click',
    );
    await wrapper.flush();
    wrapper.unmount();

    validation.resolve({ config: stopped, yaml: 'name: desk:btc' });
    await pending;
    await wrapper.flush();

    expect(store.updateConfig).not.toHaveBeenCalled();
    expect(mocks.success).not.toHaveBeenCalled();
    expect(mocks.error).not.toHaveBeenCalled();
    expect(mocks.confirm).not.toHaveBeenCalled();
    expect(wrapper.findAll((node) => node.type === 'strategy-form-stub')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.type === 'code-editor-stub')).toHaveLength(0);
  });

  it('does not send a save mutation when validation becomes stale after switching targets', async () => {
    store.statuses['desk:btc'].status = 'stopped';
    store.statuses['desk:eth'].status = 'stopped';
    const validation = deferred<{ config: StrategyConfigPayload; yaml: string }>();
    mocks.validateConfig.mockReturnValueOnce(validation.promise);
    const wrapper = await mountPage();

    await wrapper.trigger(selectButton(wrapper, 'strategies.actions.select:desk:btc'), 'click');
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
