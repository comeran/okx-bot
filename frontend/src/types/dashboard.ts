export interface AccountSummary {
  equity: number;
  daily_pnl: number;
  available_balance?: number;
  margin_ratio?: number;
  unrealized_pnl?: number;
}

export type DashboardFieldValue = string | number | boolean | null | undefined;

export interface Position {
  id?: number;
  strategy?: string;
  symbol?: string;
  side?: string;
  amount?: number;
  entry_price?: number;
  leverage?: number;
  timestamp?: number;
  mark_price?: number;
  liquidation_price?: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
  margin?: number;
  margin_mode?: string;
}

export interface Order {
  id?: number;
  order_id?: string;
  strategy?: string;
  symbol?: string;
  side?: string;
  type?: string;
  amount?: number;
  price?: number;
  status?: string;
  fill_price?: number;
  timestamp?: number;
  filled_amount?: number;
  remaining_amount?: number;
  fee?: number;
  reduce_only?: boolean;
}

export interface StrategySummary {
  name: string;
  status: string;
}

export interface MarketTicker {
  symbol: string;
  last?: number | string;
  bidPx?: number | string;
  askPx?: number | string;
  vol24h?: number | string;
}

export interface DashboardSnapshot {
  account?: AccountSummary | null;
  positions?: Position[];
  orders?: Order[];
  strategies?: StrategySummary[];
}

interface DashboardMessageBase {
  received_at?: number;
}

export interface ConnectedDashboardWebSocketMessage extends DashboardMessageBase {
  type: 'connected';
}

export interface RawDashboardWebSocketMessage extends DashboardMessageBase {
  type: 'raw';
  data: string;
}

export interface AccountDashboardWebSocketMessage extends DashboardMessageBase {
  type: 'account';
  account?: AccountSummary;
  data?: AccountSummary;
}

export interface PositionsDashboardWebSocketMessage extends DashboardMessageBase {
  type: 'positions';
  positions?: Position[];
  data?: Position[];
}

export interface OrdersDashboardWebSocketMessage extends DashboardMessageBase {
  type: 'orders';
  orders?: Order[];
  data?: Order[];
}

export interface StrategiesDashboardWebSocketMessage extends DashboardMessageBase {
  type: 'strategies';
  strategies?: StrategySummary[];
  data?: StrategySummary[];
}

export interface SnapshotDashboardWebSocketMessage extends DashboardMessageBase, DashboardSnapshot {
  type: 'snapshot';
  data?: DashboardSnapshot;
}

export interface UnknownDashboardWebSocketMessage extends DashboardMessageBase {
  type: string;
  [key: string]: unknown;
}

export type DashboardWebSocketMessage =
  | ConnectedDashboardWebSocketMessage
  | RawDashboardWebSocketMessage
  | AccountDashboardWebSocketMessage
  | PositionsDashboardWebSocketMessage
  | OrdersDashboardWebSocketMessage
  | StrategiesDashboardWebSocketMessage
  | SnapshotDashboardWebSocketMessage
  | UnknownDashboardWebSocketMessage;
