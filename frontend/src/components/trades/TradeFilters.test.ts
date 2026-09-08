import { describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';
import { createTradeFilters } from '@/utils/trades';

const TradeFilters = (await import('./TradeFilters.vue')).default;

const components = {
  ElButton: defineHostComponent('el-button'),
  ElInput: defineHostComponent('el-input'),
  ElOption: defineHostComponent('el-option'),
  ElSelect: defineHostComponent('el-select'),
};

describe('TradeFilters', () => {
  it('renders the trade filter layout structure and full-width search field contract', async () => {
    const wrapper = await mount(TradeFilters, {
      locale: 'en',
      components,
      props: {
        modelValue: createTradeFilters(),
        strategyOptions: ['ma_cross', 'rsi_mean_reversion'],
        symbolOptions: ['BTC-USDT', 'ETH-USDT'],
      },
    });

    const root = wrapper.find((node) => node.type === 'div' && node.props.class === 'trade-filters');
    const grid = wrapper.find((node) => node.type === 'div' && node.props.class === 'trade-filters__grid');
    const fields = wrapper.findAll((node) => node.type === 'div' && String(node.props.class).split(' ').includes('trade-filters__field'));
    const searchField = wrapper.find((node) => node.type === 'div' && node.props.class === 'trade-filters__field trade-filters__field--search');

    expect(root.props.class).toContain('trade-filters');
    expect(grid.props.class).toContain('trade-filters__grid');
    expect(fields).toHaveLength(4);
    expect(searchField.props.class).toContain('trade-filters__field--search');
    expect(wrapper.text()).toContain('Clear filters');

    wrapper.unmount();
  });

  it('renders localized labels, accessible controls, and available filter options', async () => {
    const wrapper = await mount(TradeFilters, {
      locale: 'en',
      components,
      props: {
        modelValue: createTradeFilters(),
        strategyOptions: ['ma_cross', 'rsi_mean_reversion'],
        symbolOptions: ['BTC-USDT', 'ETH-USDT'],
      },
    });

    const labels = wrapper.findAll((node) => node.type === 'label');
    expect(labels.map((label) => ({
      for: label.props.for,
      text: textContent(label),
    }))).toEqual([
      { for: 'trade-filter-strategy', text: 'Strategy' },
      { for: 'trade-filter-symbol', text: 'Symbol' },
      { for: 'trade-filter-side', text: 'Side' },
      { for: 'trade-filter-search', text: 'Search' },
    ]);

    const controls = [
      { id: 'trade-filter-strategy', label: 'Strategy', type: 'el-select' },
      { id: 'trade-filter-symbol', label: 'Symbol', type: 'el-select' },
      { id: 'trade-filter-side', label: 'Side', type: 'el-select' },
      { id: 'trade-filter-search', label: 'Search', type: 'el-input' },
    ];
    for (const control of controls) {
      const field = wrapper.find((node) => node.type === control.type && node.props.id === control.id);
      expect(field.props['aria-label']).toBe(control.label);
    }

    expect(wrapper.text()).toContain('Clear filters');

    const options = wrapper.findAll((node) => node.type === 'el-option');
    expect(options.map((option) => option.props.label)).toEqual([
      'All strategies',
      'ma_cross',
      'rsi_mean_reversion',
      'All symbols',
      'BTC-USDT',
      'ETH-USDT',
      'All sides',
      'Buy',
      'Sell',
    ]);

    const clearButton = wrapper.find((node) => node.type === 'el-button');
    expect(clearButton.props.disabled).toBe(true);
    expect(textContent(clearButton)).toBe('Clear filters');

    wrapper.unmount();
  });

  it('does not emit clear events while disabled', async () => {
    const onUpdateModelValue = vi.fn();
    const onClear = vi.fn();

    const wrapper = await mount(TradeFilters, {
      locale: 'en',
      components,
      props: {
        modelValue: {
          strategy: 'ma_cross',
          symbol: 'BTC-USDT',
          side: 'buy',
          search: 'btc',
        },
        strategyOptions: ['ma_cross'],
        symbolOptions: ['BTC-USDT'],
        disabled: true,
        'onUpdate:modelValue': onUpdateModelValue,
        onClear,
      },
    });

    const clearButton = wrapper.find((node) => node.type === 'el-button');
    expect(clearButton.props.disabled).toBe(true);
    await wrapper.invoke(clearButton, 'onClick', {});

    expect(onUpdateModelValue).not.toHaveBeenCalled();
    expect(onClear).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it('emits model updates and clear events', async () => {
    const onUpdateModelValue = vi.fn();
    const onClear = vi.fn();

    const wrapper = await mount(TradeFilters, {
      locale: 'en',
      components,
      props: {
        modelValue: {
          strategy: 'ma_cross',
          symbol: 'BTC-USDT',
          side: 'buy',
          search: 'btc',
        },
        strategyOptions: ['ma_cross'],
        symbolOptions: ['BTC-USDT'],
        'onUpdate:modelValue': onUpdateModelValue,
        onClear,
      },
    });

    const input = wrapper.find((node) => node.type === 'el-input');
    await wrapper.invoke(input, 'onUpdate:modelValue', 'ETH');
    expect(onUpdateModelValue).toHaveBeenCalledWith({
      strategy: 'ma_cross',
      symbol: 'BTC-USDT',
      side: 'buy',
      search: 'ETH',
    });

    const clearButton = wrapper.find((node) => node.type === 'el-button');
    expect(clearButton.props.disabled).toBe(false);
    await wrapper.invoke(clearButton, 'onClick', {});

    expect(onUpdateModelValue).toHaveBeenLastCalledWith(createTradeFilters());
    expect(onClear).toHaveBeenCalledTimes(1);

    wrapper.unmount();
  });
});
