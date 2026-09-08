import { ElOption, ElSelect, ID_INJECTION_KEY, ZINDEX_INJECTION_KEY } from 'element-plus';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, nextTick, type Component, type ComponentInternalInstance, type VNode } from 'vue';

import { defineHostComponent, mount, textContent, type TestHostNode } from '@/test-utils/mount';
import type { StrategyConfigPayload, StrategyDefinition, StrategyValidationIssue } from '@/types/strategy';
import StrategyForm from './StrategyForm.vue';

const confirm = vi.hoisted(() => vi.fn());
const warning = vi.hoisted(() => vi.fn());
const fetchTickers = vi.hoisted(() => vi.fn());

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

vi.mock('element-plus', async (importOriginal) => ({
  ...await importOriginal<typeof import('element-plus')>(),
  ElMessage: { warning },
  ElMessageBox: { confirm },
}));

vi.mock('@/services/market', () => ({
  fetchTickers,
}));

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}));

const definitions: StrategyDefinition[] = [
  {
    strategy_type: 'ma_cross',
    label: 'MA Cross',
    description: 'Moving average crossover',
    params: [
      { key: 'fast_window', label: 'Fast window', description: 'Fast period', value_type: 'integer', required: true, default: 10, minimum: 1, maximum: 100, step: 1 },
      { key: 'use_ema', label: 'EMA', description: 'Use EMA', value_type: 'boolean', required: true, default: false, minimum: null, maximum: null },
    ],
  },
  {
    strategy_type: 'donchian',
    label: 'Donchian',
    description: 'Breakout',
    params: [
      { key: 'window', label: 'Window', description: 'Channel period', value_type: 'number', required: true, default: 20, minimum: 2, maximum: 200, step: 0.5 },
      { key: 'mode', label: 'Mode', description: 'Execution mode', value_type: 'string', required: true, default: 'close', minimum: null, maximum: null },
    ],
  },
  {
    strategy_type: 'rsi',
    label: 'RSI',
    description: 'Mean reversion',
    params: [
      { key: 'period', label: 'Period', description: 'RSI period', value_type: 'integer', required: true, default: 14, minimum: 2, maximum: 100, step: 1 },
    ],
  },
];

