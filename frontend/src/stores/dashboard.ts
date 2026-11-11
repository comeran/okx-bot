import { defineStore } from 'pinia';
import { watch } from 'vue';

import { fetchExchangeSnapshot, fetchStrategyPerformance } from '@/services/trading';
import { matchesExecutionContext, useAccountContextStore } from './accountContext';
import type { ExchangeSnapshot } from '@/types/executionContext';
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
type TradingStateField = 'account' | 'positions' | 'orders';
const webSocketStateVersions = new WeakMap<object, Record<TradingStateField, number>>();

function currentWebSocketStateVersions(store: object): Record<TradingStateField, number> {
  return webSocketStateVersions.get(store) ?? { account: 0, positions: 0, orders: 0 };
}

function markWebSocketStateUpdated(store: object, field: TradingStateField) {
  const versions = currentWebSocketStateVersions(store);
  webSocketStateVersions.set(store, { ...versions, [field]: versions[field] + 1 });
}

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

function payloadFor(message: DashboardWebSocketMessage, key: string): unknown {
  const record = message as Record<string, unknown>;
  return record[key] ?? record.data;
}

function appliesToExchangeState(message: { scope?: unknown; context?: unknown }): boolean {
  // Global state is trusted only from an explicit exchange scope. Legacy
  // strategy/unscoped messages stay in history and cannot overwrite the global view.
  return message.scope === 'exchange' && matchesExecutionContext(useAccountContextStore().context, message.context);
}

function isFinancialMessage(message: DashboardWebSocketMessage): boolean {
  return message.scope === 'exchange' || ['account', 'positions', 'orders', 'trade', 'trades', 'risk_event'].includes(message.type)
    || (message.type === 'snapshot' && snapshotTouchesTradingState(isRecord(message.data) ? message.data : message));
}

function isPaperStrategyFinancialMessage(message: DashboardWebSocketMessage): boolean {
  return message.scope === 'strategy' && typeof message.strategy === 'string'
    && message.source === 'paper' && message.runtime_mode === 'paper'
    && ['account', 'positions', 'orders', 'risk_event'].includes(message.type);
}

function validateSnapshot(snapshot: ExchangeSnapshot): void {
  if (!appliesToExchangeState(snapshot) || !isRecord(snapshot.data)
    || !(snapshot.data.account === null || isAccountSummary(snapshot.data.account))
    || !isPositionArray(snapshot.data.positions) || !isOrderArray(snapshot.data.orders)) {
    throw new Error('Account snapshot context mismatch or invalid snapshot');
  }
}

