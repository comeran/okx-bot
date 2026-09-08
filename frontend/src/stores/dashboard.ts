import { defineStore } from 'pinia';

import { fetchStrategyPerformance } from '@/services/trading';
import type { StrategyPerformance } from '@/types/strategyPerformance';
import type {
  AccountSummary,
  DashboardSnapshot,
  DashboardWebSocketMessage,
  MarketTicker,
  Order,
  Position,
} from '@/types/dashboard';

const WEBSOCKET_MESSAGE_HISTORY_LIMIT = 20;
const STRATEGY_PERFORMANCE_REFRESH_DELAY_MS = 100;

const strategyPerformanceRefreshTimers = new WeakMap<
  object,
  ReturnType<typeof globalThis.setTimeout>
>();
const strategyPerformanceRequestSequences = new WeakMap<object, number>();
const strategyPerformanceRequestGenerations = new WeakMap<object, number>();
const dashboardLoadRequestSequences = new WeakMap<object, number>();
const dashboardLoadGenerations = new WeakMap<object, number>();
const dashboardLoadPendingRequests = new WeakMap<object, number>();
const accountOverviewRequestSequences = new WeakMap<object, number>();
const accountOverviewRequestGenerations = new WeakMap<object, number>();
const accountOverviewPendingRequests = new WeakMap<object, number>();

function nextDashboardLoadRequestId(store: object): number {
  const requestId = (dashboardLoadRequestSequences.get(store) ?? 0) + 1;
  dashboardLoadRequestSequences.set(store, requestId);
  return requestId;
}

function nextDashboardLoadGeneration(store: object): number {
  const generation = (dashboardLoadGenerations.get(store) ?? 0) + 1;
  dashboardLoadGenerations.set(store, generation);
  return generation;
}

function currentDashboardLoadGeneration(store: object): number {
  return dashboardLoadGenerations.get(store) ?? 0;
}

function invalidateDashboardLoads(store: object) {
  nextDashboardLoadRequestId(store);
  dashboardLoadPendingRequests.delete(store);
}

function nextAccountOverviewRequestId(store: object): number {
  const requestId = (accountOverviewRequestSequences.get(store) ?? 0) + 1;
  accountOverviewRequestSequences.set(store, requestId);
  return requestId;
}

function invalidateAccountOverviewRequests(store: object) {
  nextAccountOverviewRequestId(store);
  accountOverviewPendingRequests.delete(store);
}

function hasPendingAccountRequest(store: object, loadGeneration: number): boolean {
  return (
    accountOverviewPendingRequests.has(store)
    && (accountOverviewRequestGenerations.get(store) ?? 0) === loadGeneration
  );
}

function nextStrategyPerformanceRequestId(store: object): number {
  const requestId = (strategyPerformanceRequestSequences.get(store) ?? 0) + 1;
  strategyPerformanceRequestSequences.set(store, requestId);
  return requestId;
}

function nextStrategyPerformanceRequestGeneration(store: object): number {
  const generation = (strategyPerformanceRequestGenerations.get(store) ?? 0) + 1;
  strategyPerformanceRequestGenerations.set(store, generation);
  return generation;
}

function currentStrategyPerformanceRequestGeneration(store: object): number {
  return strategyPerformanceRequestGenerations.get(store) ?? 0;
}

function isCurrentStrategyPerformanceRequest(
  store: object,
  requestId: number,
  requestGeneration: number,
): boolean {
  return (
    strategyPerformanceRequestSequences.get(store) === requestId
    && currentStrategyPerformanceRequestGeneration(store) === requestGeneration
  );
}

function invalidateStrategyPerformanceRequests(store: object) {
  nextStrategyPerformanceRequestId(store);
  nextStrategyPerformanceRequestGeneration(store);
}

