import { defineStore } from 'pinia';

import type {
  AccountSummary,
  DashboardSnapshot,
  DashboardWebSocketMessage,
  MarketTicker,
  Order,
  Position,
  StrategySummary,
} from '@/types/dashboard';

const WEBSOCKET_MESSAGE_HISTORY_LIMIT = 20;

interface DashboardState {
  account: AccountSummary | null;
  positions: Position[];
  orders: Order[];
  strategies: StrategySummary[];
  strategyErrors: Record<string, string>;
  tickers: MarketTicker[];
  websocketConnected: boolean;
  websocketMessages: DashboardWebSocketMessage[];
  loading: boolean;
  error: string | null;
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

function isStrategySummary(value: unknown): value is StrategySummary {
  return isRecord(value) && typeof value.name === 'string' && typeof value.status === 'string';
}

function isStrategyArray(value: unknown): value is StrategySummary[] {
  return Array.isArray(value) && value.every(isStrategySummary);
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

function upsertStrategyStatus(
  strategies: StrategySummary[],
  name: string,
  status: string,
): StrategySummary[] {
  const existingIndex = strategies.findIndex((strategy) => strategy.name === name);
  if (existingIndex === -1) {
    return [...strategies, { name, status }];
  }

  return strategies.map((strategy, index) => (
    index === existingIndex ? { ...strategy, status } : strategy
  ));
}

export const useDashboardStore = defineStore('dashboard', {
  state: (): DashboardState => ({
    account: null,
    positions: [],
    orders: [],
    strategies: [],
    strategyErrors: {},
    tickers: [],
    websocketConnected: false,
    websocketMessages: [],
    loading: false,
    error: null,
    tickerError: null,
    lastUpdatedAt: null,
  }),
  getters: {
    activeStrategyCount: (state) => state.strategies.filter((strategy) => strategy.status === 'running').length,
  },
  actions: {
    async loadInitialData() {
      this.loading = true;
      this.error = null;
      this.tickerError = null;

      try {
        const [account, positions, orders, strategies] = await Promise.all([
          fetchJson<AccountSummary>('/api/trading/account'),
          fetchJson<Position[]>('/api/trading/positions'),
          fetchJson<Order[]>('/api/trading/orders'),
          fetchJson<StrategySummary[]>('/api/strategies'),
        ]);

        this.account = normalizeAccount(account, positions);
        this.positions = positions;
        this.orders = orders;
        this.strategies = strategies;

        try {
          const tickers = await fetchJson<MarketTicker[]>('/api/market/tickers');
          this.tickers = isMarketTickerArray(tickers) ? tickers : [];
        } catch (error) {
          this.tickerError = error instanceof Error ? error.message : 'Failed to load market tickers';
          this.tickers = [];
        }

        this.lastUpdatedAt = Date.now();
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Failed to load dashboard data';
      } finally {
        this.loading = false;
      }
    },
    setWebSocketConnected(connected: boolean) {
      this.websocketConnected = connected;
    },
    addWebSocketMessage(message: DashboardWebSocketMessage) {
      const receivedMessage = {
        ...message,
        received_at: message.received_at ?? Date.now(),
      };
      this.websocketMessages.unshift(receivedMessage);
      this.websocketMessages = this.websocketMessages.slice(0, WEBSOCKET_MESSAGE_HISTORY_LIMIT);
      this.applyWebSocketMessage(receivedMessage);
    },
    applyWebSocketMessage(message: DashboardWebSocketMessage) {
      switch (message.type) {
        case 'account': {
          const account = payloadFor(message, 'account');
          if (isAccountSummary(account)) {
            this.account = normalizeAccount(account, this.positions);
          }
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
          break;
        }
        case 'orders': {
          const orders = payloadFor(message, 'orders');
          if (isOrderArray(orders)) {
            this.orders = orders;
          }
          break;
        }
        case 'strategies': {
          const strategies = payloadFor(message, 'strategies');
          if (isStrategyArray(strategies)) {
            this.strategies = strategies;
          }
          break;
        }
        case 'strategy_status': {
          if (typeof message.strategy === 'string' && typeof message.status === 'string') {
            this.strategies = upsertStrategyStatus(this.strategies, message.strategy, message.status);
            if (message.status === 'running') {
              const { [message.strategy]: _cleared, ...remainingErrors } = this.strategyErrors;
              this.strategyErrors = remainingErrors;
            }
          }
          break;
        }
        case 'strategy_error': {
          if (typeof message.strategy === 'string' && typeof message.error === 'string') {
            this.strategyErrors = {
              ...this.strategyErrors,
              [message.strategy]: message.error,
            };
          }
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

      if (isStrategyArray(snapshot.strategies)) {
        this.strategies = snapshot.strategies;
      }
    },
  },
});
