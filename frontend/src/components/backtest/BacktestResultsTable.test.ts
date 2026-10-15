import { defineComponent, h } from 'vue';
import { describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount } from '@/test-utils/mount';
import type { BacktestResult } from '@/types/backtest';

const backtestUtils = vi.hoisted(() => ({
  formatBacktestTime: vi.fn((timestamp?: number | null, locale = 'en') => {
    if (timestamp === null || timestamp === undefined || !Number.isFinite(timestamp)) {
      return '—';
    }
    return `${locale}:${timestamp}`;
  }),
}));

vi.mock('@/utils/backtest', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/backtest')>(),
  formatBacktestTime: backtestUtils.formatBacktestTime,
}));

const BacktestResultsTable = (await import('./BacktestResultsTable.vue')).default;

const elComponents = {
  ElButton: defineHostComponent('el-button'),
  ElTable: defineHostComponent('el-table'),
  ElTableColumn: defineComponent({
    name: 'ElTableColumn',
    setup(_props, { slots }) {
      return () => h('el-table-column', {}, slots.default?.({ row: results[0] }));
    },
  }),
};

const results: BacktestResult[] = [
  {
    id: 'result-a',
    strategy: 'ma_cross',
    symbol: 'BTC-USDT',
    timeframe: '1h',
    start_time: new Date('2026-01-01T00:00:00Z').getTime(),
    end_time: new Date('2026-01-02T00:00:00Z').getTime(),
    initial_capital: 100000,
    total_return: 0.15,
    sharpe_ratio: 1.23,
    max_drawdown: 0.05,
    win_rate: 0.6,
    total_trades: 12,
    created_at: new Date('2026-01-03T00:00:00Z').getTime(),
  },
  {
    id: 'result-b',
    strategy: 'donchian_breakout',
    symbol: 'ETH-USDT',
    timeframe: '15m',
    start_time: new Date('2026-01-04T00:00:00Z').getTime(),
    end_time: new Date('2026-01-05T00:00:00Z').getTime(),
    initial_capital: 50000,
    total_return: 0.08,
    sharpe_ratio: 0.9,
    max_drawdown: 0.02,
    win_rate: 0.55,
    total_trades: 8,
    created_at: new Date('2026-01-06T00:00:00Z').getTime(),
  },
];

describe('BacktestResultsTable', () => {
  it('emits selection and refresh events while preserving the selected row key', async () => {
    const onSelectResult = vi.fn();
    const onRefresh = vi.fn();

    const wrapper = await mount(BacktestResultsTable, {
      locale: 'zh-CN',
      components: elComponents,
      props: {
        results,
        selectedResultId: 'result-b',
        loading: false,
        'onSelectResult': onSelectResult,
        'onRefresh': onRefresh,
      },
    });

    const table = wrapper.find((node) => node.type === 'el-table');
    expect(table.props.currentRowKey ?? table.props['current-row-key']).toBe('result-b');
    expect(wrapper.text()).toContain(`zh-CN:${results[0].start_time}`);
    expect(wrapper.text()).toContain(`zh-CN:${results[0].end_time}`);
    expect(wrapper.text()).toContain(`zh-CN:${results[0].created_at}`);
    expect(backtestUtils.formatBacktestTime).toHaveBeenCalledWith(results[0].start_time, 'zh-CN');
    expect(backtestUtils.formatBacktestTime).toHaveBeenCalledWith(results[0].end_time, 'zh-CN');
    expect(backtestUtils.formatBacktestTime).toHaveBeenCalledWith(results[0].created_at, 'zh-CN');

    const scrollRegion = wrapper.find((node) => node.type === 'div' && String(node.props.class).includes('responsive-table__scroll-region'));
    expect(scrollRegion.props.role).toBe('region');
    expect(scrollRegion.props['aria-label']).toBe('回测历史');
    expect(scrollRegion.props['aria-describedby']).toMatch(/^responsive-table-description-/);

    await wrapper.invoke(table, 'onRowClick', results[0], {}, {});
    expect(onSelectResult).toHaveBeenCalledWith('result-a');

    const refreshButton = wrapper.find((node) => node.type === 'el-button');
    await wrapper.invoke(refreshButton, 'onClick', {});
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('applies the selected row class from the Element Plus row-class-name payload', async () => {
    const wrapper = await mount(BacktestResultsTable, {
      locale: 'en',
      components: elComponents,
      props: {
        results,
        selectedResultId: 'result-b',
        loading: false,
      },
    });

    const table = wrapper.find((node) => node.type === 'el-table');
    const rowClassName = (table.props.rowClassName ?? table.props['row-class-name']) as (
      payload: { row: BacktestResult; rowIndex: number },
    ) => string;

    expect(rowClassName({ row: results[1], rowIndex: 1 })).toBe('backtest-results-table__row--selected');
    expect(rowClassName({ row: results[0], rowIndex: 0 })).toBe('');
  });

  it('shows loading and empty data states', async () => {
    const loadingWrapper = await mount(BacktestResultsTable, {
      locale: 'en',
      components: elComponents,
      props: {
        results: [],
        selectedResultId: null,
        loading: true,
      },
    });

    expect(loadingWrapper.text()).toContain('Loading');
    expect(loadingWrapper.findAll((node) => node.type === 'el-table')).toHaveLength(0);

    const emptyWrapper = await mount(BacktestResultsTable, {
      locale: 'en',
      components: elComponents,
      props: {
        results: [],
        selectedResultId: null,
        loading: false,
      },
    });

    expect(emptyWrapper.text()).toContain('No backtest results yet.');
  });
});
