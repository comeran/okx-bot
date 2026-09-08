import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Ref } from 'vue';

import { mount } from '@/test-utils/mount';

const initMock = vi.hoisted(() => vi.fn());
const useMock = vi.hoisted(() => vi.fn());
const localeState = vi.hoisted(() => ({
  locale: null as Ref<'en' | 'zh-CN'> | null,
}));

const resolveMessage = (locale: 'en' | 'zh-CN', key: string): string => {
  const messages: Record<'en' | 'zh-CN', Record<string, string>> = {
    en: {
      'market.chart.candlestick': 'Candlestick',
      'market.chart.price': 'Price',
      'market.chart.volume': 'Volume',
      'market.chart.buy': 'Buy',
      'market.chart.sell': 'Sell',
    },
    'zh-CN': {
      'market.chart.candlestick': 'K 线图',
      'market.chart.price': '价格',
      'market.chart.volume': '成交量',
      'market.chart.buy': '买入',
      'market.chart.sell': '卖出',
    },
  };

  return messages[locale][key] ?? key;
};

const cloneValue = <T,>(value: T): T => {
  if (typeof structuredClone === 'function') {
    try {
      return structuredClone(value);
    } catch {
      // fall through to JSON clone for values like formatter callbacks
    }
  }
  return JSON.parse(JSON.stringify(value)) as T;
};

const mergeValue = <T,>(target: T, patch: unknown): T => {
  if (Array.isArray(patch)) {
    if (!Array.isArray(target)) return cloneValue(patch) as T;

    return patch.map((item, index) => {
      const current = target[index];
      if (item && typeof item === 'object' && !Array.isArray(item) && current && typeof current === 'object' && !Array.isArray(current)) {
        return mergeValue(current, item);
      }
      return cloneValue(item);
    }) as T;
  }

  if (!patch || typeof patch !== 'object') return patch as T;

  const result = (target && typeof target === 'object' && !Array.isArray(target)
    ? { ...(target as Record<string, unknown>) }
    : {}) as Record<string, unknown>;

  for (const [key, value] of Object.entries(patch)) {
    result[key] = mergeValue(result[key], value);
  }

  return result as T;
};

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n');
  const vue = await vi.importActual<typeof import('vue')>('vue');

  return {
    ...actual,
    useI18n: () => {
      if (!localeState.locale) localeState.locale = vue.ref<'en' | 'zh-CN'>('en');

      return {
        locale: localeState.locale,
        t: (key: string) => resolveMessage(localeState.locale!.value, key),
      };
    },
  };
});

vi.mock('echarts/core', () => ({
  init: initMock,
  use: useMock,
}));

vi.mock('echarts/charts', () => ({
  BarChart: {},
  CandlestickChart: {},
  ScatterChart: {},
}));

vi.mock('echarts/components', () => ({
  DataZoomComponent: {},
  GridComponent: {},
  LegendComponent: {},
  TitleComponent: {},
  TooltipComponent: {},
}));

vi.mock('echarts/renderers', () => ({
  CanvasRenderer: {},
}));

const observerMock = vi.hoisted(() => ({
  observe: vi.fn(),
  disconnect: vi.fn(),
}));

const chartState = vi.hoisted(() => ({
  option: {} as Record<string, unknown>,
}));

let chartInstance: {
  setOption: ReturnType<typeof vi.fn>;
  getOption: ReturnType<typeof vi.fn>;
  resize: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>;
};

class ResizeObserverMock {
  observe = observerMock.observe;
  disconnect = observerMock.disconnect;

  constructor(_callback: ResizeObserverCallback) {}
}

if (!globalThis.ResizeObserver) {
  // @ts-expect-error test-only ResizeObserver shim
  globalThis.ResizeObserver = ResizeObserverMock;
}

const Candlestick = (await import('./Candlestick.vue')).default;

function snapshotChartOption() {
  return {
    ...cloneValue(chartState.option),
    legend: chartState.option.legend ? [cloneValue(chartState.option.legend)] : [],
    series: Array.isArray(chartState.option.series) ? cloneValue(chartState.option.series) : [],
  };
}

function createChartInstance() {
  chartState.option = {};
  return {
    setOption: vi.fn((option: Record<string, unknown>, notMerge?: boolean) => {
      chartState.option = notMerge ? cloneValue(option) : mergeValue(chartState.option, option);
    }),
    getOption: vi.fn(() => snapshotChartOption()),
    resize: vi.fn(),
    dispose: vi.fn(),
  };
}