interface DashboardState {
  account: AccountSummary | null;
  positions: Position[];
  orders: Order[];
  tickers: MarketTicker[];
  strategyPerformance: StrategyPerformance[];
  strategyPerformanceError: string | null;
  strategyPerformanceLoading: boolean;
  websocketConnected: boolean;
  websocketMessages: DashboardWebSocketMessage[];
  loading: boolean;
  accountLoading: boolean;
  error: string | null;
  accountError: string | null;
  tickerError: string | null;
  lastUpdatedAt: number | null;
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isAccountSummary(value: unknown): value is AccountSummary {
  return isRecord(value) && typeof value.equity === 'number' && typeof value.daily_pnl === 'number';
}

function isPositionArray(value: unknown): value is Position[] {
  return Array.isArray(value) && value.every(isRecord);
}

function isOrderArray(value: unknown): value is Order[] {
  return Array.isArray(value) && value.every(isRecord);
}

function isMarketTicker(value: unknown): value is MarketTicker {
  return isRecord(value) && typeof value.symbol === 'string';
}

function isMarketTickerArray(value: unknown): value is MarketTicker[] {
  return Array.isArray(value) && value.every(isMarketTicker);
}

function isZeroAccountPlaceholder(
  account: AccountSummary,
  positions: Position[],
): boolean {
  return (
    positions.length === 0
    && (account.assets?.length ?? 0) === 0
    && [
      account.cash_balance ?? 0,
      account.available_balance ?? 0,
      account.equity,
      account.realized_pnl ?? 0,
      account.unrealized_pnl ?? 0,
      account.daily_pnl,
      account.fees_paid ?? 0,
    ].every((value) => value === 0)
  );
}

function normalizeAccount(
  account: AccountSummary,
  positions: Position[],
): AccountSummary | null {
  return isZeroAccountPlaceholder(account, positions) ? null : account;
}

function payloadFor(message: DashboardWebSocketMessage, key: string): unknown {
  const record = message as Record<string, unknown>;
  return record[key] ?? record.data;
}

function snapshotTouchesTradingState(snapshot: DashboardSnapshot | Record<string, unknown>): boolean {
  return 'account' in snapshot || 'positions' in snapshot || 'orders' in snapshot;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function clearStrategyPerformanceRefreshTimer(store: object) {
  const timer = strategyPerformanceRefreshTimers.get(store);
  if (timer !== undefined) {
    globalThis.clearTimeout(timer);
    strategyPerformanceRefreshTimers.delete(store);
  }
}

const useDashboardStoreBase = defineStore('dashboard', {
  state: (): DashboardState => ({
    account: null,
    positions: [],
    orders: [],
    tickers: [],
    strategyPerformance: [],
    strategyPerformanceError: null,
    strategyPerformanceLoading: false,
    websocketConnected: false,
    websocketMessages: [],
    loading: false,
    accountLoading: false,
    error: null,
    accountError: null,
    tickerError: null,
    lastUpdatedAt: null,
  }),
  actions: {
    async loadInitialData() {
      const loadGeneration = nextDashboardLoadGeneration(this);
      const loadRequestId = nextDashboardLoadRequestId(this);
      const isCurrentLoad = () => (
        dashboardLoadRequestSequences.get(this) === loadRequestId
        && currentDashboardLoadGeneration(this) === loadGeneration
      );
      dashboardLoadPendingRequests.set(this, loadRequestId);

      this.loading = true;
      this.accountLoading = true;
      this.error = null;
      this.accountError = null;
      this.tickerError = null;
      this.strategyPerformanceError = null;
      clearStrategyPerformanceRefreshTimer(this);

      void this.refreshStrategyPerformance();

      try {
        const [accountResult, positionsResult, ordersResult] = await Promise.allSettled([
          fetchJson<AccountSummary>('/api/trading/account'),
          fetchJson<Position[]>('/api/trading/positions'),
          fetchJson<Order[]>('/api/trading/orders'),
        ]);

        if (accountResult.status !== 'fulfilled') {
          throw accountResult.reason;
        }

        if (positionsResult.status !== 'fulfilled') {
          throw positionsResult.reason;
        }

        if (ordersResult.status !== 'fulfilled') {
          throw ordersResult.reason;
        }

        if (!isCurrentLoad()) {
          return;
        }

        this.account = normalizeAccount(accountResult.value, positionsResult.value);
        this.positions = positionsResult.value;
        this.orders = ordersResult.value;

        try {
          const tickers = await fetchJson<MarketTicker[]>('/api/market/tickers');
          if (isCurrentLoad()) {
            this.tickers = isMarketTickerArray(tickers) ? tickers : [];
          }
        } catch (error) {
          if (isCurrentLoad()) {
            this.tickerError = error instanceof Error ? error.message : 'Failed to load market tickers';
            this.tickers = [];
          }
        }

        if (isCurrentLoad()) {
          this.lastUpdatedAt = Date.now();
        }
      } catch (error) {
        if (isCurrentLoad()) {
          this.error = errorMessage(error, 'Failed to load dashboard data');
        }
      } finally {
        if (dashboardLoadPendingRequests.get(this) === loadRequestId) {
          dashboardLoadPendingRequests.delete(this);
        }
        if (isCurrentLoad()) {
          this.loading = false;
          this.accountLoading = hasPendingAccountRequest(this, loadGeneration);
        }
      }
    },
    async refreshAccountOverview() {
      const accountRequestGeneration = currentDashboardLoadGeneration(this);
      const accountRequestId = nextAccountOverviewRequestId(this);
      const isCurrentAccountRequest = () => (
        accountOverviewRequestSequences.get(this) === accountRequestId
        && (accountOverviewRequestGenerations.get(this) ?? 0) === accountRequestGeneration
        && currentDashboardLoadGeneration(this) === accountRequestGeneration
      );
      accountOverviewRequestGenerations.set(this, accountRequestGeneration);
      accountOverviewPendingRequests.set(this, accountRequestId);

      this.accountLoading = true;
      this.accountError = null;

      try {
        const account = await fetchJson<AccountSummary>('/api/trading/account');
        if (!isCurrentAccountRequest()) {
          return;
        }

        this.account = normalizeAccount(account, this.positions);
        this.accountError = null;
      } catch (error) {
        if (isCurrentAccountRequest()) {
          this.accountError = errorMessage(error, 'Failed to load dashboard data');
        }
      } finally {
        if (accountOverviewPendingRequests.get(this) === accountRequestId) {
          accountOverviewPendingRequests.delete(this);
        }
        if (isCurrentAccountRequest()) {
          this.accountLoading = hasPendingAccountRequest(this, accountRequestGeneration);
        }
      }
    },
    setWebSocketConnected(connected: boolean) {
      this.websocketConnected = connected;
    },
    addWebSocketMessage(message: DashboardWebSocketMessage) {
      this.websocketMessages.unshift(message);
      this.websocketMessages = this.websocketMessages.slice(0, WEBSOCKET_MESSAGE_HISTORY_LIMIT);
      this.applyWebSocketMessage(message);
    },
    applyWebSocketMessage(message: DashboardWebSocketMessage) {
      switch (message.type) {
        case 'account': {
          const account = payloadFor(message, 'account');
          if (isAccountSummary(account)) {
            this.account = normalizeAccount(account, this.positions);
          }
          this.scheduleStrategyPerformanceRefresh();
          break;
        }
        case 'positions': {
          const positions = payloadFor(message, 'positions');
          if (isPositionArray(positions)) {
            this.positions = positions;
            if (this.account !== null) {
              this.account = normalizeAccount(this.account, this.positions);
            }
          }
          this.scheduleStrategyPerformanceRefresh();
          break;
        }
        case 'orders': {
          const orders = payloadFor(message, 'orders');
          if (isOrderArray(orders)) {
            this.orders = orders;
          }
          this.scheduleStrategyPerformanceRefresh();
          break;
        }
        case 'snapshot': {
          const snapshot = isRecord(message.data) ? message.data : message;
          this.applySnapshot(snapshot);
          break;
        }
        default:
          break;
      }
    },
    applySnapshot(snapshot: DashboardSnapshot | Record<string, unknown>) {
      const positions = isPositionArray(snapshot.positions) ? snapshot.positions : this.positions;
      const orders = isOrderArray(snapshot.orders) ? snapshot.orders : this.orders;

      if (isPositionArray(snapshot.positions)) {
        this.positions = positions;
      }

      if (isOrderArray(snapshot.orders)) {
        this.orders = orders;
      }

      if ('account' in snapshot) {
        if (snapshot.account === null) {
          this.account = null;
        } else if (isAccountSummary(snapshot.account)) {
          this.account = normalizeAccount(snapshot.account, positions);
        }
      }

      if (snapshotTouchesTradingState(snapshot)) {
        this.scheduleStrategyPerformanceRefresh();
      }
    },
    scheduleStrategyPerformanceRefresh() {
      clearStrategyPerformanceRefreshTimer(this);
      const timer = globalThis.setTimeout(() => {
        strategyPerformanceRefreshTimers.delete(this);
        void this.refreshStrategyPerformance();
      }, STRATEGY_PERFORMANCE_REFRESH_DELAY_MS);
      strategyPerformanceRefreshTimers.set(this, timer);
    },
    async refreshStrategyPerformance() {
      const requestGeneration = nextStrategyPerformanceRequestGeneration(this);
      const requestId = nextStrategyPerformanceRequestId(this);
      this.strategyPerformanceLoading = true;

      try {
        const performance = await fetchStrategyPerformance();
        if (!isCurrentStrategyPerformanceRequest(this, requestId, requestGeneration)) {
          return;
        }
        this.strategyPerformance = performance;
        this.strategyPerformanceError = null;
      } catch (error) {
        if (!isCurrentStrategyPerformanceRequest(this, requestId, requestGeneration)) {
          return;
        }
        this.strategyPerformanceError = errorMessage(
          error,
          'Failed to load strategy performance',
        );
      } finally {
        if (isCurrentStrategyPerformanceRequest(this, requestId, requestGeneration)) {
          this.strategyPerformanceLoading = false;
        }
      }
    },
  },
});

type DashboardStore = ReturnType<typeof useDashboardStoreBase>;

const patchedDashboardStores = new WeakSet<DashboardStore>();

export const useDashboardStore = (): DashboardStore => {
  const store = useDashboardStoreBase();

  if (!patchedDashboardStores.has(store)) {
    const originalDispose = store.$dispose.bind(store);
    store.$dispose = () => {
      clearStrategyPerformanceRefreshTimer(store);
      invalidateDashboardLoads(store);
      invalidateAccountOverviewRequests(store);
      invalidateStrategyPerformanceRequests(store);
      store.strategyPerformanceLoading = false;
      originalDispose();
    };
    patchedDashboardStores.add(store);
  }

  return store;
};
