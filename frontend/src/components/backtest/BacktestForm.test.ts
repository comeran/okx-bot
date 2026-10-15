import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { reactive } from 'vue';
import { describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';

const elComponents = Object.fromEntries([
  'ElForm',
  'ElFormItem',
  'ElSelect',
  'ElOption',
  'ElDatePicker',
  'ElInputNumber',
  'ElButton',
].map((name) => [name, defineHostComponent(name.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`).slice(1))]));

const BacktestForm = (await import('./BacktestForm.vue')).default;
const backtestFormSource = readFileSync(fileURLToPath(new URL('./BacktestForm.vue', import.meta.url)), 'utf8');

function createForm() {
  return reactive({
    strategy: 'ma_cross',
    symbol: 'BTC-USDT',
    timeframe: '1h',
    startTime: new Date('2026-01-01T00:00:00Z'),
    endTime: new Date('2026-01-02T00:00:00Z'),
    initialCapital: 100000,
  });
}

function strategyOption(value: string, label = value, disabled = false, backendValue = value) {
  return {
    id: value,
    value,
    backendValue,
    label,
    ...(disabled ? { disabled: true } : {}),
  };
}

describe('BacktestForm', () => {
  it('keeps the responsive style contract aligned with the SFC CSS', () => {
    expect(backtestFormSource).toContain('@media (max-width: 1023px)');
    expect(backtestFormSource).toContain('@media (max-width: 767px)');
    expect(backtestFormSource).toMatch(/\.backtest-form__actions\s*\{[\s\S]*?justify-content:\s*stretch;[\s\S]*?\}/);
    expect(backtestFormSource).toMatch(/\.backtest-form__submit\s*\{[\s\S]*?width:\s*100%;[\s\S]*?min-width:\s*0;[\s\S]*?\}/);
  });

  it('keeps the desktop, tablet, and mobile layout classes aligned with the form CSS', async () => {
    const wrapper = await mount(BacktestForm, {
      locale: 'en',
      components: elComponents,
      props: {
        form: createForm(),
        strategyOptions: [strategyOption('ma_cross'), strategyOption('donchian_breakout')],
        symbolOptions: ['BTC-USDT', 'ETH-USDT'],
        timeframeOptions: ['1m', '1h'],
      },
    });

    const grid = wrapper.find((node) => node.type === 'div' && String(node.props.class).includes('backtest-form__grid'));
    const groups = wrapper.findAll((node) => node.type === 'section' && String(node.props.class).includes('backtest-form__group'));
    const titles = wrapper
      .findAll((node) => node.type === 'h3' && String(node.props.class).includes('backtest-form__group-title'))
      .map((node) => textContent(node));

    expect(grid).toBeTruthy();
    expect(groups).toHaveLength(4);
    expect(titles).toEqual(['Strategy', 'Instrument', 'Period', 'Capital']);
    expect(wrapper.find((node) => node.type === 'div' && String(node.props.class).includes('backtest-form__actions'))).toBeTruthy();
    expect(wrapper.find((node) => node.type === 'el-button' && String(node.props.class).includes('backtest-form__submit'))).toBeTruthy();
    expect(wrapper.text()).toContain('Configure the strategy, market data, time range, and capital before running a backtest.');
  });

  it('renders localized validation feedback without changing the run emit contract', async () => {
    const onRun = vi.fn();
    const wrapper = await mount(BacktestForm, {
      locale: 'en',
      components: elComponents,
      props: {
        form: createForm(),
        strategyOptions: [strategyOption('ma_cross')],
        symbolOptions: ['BTC-USDT'],
        timeframeOptions: ['1h'],
        validationError: 'timeRequired',
        onRun,
      },
    });

    expect(wrapper.text()).toContain('Start time and end time are required');
    expect(wrapper.text()).not.toContain('timeRequired');

    const elForm = wrapper.find((node) => node.type === 'el-form');
    await wrapper.invoke(elForm, 'onSubmit', { preventDefault() {} });

    expect(onRun).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it('renders every validation error from the locale mapping', async () => {
    const wrapper = await mount(BacktestForm, {
      locale: 'en',
      components: elComponents,
      props: {
        form: createForm(),
        strategyOptions: [strategyOption('ma_cross')],
        symbolOptions: ['BTC-USDT'],
        timeframeOptions: ['1h'],
        validationError: 'initialCapitalPositive',
      },
    });

    expect(wrapper.text()).toContain('Initial capital must be greater than 0');
    expect(wrapper.text()).not.toContain('initialCapitalPositive');
    wrapper.unmount();
  });

  it('renders the backtest conflict explanation when present', async () => {
    const wrapper = await mount(BacktestForm, {
      locale: 'en',
      components: elComponents,
      props: {
        form: createForm(),
        strategyOptions: [strategyOption('ma_cross')],
        strategyConflictMessage: 'Saved configs with names that match built-in strategies are disabled.',
        symbolOptions: ['BTC-USDT'],
        timeframeOptions: ['1h'],
      },
    });

    expect(wrapper.text()).toContain('Saved configs with names that match built-in strategies are disabled.');
    wrapper.unmount();
  });

  it('shows catalog retry guidance when strategy types are unavailable', async () => {
    const onRetryStrategies = vi.fn();
    const wrapper = await mount(BacktestForm, {
      locale: 'en',
      components: elComponents,
      props: {
        form: createForm(),
        strategyOptions: [strategyOption('saved_bollinger', 'saved_bollinger · Saved config · Disabled', true)],
        strategyCatalogUnavailable: true,
        symbolOptions: ['BTC-USDT'],
        timeframeOptions: ['1h'],
        onRetryStrategies,
      },
    });

    expect(wrapper.text()).toContain('Strategy types could not be loaded. Saved configs are disabled until the catalog is available again. Retry loading strategies to restore them.');
    expect(wrapper.find((node) => node.type === 'el-select').props.disabled).toBe(true);

    const retryButton = wrapper.find((node) => node.type === 'el-button');
    await wrapper.invoke(retryButton, 'onClick');

    expect(onRetryStrategies).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it('keeps the submit button disabled while running', async () => {
    const wrapper = await mount(BacktestForm, {
      locale: 'en',
      components: elComponents,
      props: {
        form: createForm(),
        strategyOptions: [strategyOption('ma_cross'), strategyOption('donchian_breakout')],
        symbolOptions: ['BTC-USDT', 'ETH-USDT'],
        timeframeOptions: ['1m', '1h'],
        running: true,
      },
    });

    const submitButton = wrapper.find((node) => node.type === 'el-button');
    expect(submitButton.props.disabled).toBe(true);
    expect(submitButton.props.loading).toBe(true);

    const selects = wrapper.findAll((node) => node.type === 'el-select');
    expect(selects[0].props['aria-label']).toBe('Strategy');
    expect(selects[1].props['aria-label']).toBe('Symbol');
    expect(selects[2].props['aria-label']).toBe('Timeframe');

    wrapper.unmount();
  });

  it('emits run when the form submits', async () => {
    const form = createForm();
    const onRun = vi.fn();

    const wrapper = await mount(BacktestForm, {
      locale: 'en',
      components: elComponents,
      props: {
        form,
        strategyOptions: [strategyOption('ma_cross')],
        symbolOptions: ['BTC-USDT'],
        timeframeOptions: ['1h'],
        'onRun': onRun,
      },
    });

    const elForm = wrapper.find((node) => node.type === 'el-form');
    await wrapper.invoke(elForm, 'onSubmit', { preventDefault() {} });

    expect(onRun).toHaveBeenCalledTimes(1);

    wrapper.unmount();
  });
});
