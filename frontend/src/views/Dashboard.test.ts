import { defineComponent, h, reactive, type Ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';

const storeMocks = vi.hoisted(() => ({
  dashboard: {} as Record<string, unknown>,
  strategies: {} as Record<string, unknown>,
}));

vi.mock('@/stores/dashboard', () => ({
  useDashboardStore: () => storeMocks.dashboard,
}));

vi.mock('@/stores/strategies', () => ({
  useStrategiesStore: () => storeMocks.strategies,
}));

vi.mock('@/components/dashboard/AccountOverview.vue', () => ({
  default: defineComponent({
    name: 'AccountOverview',
    props: ['assets', 'loading', 'error', 'accountError', 'stale'],
    inheritAttrs: false,
    setup(props, { attrs }) {
      return () => h('account-overview-stub', {
        ...attrs,
        'data-assets': (props.assets as unknown[]).length,
        'data-loading': props.loading,
        'data-error': (props.accountError ?? props.error) ?? '',
        'data-stale': props.stale,
      });
    },
  }),
}));

vi.mock('@/components/dashboard/StrategyPerformanceTable.vue', () => ({
  default: defineComponent({
    name: 'StrategyPerformanceTable',
    props: ['rows', 'loading', 'error', 'stale'],
    setup(props) {
      return () => h('strategy-performance-table-stub', {
        'data-rows': (props.rows as unknown[]).length,
        'data-loading': props.loading,
        'data-error': props.error ?? '',
        'data-stale': props.stale,
      });
    },
  }),
}));

vi.mock('@/components/dashboard/DashboardActivity.vue', () => ({
  default: defineComponent({
    name: 'DashboardActivity',
    props: ['recentOrders', 'positions', 'runtimeSummaries', 'runtimeErrors', 'websocketMessages', 'loading'],
    setup(props) {
      return () => h('dashboard-activity-stub', {
        'data-recent-orders': (props.recentOrders as unknown[]).length,
        'data-positions': (props.positions as unknown[]).length,
        'data-runtimes': (props.runtimeSummaries as unknown[]).length,
        'data-websocket-messages': (props.websocketMessages as unknown[]).length,
        'data-loading': props.loading,
      });
    },
  }),
}));

const localeState = vi.hoisted(() => ({
  locale: null as Ref<string> | null,
}));

const Dashboard = (await import('./Dashboard.vue')).default;

const DashboardWithLocale = defineComponent({
  name: 'DashboardWithLocale',
  setup() {
    const { locale } = useI18n({ useScope: 'global' });
    localeState.locale = locale;

    return () => h(Dashboard);
  },
});

function createStoreMocks() {
  storeMocks.dashboard = reactive({
    account: null,
    positions: [{ symbol: 'BTC-USDT', amount: 1 }],
    orders: Array.from({ length: 21 }, (_, index) => ({
      order_id: `order-${index}`,
      symbol: `BTC-${index}`,
      side: 'buy',
      type: 'limit',
      amount: 1,
      price: 100 + index,
      status: 'filled',
      timestamp: 1700000000000 + index,
    })),
    tickers: [],
    strategyPerformance: [],
    strategyPerformanceError: null,
    strategyPerformanceLoading: false,
    websocketConnected: false,
    websocketMessages: Array.from({ length: 6 }, (_, index) => ({
      type: `message-${index}`,
      received_at: 1700000001000 + index,
      payload: `payload-${index}`,
    })),
    loading: false,
    accountLoading: false,
    error: null,
    accountError: null,
    tickerError: null,
    lastUpdatedAt: null,
    loadInitialData: vi.fn().mockResolvedValue(undefined),
    refreshAccountOverview: vi.fn().mockResolvedValue(undefined),
    refreshStrategyPerformance: vi.fn().mockResolvedValue(undefined),
  });

  storeMocks.strategies = reactive({
    runtimeSummaries: [
      { name: 'alpha', status: 'running' },
      { name: 'beta', status: 'stopped' },
      { name: 'gamma', status: 'running' },
    ],
    errors: { beta: 'paused by operator' },
    activeStrategyCount: 2,
    loadingInitial: false,
    loadInitialData: vi.fn().mockResolvedValue(undefined),
  });
}

describe('Dashboard view', () => {
  beforeEach(() => {
    createStoreMocks();
  });

  it('renders the header status, six metrics, and capped recent dashboard activity', async () => {
    const wrapper = await mount(Dashboard, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElAlert: defineHostComponent('el-alert'),
        ElRow: defineHostComponent('el-row'),
        ElCol: defineHostComponent('el-col'),
      },
    });

    await wrapper.flush();

    expect(storeMocks.dashboard.loadInitialData).toHaveBeenCalledTimes(1);
    expect(storeMocks.strategies.loadInitialData).toHaveBeenCalledTimes(1);
    const refreshButton = wrapper.find((node) => node.type === 'el-button');
    await wrapper.trigger(refreshButton, 'click');
    expect(storeMocks.dashboard.loadInitialData).toHaveBeenCalledTimes(2);
    expect(storeMocks.strategies.loadInitialData).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain('Last updated: —');

    const metricValues = wrapper.findAll((node) => node.type === 'span' && String(node.props.class).split(' ').includes('metric-card__value'));
    expect(metricValues).toHaveLength(6);
    expect(textContent(metricValues[0])).toBe('—');
    expect(textContent(metricValues[1])).toBe('—');
    expect(textContent(metricValues[2])).toBe('—');
    expect(textContent(metricValues[3])).toBe('—');
    expect(textContent(metricValues[4])).toBe('—');
    expect(textContent(metricValues[5])).toBe('2');

    const badge = wrapper.find((node) => String(node.props.class).includes('status-badge'));
    expect(textContent(badge)).toContain('Disconnected');
    expect(wrapper.findAll((node) => node.type === 'svg')).not.toHaveLength(0);

    const activity = wrapper.find((node) => node.type === 'dashboard-activity-stub');
    expect(activity.props['data-recent-orders']).toBe(20);
    expect(activity.props['data-websocket-messages']).toBe(5);

    wrapper.unmount();
  });

  it('retries only the account overview data from the section retry', async () => {
    const wrapper = await mount(Dashboard, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElAlert: defineHostComponent('el-alert'),
        ElRow: defineHostComponent('el-row'),
        ElCol: defineHostComponent('el-col'),
      },
    });

    await wrapper.flush();

    const accountOverview = wrapper.find((node) => node.type === 'account-overview-stub');
    await wrapper.invoke(accountOverview, 'onRetry');

    expect(storeMocks.dashboard.refreshAccountOverview).toHaveBeenCalledTimes(1);
    expect(storeMocks.dashboard.loadInitialData).toHaveBeenCalledTimes(1);
    expect(storeMocks.strategies.loadInitialData).toHaveBeenCalledTimes(1);
    expect(storeMocks.dashboard.refreshStrategyPerformance).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it('reformats the last updated label when locale changes without refetching', async () => {
    storeMocks.dashboard.lastUpdatedAt = 1700000000000;

    const wrapper = await mount(DashboardWithLocale, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElAlert: defineHostComponent('el-alert'),
        ElRow: defineHostComponent('el-row'),
        ElCol: defineHostComponent('el-col'),
      },
    });

    await wrapper.flush();

    expect(wrapper.text()).toContain(`Last updated: ${new Date(1700000000000).toLocaleString('en-US')}`);

    const initialLoadCount = (storeMocks.dashboard.loadInitialData as ReturnType<typeof vi.fn>).mock.calls.length;
    if (!localeState.locale) {
      throw new Error('Locale ref was not initialized');
    }
    localeState.locale.value = 'zh-CN';
    await wrapper.flush();

    expect(wrapper.text()).toContain(`最后更新: ${new Date(1700000000000).toLocaleString('zh-CN')}`);
    expect((storeMocks.dashboard.loadInitialData as ReturnType<typeof vi.fn>).mock.calls.length).toBe(initialLoadCount);

    wrapper.unmount();
  });

  it('binds strategy performance retry and loading independently', async () => {
    storeMocks.dashboard.strategyPerformance = [
      {
        name: 'alpha',
        status: 'running',
        equity: 1000,
        return_pct: 0.05,
        realized_pnl: 25,
        unrealized_pnl: 5,
        position_notional: 100,
        open_positions: 1,
        closed_trade_count: 3,
        win_rate: 0.66,
        fees_paid: 1.25,
        order_count: 8,
        filled_order_count: 6,
        last_order_at: 1700000000000,
      },
    ];
    storeMocks.dashboard.strategyPerformanceLoading = true;

    const wrapper = await mount(Dashboard, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElAlert: defineHostComponent('el-alert'),
        ElRow: defineHostComponent('el-row'),
        ElCol: defineHostComponent('el-col'),
      },
    });

    await wrapper.flush();

    const strategyPerformanceTable = wrapper.find((node) => node.type === 'strategy-performance-table-stub');
    expect(strategyPerformanceTable.props['data-loading']).toBe(true);
    expect(strategyPerformanceTable.props['data-rows']).toBe(3);

    await wrapper.invoke(strategyPerformanceTable, 'onRetry');

    expect(storeMocks.dashboard.refreshStrategyPerformance).toHaveBeenCalledTimes(1);
    expect(storeMocks.dashboard.refreshAccountOverview).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it('keeps ticker errors inside the shared status area and preserves page order', async () => {
    storeMocks.dashboard.error = 'Dashboard refresh failed';
    storeMocks.dashboard.tickerError = 'Ticker refresh failed';

    const wrapper = await mount(Dashboard, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElAlert: defineHostComponent('el-alert'),
        ElRow: defineHostComponent('el-row'),
        ElCol: defineHostComponent('el-col'),
      },
    });

    await wrapper.flush();

    const section = wrapper.find((node) => node.type === 'section' && String(node.props.class).includes('dashboard-view'));
    const directChildren = section.children;

    expect(directChildren).toHaveLength(6);
    expect(String(directChildren[0].props.class)).toContain('app-page-header');
    expect(String(directChildren[1].props.class)).toContain('dashboard-view__alerts');
    expect(directChildren[2].type).toBe('el-row');
    expect(String(directChildren[2].props.class)).toContain('dashboard-view__metrics');
    expect(String(directChildren[3].props.class)).toContain('dashboard-view__section');
    expect(String(directChildren[4].props.class)).toContain('dashboard-view__section');
    expect(String(directChildren[5].props.class)).toContain('dashboard-view__section');

    const alerts = directChildren[1].children.filter((child) => child.type === 'el-alert');
    expect(alerts).toHaveLength(2);
    expect(alerts[0].props.title).toBe('Stale data');
    expect(alerts[0].props.description).toBe('Dashboard refresh failed');
    expect(alerts[1].props.title).toBe('Failed to load market tickers');
    expect(alerts[1].props.description).toBe('Ticker refresh failed');
    expect(section.children.some((child) => child.type === 'el-alert')).toBe(false);

    expect(directChildren[3].children.some((child) => child.type === 'account-overview-stub')).toBe(true);
    expect(directChildren[4].children.some((child) => child.type === 'strategy-performance-table-stub')).toBe(true);
    expect(directChildren[5].children.some((child) => child.type === 'dashboard-activity-stub')).toBe(true);

    wrapper.unmount();
  });

  it('keeps existing account data stale when a shared dashboard refresh fails', async () => {
    storeMocks.dashboard.account = {
      equity: 1000,
      cash_balance: 950,
      realized_pnl: 50,
      unrealized_pnl: 0,
      daily_pnl: 12.5,
      fees_paid: 2.5,
      assets: [{ ccy: 'USDT', cash_bal: 100, eq: 100, eq_utd: 100, avail_bal: 90, upl: 0 }],
    };
    storeMocks.dashboard.error = 'Dashboard refresh failed';

    const wrapper = await mount(Dashboard, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElAlert: defineHostComponent('el-alert'),
        ElRow: defineHostComponent('el-row'),
        ElCol: defineHostComponent('el-col'),
      },
    });

    await wrapper.flush();

    const accountOverview = wrapper.find((node) => node.type === 'account-overview-stub');
    expect(accountOverview.props['data-assets']).toBe(1);
    expect(accountOverview.props['data-error']).toBe('Dashboard refresh failed');
    expect(accountOverview.props['data-stale']).toBe(true);

    const alerts = wrapper.findAll((node) => node.type === 'el-alert');
    expect(alerts).toHaveLength(1);
    expect(alerts[0].props.title).toBe('Stale data');
    expect(alerts[0].props.description).toBe('Dashboard refresh failed');

    wrapper.unmount();
  });

  it('shows a hard error when account data is missing and the shared dashboard refresh fails', async () => {
    storeMocks.dashboard.account = null;
    storeMocks.dashboard.positions = [];
    storeMocks.dashboard.orders = [];
    storeMocks.dashboard.tickers = [];
    storeMocks.dashboard.strategyPerformance = [];
    storeMocks.dashboard.websocketMessages = [];
    storeMocks.strategies.runtimeSummaries = [];
    storeMocks.dashboard.error = 'Dashboard refresh failed';

    const wrapper = await mount(Dashboard, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElAlert: defineHostComponent('el-alert'),
        ElRow: defineHostComponent('el-row'),
        ElCol: defineHostComponent('el-col'),
      },
    });

    await wrapper.flush();

    const accountOverview = wrapper.find((node) => node.type === 'account-overview-stub');
    expect(accountOverview.props['data-assets']).toBe(0);
    expect(accountOverview.props['data-error']).toBe('Dashboard refresh failed');
    expect(accountOverview.props['data-stale']).toBe(false);

    const alerts = wrapper.findAll((node) => node.type === 'el-alert');
    expect(alerts).toHaveLength(1);
    expect(alerts[0].props.title).toBe('Dashboard refresh failed');
    expect(alerts[0].props.description).toBeUndefined();

    wrapper.unmount();
  });
});
