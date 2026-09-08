import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

import { fetchStrategyPerformance } from '@/services/trading';
import { useDashboardStore } from './dashboard';

vi.mock('@/services/trading', () => ({
  fetchStrategyPerformance: vi.fn(),
}));

const mockedFetchStrategyPerformance = vi.mocked(fetchStrategyPerformance);

const jsonResponse = (data: unknown) =>
  Promise.resolve({
    ok: true,
    json: () => Promise.resolve(data),
  } as Response);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const usdtAsset = {
  ccy: 'USDT',
  cash_bal: 100,
  eq: 100,
  eq_utd: 100,
  avail_bal: 90,
  upl: 0,
};

describe('dashboard store', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    vi.restoreAllMocks();
    mockedFetchStrategyPerformance.mockReset();
    mockedFetchStrategyPerformance.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('loads core dashboard data when public market tickers fail', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === '/api/market/tickers') {
        return Promise.resolve({ ok: false, status: 503 } as Response);
      }

      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 950,
          equity: 1000,
          realized_pnl: 50,
          unrealized_pnl: 0,
          daily_pnl: 12.5,
          fees_paid: 2.5,
        },
        '/api/trading/positions': [{ symbol: 'BTC-USDT', amount: 1 }],
        '/api/trading/orders': [{ order_id: 'order-1', symbol: 'BTC-USDT' }],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();

    expect(dashboard.account).toEqual({
      cash_balance: 950,
      equity: 1000,
      realized_pnl: 50,
      unrealized_pnl: 0,
      daily_pnl: 12.5,
      fees_paid: 2.5,
    });
    expect(dashboard.positions).toEqual([{ symbol: 'BTC-USDT', amount: 1 }]);
    expect(dashboard.orders).toEqual([{ order_id: 'order-1', symbol: 'BTC-USDT' }]);
    expect(fetchMock).not.toHaveBeenCalledWith('/api/strategies');
    expect(dashboard.tickers).toEqual([]);
    expect(dashboard.error).toBeNull();
    expect(dashboard.tickerError).toBe('Request failed: 503');
    expect(dashboard.lastUpdatedAt).toEqual(expect.any(Number));
  });

  it('does not let an older core load overwrite a newer initial load', async () => {
    let resolveFirstAccount!: (account: unknown) => void;
    let resolveSecondAccount!: (account: unknown) => void;
    const firstAccount = new Promise<unknown>((resolve) => {
      resolveFirstAccount = resolve;
    });
    const secondAccount = new Promise<unknown>((resolve) => {
      resolveSecondAccount = resolve;
    });
    let accountCall = 0;
    const fetchMock = vi.fn((url: string) => {
      if (url === '/api/trading/account') {
        accountCall += 1;
        return accountCall === 1 ? firstAccount.then(jsonResponse) : secondAccount.then(jsonResponse);
      }

      const responses: Record<string, unknown> = {
        '/api/trading/positions': [],
        '/api/trading/orders': [],
        '/api/market/tickers': [{ symbol: 'BTC-USDT', last: 68000 }],
      };
      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    const firstLoad = dashboard.loadInitialData();
    const secondLoad = dashboard.loadInitialData();

    resolveSecondAccount({
      cash_balance: 1900,
      equity: 2000,
      realized_pnl: 100,
      unrealized_pnl: 0,
      daily_pnl: 25,
      fees_paid: 5,
    });
    await secondLoad;
    expect(dashboard.account?.equity).toBe(2000);

    resolveFirstAccount({
      cash_balance: 900,
      equity: 1000,
      realized_pnl: 0,
      unrealized_pnl: 0,
      daily_pnl: 0,
      fees_paid: 0,
    });
    await firstLoad;

    expect(dashboard.account?.equity).toBe(2000);
    expect(dashboard.tickers).toEqual([{ symbol: 'BTC-USDT', last: 68000 }]);
    expect(dashboard.error).toBeNull();
  });

  it('refreshes only the account snapshot when the account overview retries', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url !== '/api/trading/account') {
        throw new Error(`Unexpected fetch: ${url}`);
      }

      return jsonResponse({
        cash_balance: 1500,
        equity: 1750,
        realized_pnl: 250,
        unrealized_pnl: 12.5,
        daily_pnl: 18,
        fees_paid: 3,
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    dashboard.positions = [{ symbol: 'BTC-USDT', amount: 1 }];
    dashboard.orders = [{ order_id: 'order-1', symbol: 'BTC-USDT' }];
    dashboard.tickers = [{ symbol: 'BTC-USDT', last: 68000 }];
    dashboard.strategyPerformance = [{ strategy: 'existing' }] as never[];
    dashboard.error = 'Dashboard refresh failed';

    await dashboard.refreshAccountOverview();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/trading/account');
    expect(mockedFetchStrategyPerformance).not.toHaveBeenCalled();
    expect(dashboard.account).toEqual({
      cash_balance: 1500,
      equity: 1750,
      realized_pnl: 250,
      unrealized_pnl: 12.5,
      daily_pnl: 18,
      fees_paid: 3,
    });
    expect(dashboard.positions).toEqual([{ symbol: 'BTC-USDT', amount: 1 }]);
    expect(dashboard.orders).toEqual([{ order_id: 'order-1', symbol: 'BTC-USDT' }]);
    expect(dashboard.tickers).toEqual([{ symbol: 'BTC-USDT', last: 68000 }]);
    expect(dashboard.strategyPerformance).toEqual([{ strategy: 'existing' }]);
    expect(dashboard.error).toBe('Dashboard refresh failed');
    expect(dashboard.accountError).toBeNull();
    expect(dashboard.loading).toBe(false);
  });

  it('keeps shared loading, error, and last update untouched during account-only refresh', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url !== '/api/trading/account') {
        throw new Error(`Unexpected fetch: ${url}`);
      }

      return jsonResponse({
        cash_balance: 1500,
        equity: 1750,
        realized_pnl: 250,
        unrealized_pnl: 12.5,
        daily_pnl: 18,
        fees_paid: 3,
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    dashboard.loading = true;
    dashboard.error = 'Dashboard refresh failed';
    dashboard.lastUpdatedAt = 1700000000000;
    dashboard.accountError = 'Previous account failure';

    const refresh = dashboard.refreshAccountOverview();

    expect(dashboard.loading).toBe(true);
    expect(dashboard.error).toBe('Dashboard refresh failed');
    expect(dashboard.lastUpdatedAt).toBe(1700000000000);
    expect(dashboard.accountLoading).toBe(true);
    expect(dashboard.accountError).toBeNull();

    await refresh;

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(dashboard.account).toMatchObject({ equity: 1750 });
    expect(dashboard.accountLoading).toBe(false);
    expect(dashboard.loading).toBe(true);
    expect(dashboard.error).toBe('Dashboard refresh failed');
    expect(dashboard.lastUpdatedAt).toBe(1700000000000);
  });

  it('ignores an older account-only refresh once a full dashboard load starts', async () => {
    const accountRefresh = deferred<unknown>();
    const fullLoadAccount = deferred<unknown>();
    const fullLoadTickers = deferred<unknown>();
    let accountCall = 0;
    const fetchMock = vi.fn((url: string) => {
      if (url === '/api/trading/account') {
        accountCall += 1;
        return (accountCall === 1 ? accountRefresh.promise : fullLoadAccount.promise).then(jsonResponse);
      }

      if (url === '/api/market/tickers') {
        return fullLoadTickers.promise.then(jsonResponse);
      }

      const responses: Record<string, unknown> = {
        '/api/trading/positions': [{ symbol: 'BTC-USDT', amount: 1 }],
        '/api/trading/orders': [{ order_id: 'order-1', symbol: 'BTC-USDT' }],
      };
      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    const refresh = dashboard.refreshAccountOverview();
    const load = dashboard.loadInitialData();

    fullLoadAccount.resolve({
      cash_balance: 2100,
      equity: 2200,
      realized_pnl: 100,
      unrealized_pnl: 0,
      daily_pnl: 10,
      fees_paid: 2,
    });
    fullLoadTickers.resolve([{ symbol: 'ETH-USDT', last: 2000 }]);
    await load;

    expect(dashboard.account).toMatchObject({ equity: 2200 });
    expect(dashboard.positions).toEqual([{ symbol: 'BTC-USDT', amount: 1 }]);
    expect(dashboard.orders).toEqual([{ order_id: 'order-1', symbol: 'BTC-USDT' }]);
    expect(dashboard.tickers).toEqual([{ symbol: 'ETH-USDT', last: 2000 }]);
    expect(dashboard.loading).toBe(false);
    expect(dashboard.accountLoading).toBe(false);
    expect(dashboard.lastUpdatedAt).toEqual(expect.any(Number));
    expect(dashboard.error).toBeNull();
    expect(dashboard.accountError).toBeNull();

    accountRefresh.resolve({
      cash_balance: 1050,
      equity: 1100,
      realized_pnl: 50,
      unrealized_pnl: 0,
      daily_pnl: 5,
      fees_paid: 1,
    });
    await refresh;

    expect(dashboard.account).toMatchObject({ equity: 2200 });
    expect(dashboard.accountLoading).toBe(false);
    expect(dashboard.accountError).toBeNull();
  });

  it('does not let an older account-only refresh overwrite a newer one', async () => {
    const firstAccount = deferred<unknown>();
    const secondAccount = deferred<unknown>();
    let accountCall = 0;
    const fetchMock = vi.fn((url: string) => {
      if (url !== '/api/trading/account') {
        throw new Error(`Unexpected fetch: ${url}`);
      }

      accountCall += 1;
      return (accountCall === 1 ? firstAccount.promise : secondAccount.promise).then(jsonResponse);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    const firstRefresh = dashboard.refreshAccountOverview();
    const secondRefresh = dashboard.refreshAccountOverview();

    secondAccount.resolve({
      cash_balance: 1900,
      equity: 2000,
      realized_pnl: 100,
      unrealized_pnl: 0,
      daily_pnl: 25,
      fees_paid: 5,
    });
    await secondRefresh;
    expect(dashboard.account?.equity).toBe(2000);

    firstAccount.resolve({
      cash_balance: 900,
      equity: 1000,
      realized_pnl: 0,
      unrealized_pnl: 0,
      daily_pnl: 0,
      fees_paid: 0,
    });
    await firstRefresh;

    expect(dashboard.account?.equity).toBe(2000);
    expect(dashboard.accountLoading).toBe(false);
    expect(dashboard.accountError).toBeNull();
  });

  it('keeps shared dashboard errors while the account overview refresh succeeds', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url !== '/api/trading/account') {
        throw new Error(`Unexpected fetch: ${url}`);
      }

      return jsonResponse({
        cash_balance: 1500,
        equity: 1750,
        realized_pnl: 250,
        unrealized_pnl: 12.5,
        daily_pnl: 18,
        fees_paid: 3,
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    dashboard.error = 'Dashboard refresh failed';
    dashboard.account = {
      cash_balance: 1200,
      equity: 1300,
      realized_pnl: 100,
      unrealized_pnl: 0,
      daily_pnl: 6,
      fees_paid: 2,
      assets: [{ ccy: 'USDT', cash_bal: 100, eq: 100, eq_utd: 100, avail_bal: 90, upl: 0 }],
    } as never;

    await dashboard.refreshAccountOverview();

    expect(dashboard.accountError).toBeNull();
    expect(dashboard.error).toBe('Dashboard refresh failed');
    expect(dashboard.account).toMatchObject({ equity: 1750 });
  });

  it('stores account refresh failures without clearing shared dashboard errors', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url !== '/api/trading/account') {
        throw new Error(`Unexpected fetch: ${url}`);
      }

      return Promise.resolve({ ok: false, status: 503 } as Response);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    dashboard.error = 'Dashboard refresh failed';

    await dashboard.refreshAccountOverview();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(dashboard.accountError).toBe('Request failed: 503');
    expect(dashboard.error).toBe('Dashboard refresh failed');
    expect(dashboard.loading).toBe(false);
  });

  it('does not mutate a disposed store when its core load completes', async () => {
    let resolveAccount!: (account: unknown) => void;
    const account = new Promise<unknown>((resolve) => {
      resolveAccount = resolve;
    });
    const fetchMock = vi.fn((url: string) => {
      if (url === '/api/trading/account') {
        return account.then(jsonResponse);
      }

      const responses: Record<string, unknown> = {
        '/api/trading/positions': [{ symbol: 'BTC-USDT', amount: 1 }],
        '/api/trading/orders': [{ order_id: 'order-1', symbol: 'BTC-USDT' }],
        '/api/market/tickers': [{ symbol: 'BTC-USDT', last: 68000 }],
      };
      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    const load = dashboard.loadInitialData();
    dashboard.$dispose();

    resolveAccount({
      cash_balance: 950,
      equity: 1000,
      realized_pnl: 50,
      unrealized_pnl: 0,
      daily_pnl: 12.5,
      fees_paid: 2.5,
    });
    await load;

    expect(dashboard.account).toBeNull();
    expect(dashboard.positions).toEqual([]);
    expect(dashboard.orders).toEqual([]);
    expect(dashboard.tickers).toEqual([]);
    expect(dashboard.error).toBeNull();
    expect(dashboard.tickerError).toBeNull();
    expect(dashboard.lastUpdatedAt).toBeNull();
  });

  it('does not wait for delayed strategy performance before completing core dashboard load', async () => {
    let resolvePerformance!: (rows: never[]) => void;
    const delayedPerformance = new Promise<never[]>((resolve) => {
      resolvePerformance = resolve;
    });
    mockedFetchStrategyPerformance.mockReturnValueOnce(delayedPerformance);

    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 950,
          equity: 1000,
          realized_pnl: 50,
          unrealized_pnl: 0,
          daily_pnl: 12.5,
          fees_paid: 2.5,
        },
        '/api/trading/positions': [{ symbol: 'BTC-USDT', amount: 1 }],
        '/api/trading/orders': [{ order_id: 'order-1', symbol: 'BTC-USDT' }],
        '/api/market/tickers': [{ symbol: 'BTC-USDT', last: 68000 }],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    const initialLoad = dashboard.loadInitialData();

    await initialLoad;

    expect(dashboard.account?.equity).toBe(1000);
    expect(dashboard.positions).toEqual([{ symbol: 'BTC-USDT', amount: 1 }]);
    expect(dashboard.orders).toEqual([{ order_id: 'order-1', symbol: 'BTC-USDT' }]);
    expect(dashboard.tickers).toEqual([{ symbol: 'BTC-USDT', last: 68000 }]);
    expect(dashboard.strategyPerformance).toEqual([]);
    expect(dashboard.strategyPerformanceLoading).toBe(true);

    resolvePerformance([]);
    await delayedPerformance;
    await Promise.resolve();
    expect(dashboard.strategyPerformanceLoading).toBe(false);
    expect(dashboard.strategyPerformanceError).toBeNull();
  });

  it('tracks strategy performance loading separately during refreshes', async () => {
    const refreshedPerformance = deferred<never[]>();
    mockedFetchStrategyPerformance.mockReturnValueOnce(refreshedPerformance.promise);

    const dashboard = useDashboardStore();
    dashboard.strategyPerformance = [{ strategy: 'existing' }] as never[];

    const refresh = dashboard.refreshStrategyPerformance();

    expect(dashboard.strategyPerformanceLoading).toBe(true);
    expect(dashboard.strategyPerformance).toEqual([{ strategy: 'existing' }]);

    refreshedPerformance.resolve([{ strategy: 'refreshed' }] as never[]);
    await refresh;

    expect(dashboard.strategyPerformanceLoading).toBe(false);
    expect(dashboard.strategyPerformance).toEqual([{ strategy: 'refreshed' }]);
    expect(dashboard.strategyPerformanceError).toBeNull();
  });

  it('preserves strategy performance rows when a refresh fails', async () => {
    mockedFetchStrategyPerformance.mockRejectedValueOnce(new Error('Request failed: 500'));

    const dashboard = useDashboardStore();
    dashboard.strategyPerformance = [{ strategy: 'existing' }] as never[];

    await dashboard.refreshStrategyPerformance();

    expect(dashboard.strategyPerformanceLoading).toBe(false);
    expect(dashboard.strategyPerformance).toEqual([{ strategy: 'existing' }]);
    expect(dashboard.strategyPerformanceError).toBe('Request failed: 500');
  });

  it('does not let a stale initial performance response overwrite a newer refresh', async () => {
    let resolveInitial!: (rows: never[]) => void;
    let resolveRefresh!: (rows: never[]) => void;
    const initialPerformance = new Promise<never[]>((resolve) => {
      resolveInitial = resolve;
    });
    const refreshedPerformance = new Promise<never[]>((resolve) => {
      resolveRefresh = resolve;
    });
    mockedFetchStrategyPerformance
      .mockReturnValueOnce(initialPerformance)
      .mockReturnValueOnce(refreshedPerformance);

    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 950,
          equity: 1000,
          realized_pnl: 50,
          unrealized_pnl: 0,
          daily_pnl: 12.5,
          fees_paid: 2.5,
        },
        '/api/trading/positions': [],
        '/api/trading/orders': [],
        '/api/market/tickers': [],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();
    await dashboard.loadInitialData();

    dashboard.scheduleStrategyPerformanceRefresh();
    await vi.advanceTimersByTimeAsync(100);

    const refreshedRows = [{ strategy: 'refreshed' }] as never[];
    resolveRefresh(refreshedRows);
    await refreshedPerformance;
    await Promise.resolve();
    expect(dashboard.strategyPerformance).toEqual(refreshedRows);

    const initialRows = [{ strategy: 'initial' }] as never[];
    resolveInitial(initialRows);
    await initialPerformance;
    await Promise.resolve();
    expect(dashboard.strategyPerformance).toEqual(refreshedRows);
    expect(dashboard.strategyPerformanceError).toBeNull();
  });

  it('does not let an in-flight performance request mutate a disposed store', async () => {
    let resolvePerformance!: (rows: never[]) => void;
    const delayedPerformance = new Promise<never[]>((resolve) => {
      resolvePerformance = resolve;
    });
    mockedFetchStrategyPerformance.mockReturnValueOnce(delayedPerformance);

    const dashboard = useDashboardStore();
    const initialRows = [{ strategy: 'initial' }] as never[];
    const initialLoad = dashboard.loadInitialData();

    dashboard.$dispose();
    resolvePerformance(initialRows);
    await initialLoad;
    await delayedPerformance;
    await Promise.resolve();

    expect(dashboard.strategyPerformance).toEqual([]);
    expect(dashboard.strategyPerformanceError).toBeNull();
  });

  it('loads strategy performance alongside the dashboard snapshot', async () => {
    const performanceRows = [
      {
        strategy: 'ma_cross_btc',
        initial_equity: 1000,
        equity: 1042.5,
        return_pct: 0.0425,
        realized_pnl: 30,
        unrealized_pnl: 12.5,
        fees_paid: 1.5,
        position_notional: 250,
        open_positions: 1,
        order_count: 4,
        filled_order_count: 3,
        trade_count: 5,
        closed_trade_count: 2,
        winning_trade_count: 1,
        losing_trade_count: 1,
        win_rate: 0.5,
        last_order_at: 1700000000000,
      },
    ];
    mockedFetchStrategyPerformance.mockResolvedValueOnce(performanceRows);

    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 950,
          equity: 1000,
          realized_pnl: 50,
          unrealized_pnl: 0,
          daily_pnl: 12.5,
          fees_paid: 2.5,
        },
        '/api/trading/positions': [{ symbol: 'BTC-USDT', amount: 1 }],
        '/api/trading/orders': [{ order_id: 'order-1', symbol: 'BTC-USDT' }],
        '/api/market/tickers': [],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();

    expect(mockedFetchStrategyPerformance).toHaveBeenCalledTimes(1);
    expect(dashboard.strategyPerformance).toEqual(performanceRows);
    expect(dashboard.strategyPerformanceError).toBeNull();
    expect(dashboard.error).toBeNull();
  });

  it('keeps core and ticker data when strategy performance load fails', async () => {
    mockedFetchStrategyPerformance.mockRejectedValueOnce(new Error('Request failed: 500'));

    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 950,
          equity: 1000,
          realized_pnl: 50,
          unrealized_pnl: 0,
          daily_pnl: 12.5,
          fees_paid: 2.5,
        },
        '/api/trading/positions': [{ symbol: 'BTC-USDT', amount: 1 }],
        '/api/trading/orders': [{ order_id: 'order-1', symbol: 'BTC-USDT' }],
        '/api/market/tickers': [{ symbol: 'BTC-USDT', last: 68000 }],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();

    expect(dashboard.account).toEqual({
      cash_balance: 950,
      equity: 1000,
      realized_pnl: 50,
      unrealized_pnl: 0,
      daily_pnl: 12.5,
      fees_paid: 2.5,
    });
    expect(dashboard.positions).toEqual([{ symbol: 'BTC-USDT', amount: 1 }]);
    expect(dashboard.orders).toEqual([{ order_id: 'order-1', symbol: 'BTC-USDT' }]);
    expect(dashboard.tickers).toEqual([{ symbol: 'BTC-USDT', last: 68000 }]);
    expect(dashboard.strategyPerformance).toEqual([]);
    expect(dashboard.strategyPerformanceError).toBe('Request failed: 500');
    expect(dashboard.error).toBeNull();
  });

  it('coalesces burst websocket updates into one strategy performance refresh', async () => {
    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 950,
          equity: 1000,
          realized_pnl: 50,
          unrealized_pnl: 0,
          daily_pnl: 12.5,
          fees_paid: 2.5,
        },
        '/api/trading/positions': [],
        '/api/trading/orders': [],
        '/api/market/tickers': [],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();
    mockedFetchStrategyPerformance.mockClear();

    vi.useFakeTimers();
    try {
      dashboard.addWebSocketMessage({
        type: 'account',
        account: {
          cash_balance: 960,
          equity: 1010,
          realized_pnl: 60,
          unrealized_pnl: 0,
          daily_pnl: 15,
          fees_paid: 3,
        },
      });
      dashboard.addWebSocketMessage({
        type: 'positions',
        positions: [{ symbol: 'BTC-USDT', amount: 1 }],
      });
      dashboard.addWebSocketMessage({
        type: 'orders',
        orders: [{ order_id: 'order-2', symbol: 'BTC-USDT' }],
      });

      await vi.advanceTimersByTimeAsync(99);
      expect(mockedFetchStrategyPerformance).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(1);
      expect(mockedFetchStrategyPerformance).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps strategy performance debounce timers isolated per store', async () => {
    const firstPinia = createPinia();
    setActivePinia(firstPinia);
    const firstDashboard = useDashboardStore();

    const secondPinia = createPinia();
    setActivePinia(secondPinia);
    const secondDashboard = useDashboardStore();

    firstDashboard.scheduleStrategyPerformanceRefresh();
    secondDashboard.scheduleStrategyPerformanceRefresh();

    await vi.advanceTimersByTimeAsync(100);

    expect(mockedFetchStrategyPerformance).toHaveBeenCalledTimes(2);
  });

  it('preserves assets from initial account API load', async () => {
    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 100,
          equity: 100,
          realized_pnl: 0,
          unrealized_pnl: 0,
          daily_pnl: 0,
          fees_paid: 0,
          available_balance: 90,
          assets: [usdtAsset],
        },
        '/api/trading/positions': [],
        '/api/trading/orders': [],
        '/api/market/tickers': [],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();

    expect(dashboard.account?.assets).toEqual([usdtAsset]);
    expect(dashboard.account?.available_balance).toBe(90);
  });

  it('retains zero-valued account API load when assets are present', async () => {
    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 0,
          equity: 0,
          realized_pnl: 0,
          unrealized_pnl: 0,
          daily_pnl: 0,
          fees_paid: 0,
          assets: [usdtAsset],
        },
        '/api/trading/positions': [],
        '/api/trading/orders': [],
        '/api/market/tickers': [],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();

    expect(dashboard.account).toEqual({
      cash_balance: 0,
      equity: 0,
      realized_pnl: 0,
      unrealized_pnl: 0,
      daily_pnl: 0,
      fees_paid: 0,
      assets: [usdtAsset],
    });
  });

  it('preserves assets from direct account websocket messages', () => {
    const dashboard = useDashboardStore();

    dashboard.addWebSocketMessage({
      type: 'account',
      account: {
        cash_balance: 100,
        equity: 100,
        realized_pnl: 0,
        unrealized_pnl: 0,
        daily_pnl: 0,
        fees_paid: 0,
        assets: [usdtAsset],
      },
    });

    expect(dashboard.account?.assets).toEqual([usdtAsset]);
  });

  it('preserves assets from snapshot websocket account messages', () => {
    const dashboard = useDashboardStore();

    dashboard.addWebSocketMessage({
      type: 'snapshot',
      data: {
        account: {
          cash_balance: 100,
          equity: 100,
          realized_pnl: 0,
          unrealized_pnl: 0,
          daily_pnl: 0,
          fees_paid: 0,
          assets: [usdtAsset],
        },
        positions: [],
        orders: [],
        strategies: [],
      },
    });

    expect(dashboard.account?.assets).toEqual([usdtAsset]);
  });

  it('treats all-zero account responses without runtime rows as missing account state', async () => {
    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 0,
          equity: 0,
          realized_pnl: 0,
          unrealized_pnl: 0,
          daily_pnl: 0,
          fees_paid: 0,
        },
        '/api/trading/positions': [],
        '/api/trading/orders': [],
        '/api/market/tickers': [],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();

    expect(dashboard.account).toBeNull();
    expect(dashboard.positions).toEqual([]);
    expect(dashboard.orders).toEqual([]);
    expect(dashboard.error).toBeNull();
  });

  it('retains otherwise zero account responses when available balance is non-zero', async () => {
    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 0,
          available_balance: 25,
          equity: 0,
          realized_pnl: 0,
          unrealized_pnl: 0,
          daily_pnl: 0,
          fees_paid: 0,
        },
        '/api/trading/positions': [],
        '/api/trading/orders': [],
        '/api/market/tickers': [],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();

    expect(dashboard.account).toEqual({
      cash_balance: 0,
      available_balance: 25,
      equity: 0,
      realized_pnl: 0,
      unrealized_pnl: 0,
      daily_pnl: 0,
      fees_paid: 0,
    });
  });

  it('keeps rejected orders while treating all-zero account responses as missing account state', async () => {
    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 0,
          equity: 0,
          realized_pnl: 0,
          unrealized_pnl: 0,
          daily_pnl: 0,
          fees_paid: 0,
        },
        '/api/trading/positions': [],
        '/api/trading/orders': [{ order_id: 'risk-1', status: 'rejected' }],
        '/api/market/tickers': [],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();

    expect(dashboard.account).toBeNull();
    expect(dashboard.orders).toEqual([{ order_id: 'risk-1', status: 'rejected' }]);
  });

  it('loads public market tickers with the dashboard snapshot data', async () => {
    const fetchMock = vi.fn((url: string) => {
      const responses: Record<string, unknown> = {
        '/api/trading/account': {
          cash_balance: 950,
          equity: 1000,
          realized_pnl: 50,
          unrealized_pnl: 0,
          daily_pnl: 12.5,
          fees_paid: 2.5,
        },
        '/api/trading/positions': [],
        '/api/trading/orders': [],
        '/api/market/tickers': [
          { symbol: 'BTC-USDT', last: 68000, bidPx: 67999.5, askPx: 68000.5, vol24h: 123.45 },
        ],
      };

      return jsonResponse(responses[url]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const dashboard = useDashboardStore();

    await dashboard.loadInitialData();

    expect(fetchMock).toHaveBeenCalledWith('/api/market/tickers');
    expect(dashboard.tickers).toEqual([
      { symbol: 'BTC-USDT', last: 68000, bidPx: 67999.5, askPx: 68000.5, vol24h: 123.45 },
    ]);
    expect(dashboard.tickerError).toBeNull();
    expect(dashboard.lastUpdatedAt).toEqual(expect.any(Number));
  });

  it('treats all-zero account websocket snapshots with no runtime rows as missing account state', () => {
    const dashboard = useDashboardStore();

    dashboard.addWebSocketMessage({
      type: 'snapshot',
      data: {
        account: {
          cash_balance: 0,
          equity: 0,
          realized_pnl: 0,
          unrealized_pnl: 0,
          daily_pnl: 0,
          fees_paid: 0,
        },
        positions: [],
        orders: [],
        strategies: [],
      },
    });

    expect(dashboard.account).toBeNull();
    expect(dashboard.positions).toEqual([]);
    expect(dashboard.orders).toEqual([]);
  });

  it('treats all-zero account websocket messages with no runtime rows as missing account state', () => {
    const dashboard = useDashboardStore();

    dashboard.addWebSocketMessage({
      type: 'account',
      account: {
        cash_balance: 0,
        equity: 0,
        realized_pnl: 0,
        unrealized_pnl: 0,
        daily_pnl: 0,
        fees_paid: 0,
      },
    });

    expect(dashboard.account).toBeNull();
  });

  it('preserves shared received timestamps on websocket messages', () => {
    const dashboard = useDashboardStore();

    dashboard.addWebSocketMessage({ type: 'connected', received_at: 1700000001000 });

    expect(dashboard.websocketMessages).toEqual([
      { type: 'connected', received_at: 1700000001000 },
    ]);
  });

  it('keeps strategy websocket messages only in message history', () => {
    const dashboard = useDashboardStore();

    dashboard.addWebSocketMessage({
      type: 'strategy_status',
      strategy: 'ma_cross_btc',
      status: 'running',
      timestamp: 1700000000000,
    });
    dashboard.addWebSocketMessage({
      type: 'strategy_error',
      strategy: 'ma_cross_btc',
      error: 'boom',
      timestamp: 1700000000001,
    });

    expect(dashboard.websocketMessages.map((message) => message.type)).toEqual([
      'strategy_error',
      'strategy_status',
    ]);
    expect('strategies' in dashboard).toBe(false);
    expect('strategyErrors' in dashboard).toBe(false);
  });
});
