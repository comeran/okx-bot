import { defineComponent, h } from 'vue';
import { describe, expect, it, vi } from 'vitest';

import { mount } from '@/test-utils/mount';
import type { BacktestResultDetail as BacktestResultDetailData } from '@/types/backtest';

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

vi.mock('@/components/charts/Candlestick.vue', () => ({
  default: defineComponent({
    name: 'Candlestick',
    props: ['klines', 'markers', 'symbol', 'timeframe', 'height'],
    setup(props) {
      return () => h('candlestick-stub', {
        'data-klines': (props.klines as unknown[]).length,
        'data-markers': (props.markers as unknown[]).length,
        'data-symbol': props.symbol,
        'data-timeframe': props.timeframe,
        'data-height': props.height,
      });
    },
  }),
}));

const BacktestResultDetail = (await import('./BacktestResultDetail.vue')).default;

const detail: BacktestResultDetailData = {
  result: {
    id: 'result-a',
    strategy: 'ma_cross',
    symbol: 'BTC-USDT',
    timeframe: '1h',
    start_time: new Date('2026-01-01T00:00:00Z').getTime(),
    end_time: new Date('2026-01-02T00:00:00Z').getTime(),
    initial_capital: 100000,
    total_return: 0.18,
    sharpe_ratio: 1.45,
    max_drawdown: 0.04,
    win_rate: 0.66,
    total_trades: 9,
    created_at: new Date('2026-01-03T00:00:00Z').getTime(),
  },
  klines: [
    {
      symbol: 'BTC-USDT',
      timeframe: '1h',
      timestamp: new Date('2026-01-01T00:00:00Z').getTime(),
      open: 1,
      high: 2,
      low: 0.5,
      close: 1.5,
      volume: 100,
    },
  ],
  markers: [],
};

describe('BacktestResultDetail', () => {
  it('shows a loading state until the matching detail arrives', async () => {
    const wrapper = await mount(BacktestResultDetail, {
      locale: 'en',
      props: {
        selectedDetail: null,
        selectedResultId: 'result-a',
        loading: true,
        error: null,
      },
    });

    expect(wrapper.text()).toContain('Loading');
    expect(wrapper.findAll((node) => node.type === 'candlestick-stub')).toHaveLength(0);
  });

  it('renders the summary and chart in a two-column layout when detail is ready', async () => {
    const wrapper = await mount(BacktestResultDetail, {
      locale: 'en',
      props: {
        selectedDetail: detail,
        selectedResultId: 'result-a',
        loading: false,
        error: null,
      },
    });

    expect(wrapper.find((node) => node.type === 'div' && String(node.props.class).includes('backtest-result-detail__layout'))).toBeTruthy();
    const chart = wrapper.find((node) => node.type === 'candlestick-stub');
    expect(chart.props['data-symbol']).toBe('BTC-USDT');
    expect(chart.props['data-timeframe']).toBe('1h');
    expect(chart.props['data-height']).toBe(460);
    expect(wrapper.text()).toContain('Summary');
    expect(wrapper.text()).toContain('Chart');
  });

  it('formats summary timestamps with the active locale', async () => {
    const wrapper = await mount(BacktestResultDetail, {
      locale: 'zh-CN',
      props: {
        selectedDetail: detail,
        selectedResultId: 'result-a',
        loading: false,
        error: null,
      },
    });

    expect(wrapper.text()).toContain(`zh-CN:${detail.result.start_time}`);
    expect(wrapper.text()).toContain(`zh-CN:${detail.result.end_time}`);
    expect(wrapper.text()).toContain(`zh-CN:${detail.result.created_at}`);
    expect(backtestUtils.formatBacktestTime).toHaveBeenCalledWith(detail.result.start_time, 'zh-CN');
    expect(backtestUtils.formatBacktestTime).toHaveBeenCalledWith(detail.result.end_time, 'zh-CN');
    expect(backtestUtils.formatBacktestTime).toHaveBeenCalledWith(detail.result.created_at, 'zh-CN');
  });

  it('keeps the stacked detail sections intact for the mobile layout', async () => {
    const wrapper = await mount(BacktestResultDetail, {
      locale: 'en',
      props: {
        selectedDetail: detail,
        selectedResultId: 'result-a',
        loading: false,
        error: null,
      },
    });

    const layout = wrapper.find((node) => node.type === 'div' && String(node.props.class).includes('backtest-result-detail__layout'));
    const summary = wrapper.find((node) => node.type === 'section' && String(node.props.class).includes('backtest-result-detail__summary'));
    const chartSection = wrapper.find((node) => node.type === 'section' && String(node.props.class).includes('backtest-result-detail__chart-section'));

    expect(layout).toBeTruthy();
    expect(summary).toBeTruthy();
    expect(chartSection).toBeTruthy();
    expect(wrapper.findAll((node) => node.type === 'section' && String(node.props.class).includes('backtest-result-detail__'))).toHaveLength(2);
    expect(wrapper.find((node) => node.type === 'div' && String(node.props.class).includes('backtest-result-detail__metrics'))).toBeTruthy();
    expect(wrapper.find((node) => node.type === 'dl' && String(node.props.class).includes('backtest-result-detail__fields'))).toBeTruthy();
  });

  it('emits retry when the detail fails to load', async () => {
    const onRetry = vi.fn();
    const wrapper = await mount(BacktestResultDetail, {
      locale: 'en',
      props: {
        selectedDetail: null,
        selectedResultId: 'result-a',
        loading: false,
        error: 'Failed to load backtest detail chart. Please try another result or refresh.',
        'onRetry': onRetry,
      },
    });

    const retryButton = wrapper.find((node) => node.type === 'button');
    await wrapper.invoke(retryButton, 'onClick', {});
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
