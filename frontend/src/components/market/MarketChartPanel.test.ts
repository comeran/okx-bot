import { describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';

import { defineHostComponent, mount } from '@/test-utils/mount';

vi.mock('@/components/charts/Candlestick.vue', () => ({
  default: defineComponent({
    name: 'Candlestick',
    props: ['klines', 'symbol', 'timeframe', 'height'],
    setup(props) {
      return () => h('candlestick-stub', {
        'data-testid': 'candlestick',
        'data-klines': (props.klines as unknown[]).length,
        'data-symbol': props.symbol,
        'data-timeframe': props.timeframe,
        style: { height: `${props.height}px` },
      });
    },
  }),
}));

const MarketChartPanel = (await import('./MarketChartPanel.vue')).default;

const sampleKline = {
  symbol: 'BTC-USDT',
  timeframe: '1h',
  timestamp: 1700000000000,
  open: 1,
  high: 2,
  low: 0.5,
  close: 1.5,
  volume: 10,
};

describe('MarketChartPanel', () => {
  it('renders a stable chart container while loading', async () => {
    const wrapper = await mount(MarketChartPanel, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
      },
      props: {
        klines: [],
        loading: true,
        error: '',
        rangeQuery: false,
        symbol: 'BTC-USDT',
        timeframe: '1h',
      },
    });

    const frame = wrapper.getByTestId('market-chart-frame');
    expect(String(frame.props.class)).toContain('market-chart-panel__frame');
    expect(wrapper.text()).toContain('Loading');
    wrapper.unmount();
  });

  it('shows localized default and range empty states', async () => {
    const defaultWrapper = await mount(MarketChartPanel, {
      locale: 'en',
      props: { klines: [], loading: false, error: '', rangeQuery: false, symbol: 'BTC-USDT', timeframe: '1h' },
    });
    expect(defaultWrapper.text()).toContain('No kline data available for the selected market.');

    const rangeWrapper = await mount(MarketChartPanel, {
      locale: 'en',
      props: { klines: [], loading: false, error: '', rangeQuery: true, symbol: 'BTC-USDT', timeframe: '1h' },
    });
    expect(rangeWrapper.text()).toContain('No cached K-line data found for the selected time range.');

    defaultWrapper.unmount();
    rangeWrapper.unmount();
  });

  it('shows errors and emits retry', async () => {
    const retry = vi.fn();
    const wrapper = await mount(MarketChartPanel, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
      },
      props: {
        klines: [],
        loading: false,
        error: 'Failed to load market data. Please try again.',
        rangeQuery: false,
        symbol: 'BTC-USDT',
        timeframe: '1h',
        onRetry: retry,
      },
    });

    expect(wrapper.text()).toContain('Failed to load market data. Please try again.');
    await wrapper.invoke(wrapper.find((node) => node.type === 'el-button'), 'onClick');
    expect(retry).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it('renders candlestick content inside the stable chart frame', async () => {
    const wrapper = await mount(MarketChartPanel, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
      },
      props: {
        klines: [sampleKline],
        loading: false,
        error: '',
        rangeQuery: false,
        symbol: 'BTC-USDT',
        timeframe: '1h',
      },
    });

    expect(wrapper.getByTestId('market-chart').props['data-klines']).toBe(1);
    expect(wrapper.getByTestId('market-chart-frame').props.style).toEqual({ minHeight: '420px' });
    wrapper.unmount();
  });
});
