import { defineComponent, h, reactive } from 'vue';
import { useI18n } from 'vue-i18n';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';
import type { TradeRecord } from '@/types/trades';

vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const marketApi = vi.hoisted(() => ({ fetchTickers: vi.fn() }));
const tradingApi = vi.hoisted(() => ({ submit: vi.fn() }));
vi.mock('@/services/market', () => ({ fetchTickers: marketApi.fetchTickers }));

const fetchTradesMock = vi.hoisted(() => ({
  fetchTrades: vi.fn(),
}));

vi.mock('@/services/trades', () => ({
  fetchTrades: fetchTradesMock.fetchTrades,
}));

vi.mock('@/services/trading', () => ({
  fetchTradingOrders: vi.fn().mockResolvedValue([]),
  submitManualOrder: tradingApi.submit,
  cancelTradingOrder: vi.fn(),
  amendTradingOrder: vi.fn(),
}));

vi.mock('@/components/trades/TradeFilters.vue', () => ({
  default: defineComponent({
    name: 'TradeFilters',
    props: ['modelValue', 'strategyOptions', 'symbolOptions', 'disabled'],
    setup(props) {
      return () => h('trade-filters-stub', {
        'data-strategy-options': (props.strategyOptions as string[]).join(','),
        'data-symbol-options': (props.symbolOptions as string[]).join(','),
        'data-disabled': props.disabled,
      }, 'filters');
    },
  }),
}));

vi.mock('@/components/trades/TradeSummary.vue', () => ({
  default: defineComponent({
    name: 'TradeSummary',
    props: ['summary'],
    setup(props) {
      return () => h('trade-summary-stub', {
        'data-total-trades': props.summary.totalTrades,
        'data-total-notional': props.summary.totalNotional ?? '',
        'data-total-fees': props.summary.totalFees ?? '',
        'data-positive-pnl': props.summary.positivePnlCount ?? '',
        'data-negative-pnl': props.summary.negativePnlCount ?? '',
      }, `Results ${props.summary.totalTrades}`);
    },
  }),
}));

vi.mock('@/components/trades/TradesTable.vue', () => ({
  default: defineComponent({
    name: 'TradesTable',
    props: ['trades', 'loading', 'emptyDescription'],
    setup(props) {
      return () => h('trade-table-stub', {
        'data-count': (props.trades as TradeRecord[]).length,
        'data-loading': props.loading,
        'data-empty-description': props.emptyDescription,
      }, (props.trades as TradeRecord[]).map((trade) => trade.symbol).join(','));
    },
  }),
}));

const testContext = { available: true, account_scope_id: 'scope-A', environment: 'sandbox' as const, exchange: 'okx' as const, market_type: 'all', identity_verified: false as const, execution_ready: false as const, reason: null };
const authority = reactive({
  epoch: 1,
  context: { ...testContext } as typeof testContext | null,
  loading: false,
  error: null,
  ensure: async () => authority.context,
});
vi.mock('@/stores/accountContext', () => ({ useAccountContextStore: () => authority }));

const TradesView = (await import('./Trades.vue')).default;

const firstTrades: TradeRecord[] = [
  {
    id: 1,
    strategy: 'ma_cross',
    symbol: 'BTC-USDT',
    side: 'buy',
    amount: 0.1,
    price: 60000,
    fee: 1.2,
    timestamp: 1700000000000,
  },
  {
    id: 2,
    strategy: 'rsi_mean_reversion',
    symbol: 'ETH-USDT',
    side: 'sell',
    amount: 2,
    price: 3000,
    fee: 0.8,
    timestamp: 1700003600000,
  },
];

const ticketWrappers: Awaited<ReturnType<typeof mount>>[] = [];
async function mountTicket() {
  const wrapper = await mount(TradesView, { locale: 'en', components });
  ticketWrappers.push(wrapper);
  return wrapper;
}

const components = {
  ElSelect: defineHostComponent('el-select'),
  ElOption: defineHostComponent('el-option'),
  ElButton: defineHostComponent('el-button'),
};

