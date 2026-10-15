import { defineComponent, h, type Ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';

const marketMocks = vi.hoisted(() => ({
  fetchKlines: vi.fn(),
  fetchTickers: vi.fn(),
}));

vi.mock('@/services/market', () => ({
  fetchKlines: (...args: unknown[]) => marketMocks.fetchKlines(...args),
  fetchTickers: (...args: unknown[]) => marketMocks.fetchTickers(...args),
}));

vi.mock('@/components/charts/Candlestick.vue', () => ({
  default: defineComponent({
    name: 'Candlestick',
    inheritAttrs: false,
    props: ['klines', 'symbol', 'timeframe', 'height'],
    setup(props, { attrs }) {
      return () => h('candlestick-stub', {
        ...attrs,
        'data-klines': (props.klines as unknown[]).length,
        'data-symbol': props.symbol,
        'data-timeframe': props.timeframe,
      });
    },
  }),
}));

const localeState = vi.hoisted(() => ({
  locale: null as Ref<string> | null,
}));

const Market = (await import('./Market.vue')).default;

const MarketWithLocale = defineComponent({
  name: 'MarketWithLocale',
  setup() {
    const { locale } = useI18n({ useScope: 'global' });
    localeState.locale = locale;

    return () => h(Market);
  },
});

function formatRangeSummary(locale: 'en' | 'zh-CN', startTime: number, endTime: number): string {
  const start = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(startTime));
  const end = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(endTime));
  return locale === 'zh-CN' ? `从 ${start} 到 ${end}` : `From ${start} to ${end}`;
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('Market view', () => {
  beforeEach(() => {
    marketMocks.fetchKlines.mockReset();
    marketMocks.fetchTickers.mockReset();
  });

  it('keeps a custom symbol when market type changes and resets fallback symbols', async () => {
    marketMocks.fetchTickers.mockResolvedValue([]);
    marketMocks.fetchKlines.mockResolvedValue([]);
    const wrapper = await mount(Market, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
      },
    });

    const symbolSelect = wrapper.getByTestId('market-symbol-select');
    await wrapper.invoke(symbolSelect, 'onUpdate:modelValue', 'DOGE-USDT');
    await wrapper.flush();

    const marketTypeSelect = wrapper.getByTestId('market-market-type-select');
    await wrapper.invoke(marketTypeSelect, 'onUpdate:modelValue', 'swap');
    await wrapper.flush();

    expect(wrapper.getByTestId('market-symbol-select').props.modelValue).toBe('DOGE-USDT');

    await wrapper.invoke(symbolSelect, 'onUpdate:modelValue', 'BTC-USDT-SWAP');
    await wrapper.flush();
    await wrapper.invoke(marketTypeSelect, 'onUpdate:modelValue', 'future');
    await wrapper.flush();

    expect(wrapper.getByTestId('market-symbol-select').props.modelValue).toBe('BTC-USDT-260626');
    wrapper.unmount();
  });

  it('sets the first fallback symbol and refreshes when an empty symbol changes market type', async () => {
    marketMocks.fetchTickers.mockResolvedValue([]);
    marketMocks.fetchKlines.mockResolvedValue([]);
    const wrapper = await mount(Market, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
      },
    });
    await wrapper.flush();
    marketMocks.fetchTickers.mockClear();
    marketMocks.fetchKlines.mockClear();

    const symbolSelect = wrapper.getByTestId('market-symbol-select');
    await wrapper.invoke(symbolSelect, 'onUpdate:modelValue', '');
    await wrapper.flush();

    const marketTypeSelect = wrapper.getByTestId('market-market-type-select');
    await wrapper.invoke(marketTypeSelect, 'onUpdate:modelValue', 'swap');
    await wrapper.flush();

    expect(wrapper.getByTestId('market-symbol-select').props.modelValue).toBe('BTC-USDT-SWAP');
    expect(marketMocks.fetchTickers).toHaveBeenCalledWith('swap');
    expect(marketMocks.fetchKlines).toHaveBeenCalledWith({
      symbol: 'BTC-USDT-SWAP',
      timeframe: '1h',
      limit: 100,
      market_type: 'swap',
    });
    wrapper.unmount();
  });

  it('keeps the latest ticker results while market type changes rapidly', async () => {
    const spotTickers = createDeferred<any[]>();
    const swapTickers = createDeferred<any[]>();
    const futureTickers = createDeferred<any[]>();
    marketMocks.fetchTickers
      .mockImplementationOnce(() => spotTickers.promise)
      .mockImplementationOnce(() => swapTickers.promise)
      .mockImplementationOnce(() => futureTickers.promise);
    marketMocks.fetchKlines.mockResolvedValue([]);

    const wrapper = await mount(Market, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
      },
    });

    const marketTypeSelect = wrapper.getByTestId('market-market-type-select');
    await wrapper.invoke(marketTypeSelect, 'onUpdate:modelValue', 'swap');
    await wrapper.invoke(marketTypeSelect, 'onUpdate:modelValue', 'future');

    expect(wrapper.getByTestId('market-market-type-select').props.loading).toBe(true);

    swapTickers.resolve([{ symbol: 'ETH-USDT-SWAP' }]);
    await wrapper.flush();
    expect(wrapper.getByTestId('market-market-type-select').props.loading).toBe(true);

    futureTickers.resolve([{ symbol: 'XRP-USDT-260626' }]);
    await wrapper.flush();
    expect(wrapper.getByTestId('market-market-type-select').props.loading).toBe(false);
    expect(wrapper.getByTestId('market-symbol-select').props.modelValue).toBe('BTC-USDT-260626');
    expect(wrapper.findAll((node) => node.type === 'el-option' && node.props.label === 'XRP-USDT-260626')).toHaveLength(1);

    spotTickers.resolve([{ symbol: 'DOGE-USDT' }]);
    await wrapper.flush();

    expect(wrapper.getByTestId('market-symbol-select').props.modelValue).toBe('BTC-USDT-260626');
    expect(wrapper.getByTestId('market-market-type-select').props.loading).toBe(false);
    expect(wrapper.findAll((node) => node.type === 'el-option' && node.props.label === 'XRP-USDT-260626')).toHaveLength(1);
    expect(wrapper.findAll((node) => node.type === 'el-option' && node.props.label === 'DOGE-USDT')).toHaveLength(0);
    wrapper.unmount();
  });

  it('keeps the last successful chart query when inputs change before submit', async () => {
    marketMocks.fetchTickers.mockResolvedValue([]);
    marketMocks.fetchKlines.mockResolvedValue([
      { symbol: 'BTC-USDT', timeframe: '1h', timestamp: 1, open: 1, high: 2, low: 0.5, close: 1.5, volume: 5 },
    ]);

    const wrapper = await mount(Market, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
      },
    });
    await wrapper.flush();

    expect(wrapper.getByTestId('market-chart').props['data-symbol']).toBe('BTC-USDT');
    expect(wrapper.getByTestId('market-chart').props['data-timeframe']).toBe('1h');

    const symbolSelect = wrapper.getByTestId('market-symbol-select');
    await wrapper.invoke(symbolSelect, 'onUpdate:modelValue', 'ETH-USDT');

    expect(wrapper.getByTestId('market-symbol-select').props.modelValue).toBe('ETH-USDT');
    expect(wrapper.getByTestId('market-chart').props['data-symbol']).toBe('BTC-USDT');
    expect(wrapper.getByTestId('market-chart').props['data-timeframe']).toBe('1h');
    wrapper.unmount();
  });

  it('ignores stale kline responses after a newer request resolves', async () => {
    const initial = Promise.resolve([]);
    const first = createDeferred<any[]>();
    const second = createDeferred<any[]>();
    marketMocks.fetchTickers.mockResolvedValue([]);
    marketMocks.fetchKlines
      .mockImplementationOnce(() => initial)
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);

    const wrapper = await mount(Market, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
      },
    });

    const form = wrapper.getByTestId('market-query-form');
    await wrapper.trigger(form, 'submit');
    await wrapper.trigger(form, 'submit');

    second.resolve([{ symbol: 'BTC-USDT', timeframe: '1h', timestamp: 2, open: 2, high: 3, low: 1, close: 2.5, volume: 10 }]);
    await wrapper.flush();
    first.resolve([{ symbol: 'BTC-USDT', timeframe: '1h', timestamp: 1, open: 1, high: 2, low: 0.5, close: 1.5, volume: 5 }]);
    await wrapper.flush();

    expect(wrapper.getByTestId('market-chart').props['data-klines']).toBe(1);
    expect(wrapper.getByTestId('market-chart').props['data-symbol']).toBe('BTC-USDT');
    wrapper.unmount();
  });

  it('keeps the last successful candles and query summary when a later query fails', async () => {
    marketMocks.fetchTickers.mockResolvedValue([]);
    marketMocks.fetchKlines
      .mockResolvedValueOnce([{ symbol: 'BTC-USDT', timeframe: '1h', timestamp: 1, open: 1, high: 2, low: 0.5, close: 1.5, volume: 5 }])
      .mockRejectedValueOnce(new Error('failed'));

    const wrapper = await mount(Market, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
      },
    });
    await wrapper.flush();

    expect(wrapper.getByTestId('market-chart').props['data-symbol']).toBe('BTC-USDT');
    expect(textContent(wrapper.getByTestId('market-active-query'))).toContain('BTC-USDT');
    expect(textContent(wrapper.getByTestId('market-query-status')).includes('BTC-USDT')).toBe(true);

    const symbolSelect = wrapper.getByTestId('market-symbol-select');
    await wrapper.invoke(symbolSelect, 'onUpdate:modelValue', 'ETH-USDT');
    await wrapper.trigger(wrapper.getByTestId('market-query-form'), 'submit');
    await wrapper.flush();

    expect(wrapper.getByTestId('market-chart').props['data-symbol']).toBe('BTC-USDT');
    expect(wrapper.getByTestId('market-chart').props['data-klines']).toBe(1);
    expect(wrapper.text()).toContain('Stale data');
    expect(wrapper.text()).toContain('Failed to load market data. Please try again.');
    expect(textContent(wrapper.getByTestId('market-active-query'))).toContain('BTC-USDT');
    expect(textContent(wrapper.getByTestId('market-query-status')).includes('BTC-USDT')).toBe(true);
    expect(textContent(wrapper.getByTestId('market-active-query'))).not.toContain('ETH-USDT');
    expect(textContent(wrapper.getByTestId('market-query-status'))).not.toContain('ETH-USDT');
    wrapper.unmount();
  });

  it('shows a hard error when the first kline request fails', async () => {
    marketMocks.fetchTickers.mockResolvedValue([]);
    marketMocks.fetchKlines.mockRejectedValue(new Error('failed'));

    const wrapper = await mount(Market, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
      },
    });
    await wrapper.flush();

    expect(wrapper.text()).toContain('Failed to load market data. Please try again.');
    expect(wrapper.text()).not.toContain('Stale data');
    expect(wrapper.findAll((node) => node.props['data-testid'] === 'market-chart')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props['data-testid'] === 'market-query-status')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props['data-testid'] === 'market-active-query')).toHaveLength(0);
    wrapper.unmount();
  });

  it('reformats the active query range without refetching when locale changes', async () => {
    marketMocks.fetchTickers.mockResolvedValue([]);
    marketMocks.fetchKlines.mockResolvedValue([]);

    const wrapper = await mount(MarketWithLocale, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
      },
    });
    await wrapper.flush();

    const startTime = new Date('2026-08-01T00:00:00.000Z');
    const endTime = new Date('2026-08-02T00:00:00.000Z');
    await wrapper.invoke(wrapper.getByTestId('market-start-time-picker'), 'onUpdate:modelValue', startTime);
    await wrapper.invoke(wrapper.getByTestId('market-end-time-picker'), 'onUpdate:modelValue', endTime);
    await wrapper.trigger(wrapper.getByTestId('market-query-form'), 'submit');
    await wrapper.flush();

    const activeQuery = wrapper.getByTestId('market-active-query');
    const status = wrapper.getByTestId('market-query-status');
    const expectedEnRange = formatRangeSummary('en', startTime.getTime(), endTime.getTime());

    expect(textContent(activeQuery)).toContain(expectedEnRange);
    expect(textContent(status)).toContain(expectedEnRange);
    expect(textContent(status)).toContain('Spot');

    const klineCallsBeforeLocaleChange = marketMocks.fetchKlines.mock.calls.length;
    const tickerCallsBeforeLocaleChange = marketMocks.fetchTickers.mock.calls.length;

    if (!localeState.locale) {
      throw new Error('Locale ref was not initialized');
    }
    localeState.locale.value = 'zh-CN';
    await wrapper.flush();

    expect(marketMocks.fetchKlines.mock.calls.length).toBe(klineCallsBeforeLocaleChange);
    expect(marketMocks.fetchTickers.mock.calls.length).toBe(tickerCallsBeforeLocaleChange);
    expect(textContent(activeQuery)).toContain(formatRangeSummary('zh-CN', startTime.getTime(), endTime.getTime()));
    expect(textContent(status)).toContain(formatRangeSummary('zh-CN', startTime.getTime(), endTime.getTime()));
    expect(textContent(status)).toContain('现货');

    wrapper.unmount();
  });

  it('keeps the last successful spot query visible until the swap refresh succeeds', async () => {
    const swapKlines = createDeferred<any[]>();
    marketMocks.fetchTickers.mockResolvedValue([]);
    marketMocks.fetchKlines
      .mockResolvedValueOnce([{ symbol: 'BTC-USDT', timeframe: '1h', timestamp: 1, open: 1, high: 2, low: 0.5, close: 1.5, volume: 5 }])
      .mockImplementationOnce(() => swapKlines.promise);

    const wrapper = await mount(Market, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
      },
    });
    await wrapper.flush();

    expect(textContent(wrapper.getByTestId('market-active-query'))).toContain('Spot');
    expect(textContent(wrapper.getByTestId('market-query-status')).includes('BTC-USDT')).toBe(true);

    const marketTypeSelect = wrapper.getByTestId('market-market-type-select');
    await wrapper.invoke(marketTypeSelect, 'onUpdate:modelValue', 'swap');
    await wrapper.flush();

    expect(textContent(wrapper.getByTestId('market-active-query'))).toContain('Spot');
    expect(textContent(wrapper.getByTestId('market-query-status')).includes('BTC-USDT')).toBe(true);

    swapKlines.resolve([{ symbol: 'BTC-USDT-SWAP', timeframe: '1h', timestamp: 2, open: 2, high: 3, low: 1, close: 2.5, volume: 10 }]);
    await wrapper.flush();

    expect(textContent(wrapper.getByTestId('market-active-query'))).toContain('Swap');
    expect(textContent(wrapper.getByTestId('market-active-query'))).toContain('BTC-USDT-SWAP');
    expect(textContent(wrapper.getByTestId('market-query-status'))).toContain('BTC-USDT-SWAP');
    expect(wrapper.getByTestId('market-chart').props['data-symbol']).toBe('BTC-USDT-SWAP');
    wrapper.unmount();
  });
});