function snapshotTouchesTradingState(snapshot: DashboardSnapshot | Record<string, unknown>): boolean {
  return 'account' in snapshot || 'positions' in snapshot || 'orders' in snapshot
    || 'trade' in snapshot || 'trades' in snapshot;
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
    resetAccountState() {
      invalidateDashboardLoads(this);
      nextDashboardLoadGeneration(this);
      invalidateAccountOverviewRequests(this);
      invalidateStrategyPerformanceRequests(this);
      clearStrategyPerformanceRefreshTimer(this);
      this.account = null;
      this.positions = [];
      this.orders = [];
      this.strategyPerformance = [];
      this.websocketMessages = [];
      this.loading = false;
      this.accountLoading = false;
      this.strategyPerformanceLoading = false;
      this.error = null;
      this.accountError = null;
      this.strategyPerformanceError = null;
      this.lastUpdatedAt = null;
    },
    async loadInitialData() {
      const authority = useAccountContextStore();
      const contextRequest = authority.ensure();
      const accountEpoch = authority.epoch;
      const loadGeneration = nextDashboardLoadGeneration(this);
      const loadRequestId = nextDashboardLoadRequestId(this);
      const stateVersions = currentWebSocketStateVersions(this);
      const isCurrentLoad = () => (
        dashboardLoadRequestSequences.get(this) === loadRequestId
        && currentDashboardLoadGeneration(this) === loadGeneration
        && authority.epoch === accountEpoch
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
        const context = await contextRequest;
        if (authority.epoch !== accountEpoch) return;
        if (!context) throw new Error(authority.error ?? 'Account context unavailable');
        if (context.available && context.account_scope_id) {
          const snapshot = await fetchExchangeSnapshot(context.account_scope_id);
          if (!isCurrentLoad()) return;
          validateSnapshot(snapshot);
          const currentVersions = currentWebSocketStateVersions(this);
          if (currentVersions.positions === stateVersions.positions) this.positions = snapshot.data.positions ?? [];
          if (currentVersions.account === stateVersions.account) this.account = snapshot.data.account ?? null;
          if (currentVersions.orders === stateVersions.orders) this.orders = snapshot.data.orders ?? [];
        }

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
      const authority = useAccountContextStore();
      const contextRequest = authority.ensure();
      const accountEpoch = authority.epoch;
      const accountRequestGeneration = currentDashboardLoadGeneration(this);
      const accountRequestId = nextAccountOverviewRequestId(this);
      const accountStateVersion = currentWebSocketStateVersions(this).account;
      const isCurrentAccountRequest = () => (
        accountOverviewRequestSequences.get(this) === accountRequestId
        && (accountOverviewRequestGenerations.get(this) ?? 0) === accountRequestGeneration
        && currentDashboardLoadGeneration(this) === accountRequestGeneration
        && authority.epoch === accountEpoch
      );
      accountOverviewRequestGenerations.set(this, accountRequestGeneration);
      accountOverviewPendingRequests.set(this, accountRequestId);

      this.accountLoading = true;
      this.accountError = null;

      try {
        const context = await contextRequest;
        if (authority.epoch !== accountEpoch) return;
        if (!context) throw new Error(authority.error ?? 'Account context unavailable');
        if (!context.available || !context.account_scope_id) return;
        const snapshot = await fetchExchangeSnapshot(context.account_scope_id);
        if (isCurrentAccountRequest()) validateSnapshot(snapshot);
        if (!isCurrentAccountRequest()) {
          return;
        }

        if (currentWebSocketStateVersions(this).account === accountStateVersion) {
          this.account = snapshot.data.account ?? null;
        }
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
      if (isPaperStrategyFinancialMessage(message)) {
        this.scheduleStrategyPerformanceRefresh();
        return;
      }
      if (isFinancialMessage(message) && !appliesToExchangeState(message)) return;
      this.websocketMessages.unshift(message);
      this.websocketMessages = this.websocketMessages.slice(0, WEBSOCKET_MESSAGE_HISTORY_LIMIT);
      this.applyWebSocketMessage(message);
    },
    applyWebSocketMessage(message: DashboardWebSocketMessage) {
      if (isPaperStrategyFinancialMessage(message)) {
        this.scheduleStrategyPerformanceRefresh();
        return;
      }
      if (isFinancialMessage(message) && !appliesToExchangeState(message)) return;
      switch (message.type) {
        case 'account': {
          const account = payloadFor(message, 'account');
          if (appliesToExchangeState(message) && isAccountSummary(account)) {
            markWebSocketStateUpdated(this, 'account');
            this.account = account;
          }
          this.scheduleStrategyPerformanceRefresh();
          break;
        }
        case 'positions': {
          const positions = payloadFor(message, 'positions');
          if (appliesToExchangeState(message) && isPositionArray(positions)) {
            markWebSocketStateUpdated(this, 'positions');
            this.positions = positions;
          }
          this.scheduleStrategyPerformanceRefresh();
          break;
        }
        case 'orders': {
          const orders = payloadFor(message, 'orders');
          if (appliesToExchangeState(message) && isOrderArray(orders)) {
            markWebSocketStateUpdated(this, 'orders');
            this.orders = orders;
          }
          this.scheduleStrategyPerformanceRefresh();
          break;
        }
        case 'snapshot': {
          const snapshot = isRecord(message.data) ? message.data : message;
          if (appliesToExchangeState(message)) {
            this.applySnapshot(snapshot);
          }
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
        markWebSocketStateUpdated(this, 'positions');
        this.positions = positions;
      }

      if (isOrderArray(snapshot.orders)) {
        markWebSocketStateUpdated(this, 'orders');
        this.orders = orders;
      }

      if ('account' in snapshot) {
        if (snapshot.account === null) {
          markWebSocketStateUpdated(this, 'account');
          this.account = null;
        } else if (isAccountSummary(snapshot.account)) {
          markWebSocketStateUpdated(this, 'account');
          this.account = snapshot.account;
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
      const authority = useAccountContextStore();
      const contextRequest = authority.ensure();
      const accountEpoch = authority.epoch;
      const requestGeneration = nextStrategyPerformanceRequestGeneration(this);
      const requestId = nextStrategyPerformanceRequestId(this);
      this.strategyPerformanceLoading = true;

      try {
        const context = await contextRequest;
        if (authority.epoch !== accountEpoch) return;
        if (!context) throw new Error(authority.error ?? 'Account context unavailable');
        const performance = await fetchStrategyPerformance(context.available ? context.account_scope_id : null);
        if (authority.epoch !== accountEpoch || !isCurrentStrategyPerformanceRequest(this, requestId, requestGeneration)) {
          return;
        }
        this.strategyPerformance = performance;
        this.strategyPerformanceError = null;
      } catch (error) {
        if (authority.epoch !== accountEpoch || !isCurrentStrategyPerformanceRequest(this, requestId, requestGeneration)) {
          return;
        }
        this.strategyPerformanceError = errorMessage(
          error,
          'Failed to load strategy performance',
        );
      } finally {
        if (authority.epoch === accountEpoch && isCurrentStrategyPerformanceRequest(this, requestId, requestGeneration)) {
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
    const authority = useAccountContextStore();
    const stopAccountWatch = watch(() => authority.epoch, () => store.resetAccountState(), { flush: 'sync' });
    store.$dispose = () => {
      stopAccountWatch();
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
