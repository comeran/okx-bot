import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useAccountContextStore } from './accountContext';
import { useDashboardStore } from './dashboard';
import type { ExecutionContext } from '@/types/executionContext';

const api = vi.hoisted(() => ({
  context: vi.fn(),
  snapshot: vi.fn(),
  performance: vi.fn()
}));
vi.mock('@/services/trading', () => ({
  fetchTradingContext: api.context,
  fetchExchangeSnapshot: api.snapshot,
  fetchStrategyPerformance: api.performance
}));
const context = (id = 'A', environment: 'sandbox' | 'live' = 'sandbox'): ExecutionContext => ({
  account_scope_id: id,
  exchange: 'okx',
  environment,
  market_type: 'spot',
  identity_verified: false,
  available: true,
  reason: null,
  execution_ready: false
});
const snapshot = (id = 'A', equity: number | null = 100) => ({
  type: 'snapshot',
  scope: 'exchange',
  context: context(id),
  data: {
    scope: 'exchange',
    strategy: '__exchange__',
    account: equity === null ? null : {
      equity,
      daily_pnl: 0
    },
    positions: [],
    orders: []
  }
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return {
    promise,
    resolve,
    reject
  };
}
async function ready() {
  const account = useAccountContextStore();
  await account.reload();
  return account;
}
beforeEach(() => {
  setActivePinia(createPinia());
  vi.resetAllMocks();
  vi.useFakeTimers();
  api.context.mockResolvedValue(context());
  api.snapshot.mockResolvedValue(snapshot());
  api.performance.mockResolvedValue([]);
  vi.stubGlobal('fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => []
    }));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('authoritative account context', () => {
  it('increments epoch and clears context as soon as reload starts, including A to B to A', async () => {
    const account = await ready();
    const first = deferred<ExecutionContext>();
    const second = deferred<ExecutionContext>();
    api.context.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockResolvedValueOnce(context('A'));
    const oldA = account.reload();
    expect(account.epoch).toBe(2);
    expect(account.context).toBeNull();
    expect(account.loading).toBe(true);
    const oldB = account.reload();
    await account.reload();
    first.resolve(context('A'));
    second.reject(new Error('stale B error'));
    await Promise.all([oldA,
      oldB]);
    expect(account.epoch).toBe(4);
    expect(account.context?.account_scope_id).toBe('A');
    expect(account.error).toBeNull();
    expect(account.loading).toBe(false);
  });
  it('does not let a stale finally clear the newest context loading indicator', async () => {
    const account = await ready();
    const old = deferred<ExecutionContext>();
    const current = deferred<ExecutionContext>();
    api.context.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    const first = account.reload();
    const second = account.reload();
    old.resolve(context());
    await first;
    expect(account.loading).toBe(true);
    expect(account.context).toBeNull();
    current.resolve(context('B'));
    await second;
    expect(account.context?.account_scope_id).toBe('B');
  });
});

describe('account bound dashboard', () => {
  it.each(['trade', 'trades'])('rejects unbound %s events and snapshots before visible history', async (field) => {
    await ready();
    const dashboard = useDashboardStore();
    dashboard.addWebSocketMessage({ type: field, scope: 'strategy', strategy: 'old', [field]: [] });
    dashboard.addWebSocketMessage({ type: 'snapshot', scope: 'strategy', data: { [field]: [] } });
    expect(dashboard.websocketMessages).toEqual([]);
    await vi.runAllTimersAsync();
    expect(api.performance).not.toHaveBeenCalled();
  });
  it('clears visible financial state and invalidates old snapshot, performance, error and finally at reload start', async () => {
    const account = await ready();
    const dashboard = useDashboardStore();
    const oldSnapshot = deferred<ReturnType<typeof snapshot>>();
    const oldPerformance = deferred<never[]>();
    const newContext = deferred<ExecutionContext>();
    api.snapshot.mockReturnValueOnce(oldSnapshot.promise);
    api.performance.mockReturnValueOnce(oldPerformance.promise);
    dashboard.account = {
      equity: 9,
      daily_pnl: 0
    };
    dashboard.positions = [{
      symbol: 'OLD'
    }];
    dashboard.orders = [{
      order_id: 'old'
    }];
    dashboard.strategyPerformance = [{
      strategy: 'old'
    }] as never;
    dashboard.websocketMessages = [{
      type: 'orders',
      scope: 'exchange',
      context: context(),
      orders: []
    }];
    const loading = dashboard.loadInitialData();
    await Promise.resolve();
    await Promise.resolve();
    api.context.mockReturnValueOnce(newContext.promise);
    const reloading = account.reload();
    expect(dashboard.account).toBeNull();
    expect(dashboard.positions).toEqual([]);
    expect(dashboard.orders).toEqual([]);
    expect(dashboard.strategyPerformance).toEqual([]);
    expect(dashboard.websocketMessages).toEqual([]);
    oldSnapshot.reject(new Error('old account error'));
    oldPerformance.resolve([{
      strategy: 'old'
    }] as never);
    await loading;
    expect(dashboard.error).toBeNull();
    expect(dashboard.strategyPerformance).toEqual([]);
    expect(account.loading).toBe(true);
    newContext.resolve(context('B'));
    await reloading;
    await dashboard.loadInitialData();
    expect(api.snapshot).toHaveBeenLastCalledWith('B');
    expect(api.performance).toHaveBeenLastCalledWith('B');
  });
  it('rejects unbound, old-account and wrong-environment events before history and derived refresh', async () => {
    await ready();
    const dashboard = useDashboardStore();
    for (const binding of [undefined,
      context('B'),
      context('A',
        'live')]) dashboard.addWebSocketMessage({
          type: 'account',
          scope: 'exchange',
          context: binding,
          account: {
            equity: 999,
            daily_pnl: 0
          }
        });
    expect(dashboard.account).toBeNull();
    expect(dashboard.websocketMessages).toEqual([]);
    await vi.runAllTimersAsync();
    expect(api.performance).not.toHaveBeenCalled();
    dashboard.addWebSocketMessage({
      type: 'account',
      scope: 'exchange',
      context: context(),
      account: {
        equity: 5,
        daily_pnl: 0
      }
    });
    expect(dashboard.account?.equity).toBe(5);
    expect(dashboard.websocketMessages).toHaveLength(1);
  });
  it('refreshes paper strategy performance on provenanced financial events without recording exchange history', async () => {
    api.context.mockResolvedValueOnce({
      ...context(),
      available: false,
      account_scope_id: null,
      reason: 'credentials_unavailable'
    });
    await ready();
    const dashboard = useDashboardStore();
    for (const type of ['orders',
      'account',
      'positions']) dashboard.addWebSocketMessage({
        type,
        scope: 'strategy',
        strategy: 'paper-alpha',
        source: 'paper',
        runtime_mode: 'paper',
        data: []
      });
    expect(dashboard.account).toBeNull();
    expect(dashboard.positions).toEqual([]);
    expect(dashboard.orders).toEqual([]);
    expect(dashboard.websocketMessages).toEqual([]);
    await vi.advanceTimersByTimeAsync(100);
    expect(api.performance).toHaveBeenCalledTimes(1);
    expect(api.performance).toHaveBeenCalledWith(null);
  });
  it('retains strategy lifecycle messages without accepting their financial payloads', async () => {
    await ready();
    const dashboard = useDashboardStore();
    dashboard.addWebSocketMessage({
      type: 'strategy_status',
      scope: 'strategy',
      strategy: 'paper',
      status: 'running'
    });
    dashboard.addWebSocketMessage({
      type: 'account',
      scope: 'strategy',
      strategy: 'paper',
      account: {
        equity: 99,
        daily_pnl: 0
      }
    });
    expect(dashboard.websocketMessages).toHaveLength(1);
    expect(dashboard.account).toBeNull();
  });
  it('keeps same-account per-field websocket ordering for a bound REST snapshot', async () => {
    await ready();
    const dashboard = useDashboardStore();
    const pending = deferred<ReturnType<typeof snapshot>>();
    api.snapshot.mockReturnValueOnce(pending.promise);
    const load = dashboard.loadInitialData();
    dashboard.addWebSocketMessage({
      type: 'orders',
      scope: 'exchange',
      context: context(),
      orders: [{
        order_id: 'new-ws'
      }]
    });
    pending.resolve(snapshot('A',
      200));
    await load;
    expect(dashboard.orders).toEqual([{
      order_id: 'new-ws'
    }]);
    expect(dashboard.account?.equity).toBe(200);
  });
  it('does not apply a mismatching REST snapshot or let its context select the account', async () => {
    const account = await ready();
    const dashboard = useDashboardStore();
    api.snapshot.mockResolvedValueOnce(snapshot('B',
      999));
    await dashboard.loadInitialData();
    expect(dashboard.account).toBeNull();
    expect(account.context?.account_scope_id).toBe('A');
    expect(dashboard.error).toBeTruthy();
  });
  it('keeps a real zero account distinct from a missing snapshot', async () => {
    await ready();
    const dashboard = useDashboardStore();
    api.snapshot.mockResolvedValueOnce(snapshot('A',
      0));
    await dashboard.loadInitialData();
    expect(dashboard.account?.equity).toBe(0);
    api.snapshot.mockResolvedValueOnce(snapshot('A',
      null));
    await dashboard.refreshAccountOverview();
    expect(dashboard.account).toBeNull();
  });
  it('does not fetch exchange facts for unavailable context and still permits paper performance', async () => {
    api.context.mockResolvedValueOnce({
      ...context(),
      available: false,
      account_scope_id: null,
      reason: 'credentials_unavailable'
    });
    await ready();
    const dashboard = useDashboardStore();
    await dashboard.loadInitialData();
    expect(api.snapshot).not.toHaveBeenCalled();
    expect(dashboard.account).toBeNull();
    expect(api.performance).toHaveBeenCalledWith(null);
  });
});
