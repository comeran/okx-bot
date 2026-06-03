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
  tickers: MarketTicker[];
  websocketConnected: boolean;
  websocketMessages: DashboardWebSocketMessage[];
  loading: boolean;
  error: string | null;
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

function payloadFor(message: DashboardWebSocketMessage, key: string): unknown {
  const record = message as Record<string, unknown>;
  return record[key] ?? record.data;
}

export const useDashboardStore = defineStore('dashboard', {
  state: (): DashboardState => ({
    account: null,
    positions: [],
    orders: [],
    strategies: [],
    tickers: [],
    websocketConnected: false,
    websocketMessages: [],
    loading: false,
    error: null,
  }),
  getters: {
    activeStrategyCount: (state) => state.strategies.filter((strategy) => strategy.status === 'running').length,
  },
  actions: {
    async loadInitialData() {
      this.loading = true;
      this.error = null;

      try {
        const [account, positions, orders, strategies] = await Promise.all([
          fetchJson<AccountSummary>('/api/trading/account'),
          fetchJson<Position[]>('/api/trading/positions'),
          fetchJson<Order[]>('/api/trading/orders'),
          fetchJson<StrategySummary[]>('/api/strategies'),
        ]);

        this.account = account;
        this.positions = positions;
        this.orders = orders;
        this.strategies = strategies;

        try {
          const tickers = await fetchJson<MarketTicker[]>('/api/market/tickers');
          this.tickers = isMarketTickerArray(tickers) ? tickers : [];
        } catch {
          this.tickers = [];
        }
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
      this.websocketMessages.unshift(message);
      this.websocketMessages = this.websocketMessages.slice(0, WEBSOCKET_MESSAGE_HISTORY_LIMIT);
      this.applyWebSocketMessage(message);
    },
    applyWebSocketMessage(message: DashboardWebSocketMessage) {
      switch (message.type) {
        case 'account': {
          const account = payloadFor(message, 'account');
          if (isAccountSummary(account)) {
            this.account = account;
          }
          break;
        }
        case 'positions': {
          const positions = payloadFor(message, 'positions');
          if (isPositionArray(positions)) {
            this.positions = positions;
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
      if ('account' in snapshot) {
        if (snapshot.account === null) {
          this.account = null;
        } else if (isAccountSummary(snapshot.account)) {
          this.account = snapshot.account;
        }
      }

      if (isPositionArray(snapshot.positions)) {
        this.positions = snapshot.positions;
      }

      if (isOrderArray(snapshot.orders)) {
        this.orders = snapshot.orders;
      }

      if (isStrategyArray(snapshot.strategies)) {
        this.strategies = snapshot.strategies;
      }
    },
  },
});
