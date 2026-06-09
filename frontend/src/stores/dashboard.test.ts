import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

import { useDashboardStore } from './dashboard';

const jsonResponse = (data: unknown) =>
  Promise.resolve({
    ok: true,
    json: () => Promise.resolve(data),
  } as Response);

describe('dashboard store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
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
        '/api/strategies': [{ name: 'ma_cross', status: 'running' }],
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
    expect(dashboard.strategies).toEqual([{ name: 'ma_cross', status: 'running' }]);
    expect(dashboard.tickers).toEqual([]);
    expect(dashboard.error).toBeNull();
    expect(dashboard.tickerError).toBe('Request failed: 503');
    expect(dashboard.lastUpdatedAt).toEqual(expect.any(Number));
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
        '/api/strategies': [{ name: 'ma_cross', status: 'stopped' }],
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
        '/api/strategies': [{ name: 'ma_cross', status: 'stopped' }],
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

  it('adds received timestamps to websocket messages', () => {
    vi.setSystemTime(new Date('2026-06-03T12:00:00Z'));

    const dashboard = useDashboardStore();

    dashboard.addWebSocketMessage({ type: 'connected' });

    expect(dashboard.websocketMessages).toEqual([
      { type: 'connected', received_at: new Date('2026-06-03T12:00:00Z').getTime() },
    ]);
  });

  it('applies strategy_status websocket messages to known strategies', () => {
    const dashboard = useDashboardStore();
    dashboard.strategies = [{ name: 'ma_cross', status: 'stopped' }];
    dashboard.strategyErrors = { ma_cross: 'previous error' };

    dashboard.addWebSocketMessage({
      type: 'strategy_status',
      strategy: 'ma_cross',
      status: 'running',
      timestamp: 1700000000000,
    });

    expect(dashboard.strategies).toEqual([{ name: 'ma_cross', status: 'running' }]);
    expect(dashboard.strategyErrors).toEqual({});
  });

  it('adds unknown strategies from strategy_status websocket messages', () => {
    const dashboard = useDashboardStore();

    dashboard.addWebSocketMessage({
      type: 'strategy_status',
      strategy: 'ma_cross_btc',
      status: 'stopped',
      timestamp: 1700000000000,
    });

    expect(dashboard.strategies).toEqual([{ name: 'ma_cross_btc', status: 'stopped' }]);
  });

  it('records strategy_error websocket messages without fabricating status', () => {
    const dashboard = useDashboardStore();
    dashboard.strategies = [{ name: 'ma_cross_btc', status: 'stopped' }];

    dashboard.addWebSocketMessage({
      type: 'strategy_error',
      strategy: 'ma_cross_btc',
      error: 'boom',
      timestamp: 1700000000000,
    });

    expect(dashboard.strategyErrors).toEqual({ ma_cross_btc: 'boom' });
    expect(dashboard.strategies).toEqual([{ name: 'ma_cross_btc', status: 'stopped' }]);
  });
});