describe('Candlestick', () => {
  beforeEach(() => {
    initMock.mockReset();
    useMock.mockReset();
    observerMock.observe.mockReset();
    observerMock.disconnect.mockReset();
    localeState.locale?.value && (localeState.locale.value = 'en');
    chartInstance = createChartInstance();
    initMock.mockReturnValue(chartInstance);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('initializes on data, disposes on empty, and cleans up observers', async () => {
    const wrapper = await mount(Candlestick, {
      props: {
        klines: [],
        symbol: 'BTC-USDT',
        timeframe: '1h',
      },
    });

    expect(initMock).not.toHaveBeenCalled();
    expect(observerMock.observe).toHaveBeenCalledTimes(1);

    await wrapper.updateProps({
      klines: [
        {
          symbol: 'BTC-USDT',
          timeframe: '1h',
          timestamp: 1700000000000,
          open: 1,
          high: 2,
          low: 0.5,
          close: 1.5,
          volume: 10,
        },
      ],
    });

    expect(initMock).toHaveBeenCalledTimes(1);
    expect(chartInstance.setOption).toHaveBeenCalledTimes(1);

    await wrapper.updateProps({ klines: [] });

    expect(chartInstance.dispose).toHaveBeenCalledTimes(1);

    wrapper.unmount();

    expect(observerMock.disconnect).toHaveBeenCalledTimes(1);
    expect(chartInstance.dispose).toHaveBeenCalledTimes(1);
  });

  it('patches locale text without rebuilding series data or clearing zoom state', async () => {
    if (!localeState.locale) {
      throw new Error('Locale ref was not initialized');
    }
    localeState.locale.value = 'en';

    const wrapper = await mount(Candlestick, {
      props: {
        klines: [
          {
            symbol: 'BTC-USDT',
            timeframe: '1h',
            timestamp: 1700000000000,
            open: 1,
            high: 2,
            low: 0.5,
            close: 1.5,
            volume: 10,
          },
        ],
        markers: [
          { side: 'buy', symbol: 'BTC-USDT', timestamp: 1700000000000, price: 1.2 },
          { side: 'sell', symbol: 'BTC-USDT', timestamp: 1700000000000, price: 1.8 },
        ],
      },
    });

    expect(initMock).toHaveBeenCalledTimes(1);
    expect(chartInstance.setOption).toHaveBeenCalledTimes(1);

    const firstCallOption = chartInstance.setOption.mock.calls[0][0] as {
      title: { text: string };
      legend: { data: string[] };
      dataZoom: Array<{ start: number; end: number }>;
      series: Array<{ name: string; data: unknown[] }>;
      xAxis: Array<{ axisLabel: { formatter: (value: string | number) => string } }>;
    };

    expect(firstCallOption.title.text).toBe('Candlestick');
    expect(firstCallOption.legend.data).toEqual(['Price', 'Volume', 'Buy', 'Sell']);
    expect(firstCallOption.series.map((series) => series.name)).toEqual(['Price', 'Volume', 'Buy', 'Sell']);
    expect(firstCallOption.series.every((series) => Array.isArray(series.data))).toBe(true);
    const beforeLocaleChangeLabel = firstCallOption.xAxis[0].axisLabel.formatter(1700000000000);
    expect(beforeLocaleChangeLabel).toBe(
      new Intl.DateTimeFormat('en', {
        month: 'long',
        weekday: 'long',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date(1700000000000)),
    );
    expect(firstCallOption.dataZoom).toEqual([
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      { show: true, type: 'slider', xAxisIndex: [0, 1], top: '92%', start: 0, end: 100 },
    ]);

    chartState.option.legend = {
      ...(chartState.option.legend as Record<string, unknown>),
      selected: {
        Price: true,
        Volume: false,
        Buy: true,
        Sell: true,
      },
    };
    const beforeLocaleChangeZoom = cloneValue(chartState.option.dataZoom);
    const beforeLocaleChangeSeriesData = cloneValue(
      (chartState.option.series as Array<{ data?: unknown[] }> | undefined)?.map((series) => series.data),
    );

    localeState.locale.value = 'zh-CN';
    await wrapper.flush();

    expect(chartInstance.setOption).toHaveBeenCalledTimes(2);
    expect(chartInstance.setOption.mock.calls[1][1]).toBe(false);

    const localePatch = chartInstance.setOption.mock.calls[1][0] as {
      title: { text: string };
      legend: { data: string[]; selected: Record<string, boolean> };
      xAxis: Array<{ axisLabel: { formatter: (value: string | number) => string } }>;
      series: Array<{ id: string; name: string; data?: unknown[] }>;
    };

    expect(localePatch.title.text).toBe('K 线图');
    expect(localePatch.legend.data).toEqual(['价格', '成交量', '买入', '卖出']);
    expect(localePatch.legend.selected).toEqual({
      价格: true,
      成交量: false,
      买入: true,
      卖出: true,
    });
    expect(localePatch.xAxis[0].axisLabel.formatter(1700000000000)).toBe(
      new Intl.DateTimeFormat('zh-CN', {
        month: 'long',
        weekday: 'long',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date(1700000000000)),
    );
    expect(localePatch.xAxis[0].axisLabel.formatter(1700000000000)).not.toBe(beforeLocaleChangeLabel);
    expect(localePatch.series.map((series) => series.name)).toEqual(['价格', '成交量', '买入', '卖出']);
    expect(localePatch.series.every((series) => series.data === undefined)).toBe(true);
    expect((chartState.option.series as Array<{ data?: unknown[] }> | undefined)?.map((series) => series.data)).toEqual(
      beforeLocaleChangeSeriesData,
    );
    expect(chartState.option.dataZoom).toEqual(beforeLocaleChangeZoom);
    expect(chartState.option.legend).toMatchObject({
      selected: {
        价格: true,
        成交量: false,
        买入: true,
        卖出: true,
      },
    });

    wrapper.unmount();
  });
});