describe('Trades view', () => {
  afterEach(() => { ticketWrappers.splice(0).forEach((wrapper) => wrapper.unmount()); vi.unstubAllGlobals(); });
  it('clears the ticket instrument on market change and submits the explicit selected market', async () => {
    const wrapper = await mountTicket();
    await wrapper.flush();
    await wrapper.invoke(wrapper.getByTestId('manual-market-type'), 'onUpdate:modelValue', 'swap');
    expect(wrapper.getByTestId('manual-symbol').props.modelValue).toBe('');
    expect(marketApi.fetchTickers).toHaveBeenLastCalledWith('swap');
    await wrapper.invoke(wrapper.getByTestId('manual-symbol'), 'onUpdate:modelValue', 'ETH/USDT:USDT');
    await wrapper.invoke(wrapper.getByTestId('manual-price'), 'onUpdate:modelValue', '3000');
    await wrapper.trigger(wrapper.find((node) => node.type === 'form'), 'submit');
    expect(tradingApi.submit).toHaveBeenCalledWith({
      symbol: 'ETH/USDT:USDT', market_type: 'swap', side: 'buy', order_type: 'limit', amount: 0.0001, price: 3000,
    });
    wrapper.unmount();
  });

  it('rejects a typed symbol from another market before submitting', async () => {
    const wrapper = await mountTicket();
    await wrapper.invoke(wrapper.getByTestId('manual-symbol'), 'onUpdate:modelValue', 'BTC-USDT-SWAP');
    await wrapper.invoke(wrapper.getByTestId('manual-price'), 'onUpdate:modelValue', '3000');
    await wrapper.trigger(wrapper.find((node) => node.type === 'form'), 'submit');
    expect(tradingApi.submit).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('keeps only the newest market instruments when ticker requests arrive out of order', async () => {
    let resolveOld!: (rows: Array<{ symbol: string }>) => void;
    marketApi.fetchTickers.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce([{ symbol: 'BTC-USD-271231-100000-C' }, { symbol: 'ETH-USDT' }]);
    const wrapper = await mountTicket();
    await wrapper.invoke(wrapper.getByTestId('manual-market-type'), 'onUpdate:modelValue', 'option');
    await wrapper.flush();
    resolveOld([{ symbol: 'OLD-USDT' }]);
    await wrapper.flush();
    const select = wrapper.getByTestId('manual-symbol');
    const symbols = wrapper.findAll((node) => node.type === 'el-option' && node.parent === select).map((node) => node.props.value);
    expect(symbols).toEqual(['BTC-USD-271231-100000-C']);
    wrapper.unmount();
  });
  beforeEach(() => {
    vi.stubGlobal('Document', class {});
    vi.stubGlobal('ShadowRoot', class {});
    fetchTradesMock.fetchTrades.mockReset().mockResolvedValue([]);
    tradingApi.submit.mockReset().mockResolvedValue({});
    marketApi.fetchTickers.mockReset().mockResolvedValue([]);
    authority.epoch = 1;
    authority.context = { ...testContext };
    authority.loading = false;
  });

  it('keeps the loaded trades visible when a refresh fails and shows stale data state', async () => {
    fetchTradesMock.fetchTrades
      .mockResolvedValueOnce(firstTrades)
      .mockRejectedValueOnce(new Error('refresh failed'));

    const wrapper = await mount(TradesView, {
      locale: 'en',
      components,
    });

    await wrapper.flush();

    expect(fetchTradesMock.fetchTrades).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain('Trades');
    expect(wrapper.text()).toContain('Trade Summary');
    expect(wrapper.text()).toContain('Trade History');

    const summary = wrapper.find((node) => node.type === 'trade-summary-stub');
    const table = wrapper.find((node) => node.type === 'trade-table-stub');
    expect(summary.props['data-total-trades']).toBe(2);
    expect(textContent(table)).toBe('BTC-USDT,ETH-USDT');
    expect(table.props['data-count']).toBe(2);

    const refreshButton = wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'Refresh');
    await wrapper.invoke(refreshButton, 'onClick', {});
    await wrapper.flush();

    expect(fetchTradesMock.fetchTrades).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain('Stale data');
    expect(wrapper.text()).toContain('Failed to load trades');
    expect(textContent(wrapper.find((node) => node.type === 'trade-table-stub'))).toBe('BTC-USDT,ETH-USDT');
    expect(wrapper.find((node) => node.type === 'trade-table-stub').props['data-count']).toBe(2);
    expect(wrapper.text()).not.toContain('SOL-USDT');

    wrapper.unmount();
  });

  it('keeps an empty successful result as stale when a refresh fails', async () => {
    fetchTradesMock.fetchTrades
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error('refresh failed'));

    const wrapper = await mount(TradesView, {
      locale: 'en',
      components,
    });

    await wrapper.flush();

    expect(fetchTradesMock.fetchTrades).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain('No trade history yet.');
    expect(wrapper.findAll((node) => node.props.role === 'alert')).toHaveLength(0);

    const refreshButton = wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'Refresh');
    await wrapper.invoke(refreshButton, 'onClick', {});
    await wrapper.flush();

    expect(fetchTradesMock.fetchTrades).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain('Stale data');
    expect(wrapper.text()).toContain('Failed to load trades');
    expect(wrapper.text()).toContain('Retry');
    expect(wrapper.text()).toContain('No trade history yet.');
    expect(wrapper.findAll((node) => node.props.role === 'alert')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.type === 'trade-table-stub')).toHaveLength(0);

    wrapper.unmount();
  });

  it('recovers from a failed initial fetch through the retry action', async () => {
    fetchTradesMock.fetchTrades
      .mockRejectedValueOnce(new Error('initial load failed'))
      .mockResolvedValueOnce(firstTrades);

    const wrapper = await mount(TradesView, {
      locale: 'en',
      components,
    });

    await wrapper.flush();

    expect(fetchTradesMock.fetchTrades).toHaveBeenCalledTimes(1);
    const errorState = wrapper.find((node) => node.props.role === 'alert');
    expect(textContent(errorState)).toContain('Error');
    expect(textContent(errorState)).toContain('Failed to load trades');
    expect(wrapper.findAll((node) => node.type === 'trade-table-stub')).toHaveLength(0);

    const retryButton = wrapper.find((node) => (
      node.type === 'button'
      && String(node.props.class).includes('data-state__retry')
    ));
    expect(retryButton.props['aria-label']).toBe('Retry');
    expect(textContent(retryButton)).toBe('Retry');

    await wrapper.trigger(retryButton, 'click');
    await wrapper.flush();

    expect(fetchTradesMock.fetchTrades).toHaveBeenCalledTimes(2);
    expect(wrapper.findAll((node) => node.props.role === 'alert')).toHaveLength(0);
    expect(wrapper.findAll((node) => String(node.props.class).includes('data-state__retry'))).toHaveLength(0);
    expect(wrapper.text()).not.toContain('Failed to load trades');
    expect(wrapper.text()).not.toContain('Stale data');

    const table = wrapper.find((node) => node.type === 'trade-table-stub');
    expect(textContent(table)).toBe('BTC-USDT,ETH-USDT');
    expect(table.props['data-count']).toBe(2);

    wrapper.unmount();
  });

  it('keeps the latest trades result when overlapping requests resolve out of order', async () => {
    let resolveInitial: ((value: TradeRecord[]) => void) | undefined;
    let resolveRefresh: ((value: TradeRecord[]) => void) | undefined;

    fetchTradesMock.fetchTrades
      .mockReturnValueOnce(new Promise<TradeRecord[]>((resolve) => {
        resolveInitial = resolve;
      }))
      .mockReturnValueOnce(new Promise<TradeRecord[]>((resolve) => {
        resolveRefresh = resolve;
      }));

    const wrapper = await mount(TradesView, {
      locale: 'en',
      components,
    });

    expect(wrapper.text()).toContain('Loading');

    const refreshButton = wrapper.find((node) => node.type === 'el-button' && textContent(node) === 'Refresh');
    (refreshButton.props.onClick as ((event: unknown) => void) | undefined)?.({});

    expect(fetchTradesMock.fetchTrades).toHaveBeenCalledTimes(2);

    resolveInitial?.(firstTrades);
    await wrapper.flush();

    expect(wrapper.text()).toContain('Loading');
    expect(wrapper.findAll((node) => node.type === 'trade-summary-stub')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.type === 'trade-table-stub')).toHaveLength(0);

    const latestTrades: TradeRecord[] = [
      {
        ...firstTrades[0],
        id: 99,
        strategy: 'trend_follow',
        symbol: 'SOL-USDT',
      },
    ];
    resolveRefresh?.(latestTrades);
    await wrapper.flush();

    expect(wrapper.text()).not.toContain('Loading');
    expect(wrapper.text()).not.toContain('Failed to load trades');
    const table = wrapper.find((node) => node.type === 'trade-table-stub');
    expect(textContent(table)).toBe('SOL-USDT');
    expect(table.props['data-count']).toBe(1);

    wrapper.unmount();
  });

  it.each(['success', 'error'])('invalidates old account trade %s and finally while A to B to A is pending', async (outcome) => {
    let finishOld!: (value: TradeRecord[]) => void;
    let failOld!: (error: Error) => void;
    let finishCurrent!: (value: TradeRecord[]) => void;
    fetchTradesMock.fetchTrades
      .mockReturnValueOnce(new Promise((resolve, reject) => {
        finishOld = resolve;
        failOld = reject;
      }))
      .mockReturnValueOnce(new Promise((resolve) => {
        finishCurrent = resolve;
      }));
    const wrapper = await mount(TradesView, { locale: 'en', components });
    authority.context = null;
    authority.epoch += 1;
    authority.epoch += 1;
    authority.context = { ...testContext };
    await wrapper.flush();
    expect(fetchTradesMock.fetchTrades).toHaveBeenLastCalledWith(undefined, 'scope-A');
    if (outcome === 'success') {
      finishOld(firstTrades);
    } else {
      failOld(new Error('old account error'));
    }
    await wrapper.flush();
    expect(wrapper.text()).toContain('Loading');
    expect(wrapper.text()).not.toContain('Failed to load trades');
    expect(wrapper.text()).not.toContain('BTC-USDT');
    finishCurrent([{ ...firstTrades[0], symbol: 'CURRENT' }]);
    await wrapper.flush();
    expect(wrapper.text()).toContain('CURRENT');
    wrapper.unmount();
  });

  it('re-renders the load error message when the locale changes', async () => {
    let localeRef: { value: string } | undefined;

    fetchTradesMock.fetchTrades.mockRejectedValueOnce(new Error('initial load failed'));

    const Harness = defineComponent({
      name: 'TradesLocaleHarness',
      setup() {
        const { locale } = useI18n();
        localeRef = locale;
        return () => h(TradesView);
      },
    });

    const wrapper = await mount(Harness, {
      locale: 'en',
      components,
    });

    await wrapper.flush();

    const errorState = wrapper.find((node) => node.props.role === 'alert');
    expect(textContent(errorState)).toContain('Failed to load trades');

    localeRef!.value = 'zh-CN';
    await wrapper.flush();

    const localizedErrorState = wrapper.find((node) => node.props.role === 'alert');
    expect(textContent(localizedErrorState)).toContain('交易记录加载失败');

    wrapper.unmount();
  });

  it('shows a blocking loading state before the first successful fetch', async () => {
    let resolveTrades: ((value: TradeRecord[]) => void) | undefined;
    fetchTradesMock.fetchTrades.mockReturnValueOnce(new Promise<TradeRecord[]>((resolve) => {
      resolveTrades = resolve;
    }));

    const wrapper = await mount(TradesView, {
      locale: 'en',
      components,
    });

    expect(wrapper.text()).toContain('Loading');
    expect(wrapper.text()).not.toContain('filters');

    resolveTrades?.([]);
    await wrapper.flush();

    expect(wrapper.text()).toContain('No trade history yet.');
    expect(wrapper.text()).not.toContain('Loading');

    wrapper.unmount();
  });
});
