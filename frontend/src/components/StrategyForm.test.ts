import { beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';

import { defineHostComponent, mount, textContent, type TestHostNode } from '@/test-utils/mount';
import type { StrategyConfigPayload, StrategyDefinition, StrategyValidationIssue } from '@/types/strategy';
import StrategyForm from './StrategyForm.vue';

const confirm = vi.hoisted(() => vi.fn());

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
  ElMessageBox: { confirm },
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
} = {}) {
  let model = options.model ?? draft();
  const updates: StrategyConfigPayload[] = [];
  const wrapper = await mount(StrategyForm, {
    components,
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

function referencedIds(node: TestHostNode, attribute: string): string[] {
  const value = node.props[attribute];
  return typeof value === 'string' ? value.split(/\s+/).filter(Boolean) : [];
}

describe('StrategyForm', () => {
  beforeEach(() => {
    confirm.mockReset();
    confirm.mockResolvedValue(undefined);
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
  });
});