const components = Object.fromEntries([
  'ElForm',
  'ElRow',
  'ElCol',
  'ElFormItem',
  'ElInput',
  'ElSelect',
  'ElOption',
  'ElSwitch',
  'ElInputNumber',
].map((name) => [name, defineHostComponent(name.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`).slice(1))]));
const componentsWithRealSelects = {
  ...components,
  ElSelect,
  ElOption,
};

function draft(overrides: Partial<StrategyConfigPayload> = {}): StrategyConfigPayload {
  return {
    name: 'desk:btc',
    strategy_type: 'ma_cross',
    symbol: 'BTC-USDT-SWAP',
    timeframe: '5m',
    enabled: true,
    params: { fast_window: 10, use_ema: false },
    ...overrides,
  };
}

async function mountForm(options: {
  model?: StrategyConfigPayload;
  mode?: 'create' | 'edit' | 'clone';
  issues?: StrategyValidationIssue[];
  readonly?: boolean;
  dirty?: boolean;
  realSelects?: boolean;
  provide?: Record<string | symbol, unknown>;
} = {}) {
  let model = options.model ?? draft();
  const updates: StrategyConfigPayload[] = [];
  const wrapper = await mount(StrategyForm, {
    components: options.realSelects ? componentsWithRealSelects : components,
    provide: options.provide,
    props: {
      modelValue: model,
      definitions,
      mode: options.mode ?? 'create',
      issues: options.issues ?? [],
      readonly: options.readonly ?? false,
      dirty: options.dirty ?? false,
      'onUpdate:modelValue': (next: StrategyConfigPayload) => {
        model = next;
        updates.push(next);
        void wrapper.updateProps({ modelValue: next });
      },
    },
  });
  return { wrapper, updates, model: () => model };
}

function control(wrapper: Awaited<ReturnType<typeof mount>>, id: string): TestHostNode {
  return wrapper.getById(id);
}

type StrategyFormInstance = ComponentInternalInstance & { setupState: Record<string, unknown> };

async function mountInstrumentedForm(options: Parameters<typeof mountForm>[0] = {}) {
  const captured: { formInstance: StrategyFormInstance | null } = { formInstance: null };
  const InstrumentedStrategyForm = defineComponent({
    inheritAttrs: false,
    setup(_props, { attrs }) {
      return () => h(StrategyForm as Component, {
        ...attrs,
        onVnodeMounted: (vnode: VNode) => {
          captured.formInstance = vnode.component as StrategyFormInstance | null;
        },
      });
    },
  });

  let model = options.model ?? draft();
  const updates: StrategyConfigPayload[] = [];
  const wrapper = await mount(InstrumentedStrategyForm, {
    components: options.realSelects ? componentsWithRealSelects : components,
    provide: options.provide,
    props: {
      modelValue: model,
      definitions,
      mode: options.mode ?? 'create',
      issues: options.issues ?? [],
      readonly: options.readonly ?? false,
      dirty: options.dirty ?? false,
      'onUpdate:modelValue': (next: StrategyConfigPayload) => {
        model = next;
        updates.push(next);
        void wrapper.updateProps({ modelValue: next });
      },
    },
  });
  if (!captured.formInstance) throw new Error('StrategyForm component instance not captured');
  return { wrapper, updates, model: () => model, formInstance: captured.formInstance };
}

function referencedIds(node: TestHostNode, attribute: string): string[] {
  const value = node.props[attribute];
  return typeof value === 'string' ? value.split(/\s+/).filter(Boolean) : [];
}

function selectOptionValues(wrapper: Awaited<ReturnType<typeof mount>>, selectId: string): string[] {
  const select = control(wrapper, selectId);
  return wrapper.findAll((node) => node.type === 'el-option' && node.parent === select)
    .map((node) => String(node.props.value));
}

function firstDescendant(node: TestHostNode, predicate: (candidate: TestHostNode) => boolean): TestHostNode | undefined {
  for (const child of node.children) {
    if (predicate(child)) return child;
    const match = firstDescendant(child, predicate);
    if (match) return match;
  }
  return undefined;
}

function internalCombobox(wrapper: Awaited<ReturnType<typeof mount>>, selectId: string): TestHostNode {
  const select = control(wrapper, selectId);
  if (select.props.role === 'combobox') return select;
  const combobox = firstDescendant(select, (node) => node.props.role === 'combobox');
  if (!combobox) throw new Error(`Internal combobox for ${selectId} not found`);
  return combobox;
}

describe('StrategyForm', () => {
  beforeEach(() => {
    confirm.mockReset();
    confirm.mockResolvedValue(undefined);
    warning.mockReset();
    fetchTickers.mockReset();
    fetchTickers.mockImplementation((marketType: string) => Promise.resolve(
      marketType === 'spot'
        ? [{ symbol: 'BTC-USDT' }, { symbol: 'ETH-USDT' }]
        : [{ symbol: 'BTC-USDT-SWAP' }, { symbol: 'ETH-USDT-SWAP' }],
    ));
    const ElementConstructor = class {
      static [Symbol.hasInstance](value: unknown) {
        return Boolean(
          value
            && typeof value === 'object'
            && 'nodeName' in value
            && 'ownerDocument' in value,
        );
      }
    };
    const documentElement = { nodeName: 'HTML', ownerDocument: null };
    const body = { nodeName: 'BODY', ownerDocument: null };
    const document = {
      activeElement: null,
      body,
      documentElement,
      addEventListener: () => {},
      removeEventListener: () => {},
    };
    vi.stubGlobal('document', document);
    vi.stubGlobal('Element', ElementConstructor);
    vi.stubGlobal('HTMLElement', ElementConstructor);
    vi.stubGlobal('window', {
      document,
      Element: ElementConstructor,
      HTMLElement: ElementConstructor,
      addEventListener: () => {},
      removeEventListener: () => {},
      getComputedStyle: () => ({
        transitionDelay: '0s',
        transitionDuration: '0s',
        animationDelay: '0s',
        animationDuration: '0s',
      }),
    });
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      callback(0);
      return 0;
    });
    vi.stubGlobal('cancelAnimationFrame', () => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('mounts create-mode common and schema controls with numeric constraints', async () => {
    const { wrapper } = await mountForm({ mode: 'create' });

    expect(control(wrapper, 'strategy-name').props.readonly).toBe(false);
    expect(control(wrapper, 'strategy-strategy-type').props.modelValue).toBe('ma_cross');
    expect(control(wrapper, 'strategy-symbol').props.modelValue).toBe('BTC-USDT-SWAP');
    expect(control(wrapper, 'strategy-timeframe').props.modelValue).toBe('5m');
    expect(control(wrapper, 'strategy-enabled').props.modelValue).toBe(true);

    const number = control(wrapper, 'strategy-params-fast_window');
    expect(number.type).toBe('el-input-number');
    expect(number.props).toMatchObject({ min: 1, max: 100, step: 1, precision: 0 });
    expect(textContent(wrapper.getById('strategy-params-fast_window-description'))).toBe('Fast period');
    expect(control(wrapper, 'strategy-params-use_ema').type).toBe('el-switch');
  });

  it('renders symbol and timeframe as selectable controls', async () => {
    const { wrapper } = await mountForm();

    expect(control(wrapper, 'strategy-symbol').type).toBe('el-select');
    expect(control(wrapper, 'strategy-timeframe').type).toBe('el-select');
  });

  it('combines fetched spot and swap symbols with fallbacks and the current value', async () => {
    fetchTickers.mockImplementation((marketType: string) => Promise.resolve(
      marketType === 'spot'
        ? [{ symbol: 'XRP-USDT' }, { symbol: 'BTC-USDT' }]
        : [{ symbol: 'XRP-USDT-SWAP' }, { symbol: 'BTC-USDT-SWAP' }],
    ));

    const { wrapper } = await mountForm({ model: draft({ symbol: 'LEGACY-USDT-SWAP' }) });
    await wrapper.flush();

    expect(fetchTickers).toHaveBeenCalledWith('spot');
    expect(fetchTickers).toHaveBeenCalledWith('swap');
    expect(selectOptionValues(wrapper, 'strategy-symbol')).toEqual([
      'BTC-USDT',
      'ETH-USDT',
      'OKB-USDT',
      'SOL-USDT',
      'BTC-USDT-SWAP',
      'ETH-USDT-SWAP',
      'SOL-USDT-SWAP',
      'LEGACY-USDT-SWAP',
      'XRP-USDT',
      'XRP-USDT-SWAP',
    ]);
    expect(control(wrapper, 'strategy-symbol').props).toMatchObject({ filterable: true, 'allow-create': true, loading: false });
  });

  it('updates the model when symbol and timeframe selections change', async () => {
    const { wrapper, updates } = await mountForm();

    await wrapper.invoke(control(wrapper, 'strategy-symbol'), 'onUpdate:modelValue', 'ETH-USDT-SWAP');
    await wrapper.invoke(control(wrapper, 'strategy-timeframe'), 'onUpdate:modelValue', '1h');

    expect(updates.at(-1)).toMatchObject({ symbol: 'ETH-USDT-SWAP', timeframe: '1h' });
  });

  it('commits a custom exact symbol through the real Element Plus keyboard flow', async () => {
    const customSymbol = 'DOGE-USDT-SWAP';
    const { wrapper, model } = await mountForm({
      realSelects: true,
      provide: {
        [ID_INJECTION_KEY]: { prefix: 1024, current: 0 },
        [ZINDEX_INJECTION_KEY]: { current: 0 },
      },
    });
    await wrapper.flush();

    const symbolCombobox = internalCombobox(wrapper, 'strategy-symbol');
    await wrapper.trigger(symbolCombobox, 'input', { target: { value: customSymbol } });
    await wrapper.flush();
    await wrapper.flush();
    await wrapper.flush();
    await wrapper.trigger(symbolCombobox, 'keydown', { key: 'Enter', code: 'Enter' });
    await wrapper.flush();

    expect(model().symbol).toBe(customSymbol);
  });

  it('commits a custom exact timeframe through the real Element Plus keyboard flow', async () => {
    const customTimeframe = '2h';
    const { wrapper, model } = await mountForm({
      realSelects: true,
      provide: {
        [ID_INJECTION_KEY]: { prefix: 1024, current: 0 },
        [ZINDEX_INJECTION_KEY]: { current: 0 },
      },
    });
    await wrapper.flush();

    const timeframeCombobox = internalCombobox(wrapper, 'strategy-timeframe');
    await wrapper.trigger(timeframeCombobox, 'input', { target: { value: customTimeframe } });
    await wrapper.flush();
    await wrapper.flush();
    await wrapper.flush();
    await wrapper.trigger(timeframeCombobox, 'keydown', { key: 'Enter', code: 'Enter' });
    await wrapper.flush();

    expect(model().timeframe).toBe(customTimeframe);
  });

  it('renders the six canonical timeframe values', async () => {
    const { wrapper } = await mountForm();

    expect(selectOptionValues(wrapper, 'strategy-timeframe')).toEqual(['1m', '5m', '15m', '1h', '4h', '1d']);
  });

  it('keeps custom existing symbol and timeframe values selectable', async () => {
    const { wrapper } = await mountForm({ model: draft({ symbol: 'DOGE-USDT-SWAP', timeframe: '30m' }) });
    await wrapper.flush();

    expect(selectOptionValues(wrapper, 'strategy-symbol')).toContain('DOGE-USDT-SWAP');
    expect(selectOptionValues(wrapper, 'strategy-timeframe')).toEqual(['1m', '5m', '15m', '1h', '4h', '1d', '30m']);
    expect(control(wrapper, 'strategy-timeframe').props).toMatchObject({
      filterable: true,
      'allow-create': true,
      'default-first-option': true,
    });
  });

  it('retains symbol fallbacks and successful ticker results when one ticker request fails', async () => {
    fetchTickers.mockImplementation((marketType: string) => (
      marketType === 'spot'
        ? Promise.reject(new Error('spot unavailable'))
        : Promise.resolve([{ symbol: 'LTC-USDT-SWAP' }])
    ));

    const { wrapper } = await mountForm({ model: draft({ symbol: 'LEGACY-USDT' }) });
    await wrapper.flush();

    expect(selectOptionValues(wrapper, 'strategy-symbol')).toEqual([
      'BTC-USDT',
      'ETH-USDT',
      'OKB-USDT',
      'SOL-USDT',
      'BTC-USDT-SWAP',
      'ETH-USDT-SWAP',
      'SOL-USDT-SWAP',
      'LEGACY-USDT',
      'LTC-USDT-SWAP',
    ]);
    expect(warning).toHaveBeenCalledTimes(1);
    expect(warning).toHaveBeenCalledWith('market.unableToLoadSymbols');
  });

  it('ignores pending ticker results after unmount', async () => {
    const spot = deferred<Array<{ symbol: string }>>();
    const swap = deferred<Array<{ symbol: string }>>();
    fetchTickers.mockImplementation((marketType: string) => (
      marketType === 'spot' ? spot.promise : swap.promise
    ));

    const { wrapper, formInstance } = await mountInstrumentedForm({ model: draft({ symbol: 'LEGACY-USDT' }) });
    const setupState = formInstance.setupState as unknown as { fetchedSymbols: string[]; symbolsLoading: boolean };

    expect(fetchTickers).toHaveBeenCalledWith('spot');
    expect(fetchTickers).toHaveBeenCalledWith('swap');
    expect(control(wrapper, 'strategy-symbol').props.loading).toBe(true);
    expect(setupState.symbolsLoading).toBe(true);
    expect(setupState.fetchedSymbols).toEqual([]);

    wrapper.unmount();
    spot.reject(new Error('spot unavailable'));
    swap.resolve([{ symbol: 'LTC-USDT-SWAP' }]);
    await wrapper.flush();
    await wrapper.flush();

    expect(setupState.fetchedSymbols).toEqual([]);
    expect(setupState.symbolsLoading).toBe(true);
    expect(warning).not.toHaveBeenCalled();
    expect(wrapper.all()).toEqual([]);
  });

  it('syncs strategy type, symbol, and timeframe accessibility linkage to real Element Plus comboboxes', async () => {
    const issues: StrategyValidationIssue[] = [
      { path: 'strategy_type', code: 'invalid', message: 'Bad type', line: null, column: null },
      { path: 'symbol', code: 'invalid', message: 'Bad symbol', line: null, column: null },
      { path: 'timeframe', code: 'invalid', message: 'Bad timeframe', line: null, column: null },
    ];
    const { wrapper } = await mountForm({
      issues,
      realSelects: true,
      provide: {
        [ID_INJECTION_KEY]: { prefix: 1024, current: 0 },
        [ZINDEX_INJECTION_KEY]: { current: 0 },
      },
    });
    await wrapper.flush();

    expect(fetchTickers).toHaveBeenCalledWith('spot');
    expect(fetchTickers).toHaveBeenCalledWith('swap');
    expect(internalCombobox(wrapper, 'strategy-strategy-type').props).toMatchObject({
      'aria-describedby': 'strategy-strategy-type-description strategy-strategy-type-error',
      'aria-errormessage': 'strategy-strategy-type-error',
    });
    expect(internalCombobox(wrapper, 'strategy-symbol').props).toMatchObject({
      'aria-describedby': 'strategy-symbol-description strategy-symbol-error',
      'aria-errormessage': 'strategy-symbol-error',
    });
    expect(internalCombobox(wrapper, 'strategy-timeframe').props).toMatchObject({
      'aria-describedby': 'strategy-timeframe-description strategy-timeframe-error',
      'aria-errormessage': 'strategy-timeframe-error',
    });

    await wrapper.updateProps({ issues: [] });
    await wrapper.flush();

    expect(internalCombobox(wrapper, 'strategy-strategy-type').props['aria-describedby']).toBe('strategy-strategy-type-description');
    expect(internalCombobox(wrapper, 'strategy-strategy-type').props['aria-errormessage']).toBeUndefined();
    expect(internalCombobox(wrapper, 'strategy-symbol').props['aria-describedby']).toBe('strategy-symbol-description');
    expect(internalCombobox(wrapper, 'strategy-symbol').props['aria-errormessage']).toBeUndefined();
    expect(internalCombobox(wrapper, 'strategy-timeframe').props['aria-describedby']).toBe('strategy-timeframe-description');
    expect(internalCombobox(wrapper, 'strategy-timeframe').props['aria-errormessage']).toBeUndefined();
  });

  it('renders every referenced description and error node for common and dynamic fields', async () => {
    const issues: StrategyValidationIssue[] = [
      { path: 'strategy_type', code: 'invalid', message: 'Bad type', line: null, column: null },
      { path: 'symbol', code: 'invalid', message: 'Bad symbol', line: null, column: null },
      { path: 'timeframe', code: 'invalid', message: 'Bad timeframe', line: null, column: null },
      { path: 'enabled', code: 'invalid', message: 'Bad enabled value', line: null, column: null },
      { path: 'params.fast_window', code: 'minimum', message: 'Too small', line: null, column: null },
    ];
    const { wrapper } = await mountForm({ issues });

    for (const path of ['strategy-type', 'symbol', 'timeframe', 'enabled', 'params-fast_window']) {
      const input = control(wrapper, `strategy-${path}`);
      for (const id of referencedIds(input, 'aria-describedby')) expect(wrapper.getById(id)).toBeTruthy();
      for (const id of referencedIds(input, 'aria-errormessage')) expect(wrapper.getById(id)).toBeTruthy();
    }

    expect(textContent(wrapper.getById('strategy-strategy-type-error'))).toBe('Bad type');
    expect(textContent(wrapper.getById('strategy-symbol-error'))).toBe('Bad symbol');
    expect(textContent(wrapper.getById('strategy-timeframe-error'))).toBe('Bad timeframe');
    expect(textContent(wrapper.getById('strategy-enabled-error'))).toBe('Bad enabled value');
    expect(textContent(wrapper.getById('strategy-params-fast_window-error'))).toBe('Too small');
  });

  it('keeps aria references valid when diagnostics are removed', async () => {
    const issue = { path: 'symbol', code: 'invalid', message: 'Bad symbol', line: null, column: null };
    const { wrapper } = await mountForm({ issues: [issue] });

    await wrapper.updateProps({ issues: [] });
    const symbol = control(wrapper, 'strategy-symbol');
    expect(symbol.props['aria-errormessage']).toBeUndefined();
    for (const id of referencedIds(symbol, 'aria-describedby')) expect(wrapper.getById(id)).toBeTruthy();
  });

  it('confirms a dirty type switch and mounts the selected definition defaults', async () => {
    const { wrapper, updates } = await mountForm({ dirty: true });
    const type = control(wrapper, 'strategy-strategy-type');

    await wrapper.invoke(type, 'onUpdate:modelValue', 'donchian');
    await wrapper.invoke(type, 'onChange', 'donchian');
    await nextTick();

    expect(confirm).toHaveBeenCalledTimes(1);
    expect(updates.at(-1)).toMatchObject({
      name: 'desk:btc',
      strategy_type: 'donchian',
      params: { window: 20, mode: 'close' },
    });
    expect(control(wrapper, 'strategy-params-window').props).toMatchObject({ min: 2, max: 200, step: 0.5 });
    expect(control(wrapper, 'strategy-params-mode').type).toBe('el-input');
  });

  it('keeps the original type and controls when dirty confirmation is cancelled', async () => {
    confirm.mockRejectedValueOnce('cancel');
    const { wrapper, updates } = await mountForm({ dirty: true });
    const type = control(wrapper, 'strategy-strategy-type');

    await wrapper.invoke(type, 'onUpdate:modelValue', 'donchian');
    await wrapper.invoke(type, 'onChange', 'donchian');

    expect(updates).toEqual([]);
    expect(control(wrapper, 'strategy-strategy-type').props.modelValue).toBe('ma_cross');
    expect(control(wrapper, 'strategy-params-fast_window')).toBeTruthy();
  });

  it('keeps only the latest confirmed type when confirmations resolve out of order', async () => {
    const first = deferred<void>();
    const second = deferred<void>();
    confirm.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { wrapper, updates } = await mountForm({ dirty: true });
    const type = control(wrapper, 'strategy-strategy-type');

    await wrapper.invoke(type, 'onUpdate:modelValue', 'donchian');
    const firstChange = wrapper.invoke(type, 'onChange', 'donchian');
    await wrapper.invoke(type, 'onUpdate:modelValue', 'rsi');
    const secondChange = wrapper.invoke(type, 'onChange', 'rsi');

    second.resolve();
    await secondChange;
    first.resolve();
    await firstChange;
    await wrapper.flush();

    expect(updates).toHaveLength(1);
    expect(updates[0]).toMatchObject({ strategy_type: 'rsi', params: { period: 14 } });
    expect(control(wrapper, 'strategy-strategy-type').props.modelValue).toBe('rsi');
  });

  it('returns to the canonical type after the latest cancellation and ignores an older success', async () => {
    const first = deferred<void>();
    const second = deferred<void>();
    confirm.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { wrapper, updates } = await mountForm({ dirty: true });
    const type = control(wrapper, 'strategy-strategy-type');

    await wrapper.invoke(type, 'onUpdate:modelValue', 'donchian');
    const firstChange = wrapper.invoke(type, 'onChange', 'donchian');
    await wrapper.invoke(type, 'onUpdate:modelValue', 'rsi');
    const secondChange = wrapper.invoke(type, 'onChange', 'rsi');

    second.reject('cancel');
    await secondChange;
    expect(control(wrapper, 'strategy-strategy-type').props.modelValue).toBe('ma_cross');

    first.resolve();
    await firstChange;
    await wrapper.flush();

    expect(updates).toEqual([]);
    expect(control(wrapper, 'strategy-strategy-type').props.modelValue).toBe('ma_cross');
  });

  it('invalidates a pending type confirmation when the external model type changes', async () => {
    const pending = deferred<void>();
    confirm.mockReturnValueOnce(pending.promise);
    const { wrapper, updates } = await mountForm({ dirty: true });
    const type = control(wrapper, 'strategy-strategy-type');

    await wrapper.invoke(type, 'onUpdate:modelValue', 'donchian');
    const change = wrapper.invoke(type, 'onChange', 'donchian');
    await wrapper.updateProps({ modelValue: draft({ strategy_type: 'rsi', params: { period: 14 } }) });
    pending.resolve();
    await change;
    await wrapper.flush();

    expect(updates).toEqual([]);
    expect(control(wrapper, 'strategy-strategy-type').props.modelValue).toBe('rsi');
  });

  it('makes edit names immutable and active forms readonly through the rendered form', async () => {
    const editable = await mountForm({ mode: 'edit' });
    expect(control(editable.wrapper, 'strategy-name').props.readonly).toBe(true);

    const active = await mountForm({ mode: 'edit', readonly: true });
    expect(active.wrapper.find((node) => node.type === 'el-form').props.disabled).toBe(true);
    expect(control(active.wrapper, 'strategy-name').props.readonly).toBe(true);
    expect(control(active.wrapper, 'strategy-strategy-type').props.disabled).toBe(true);
    expect(control(active.wrapper, 'strategy-symbol').props.disabled).toBe(true);
    expect(control(active.wrapper, 'strategy-timeframe').props.disabled).toBe(true);
  });
});
