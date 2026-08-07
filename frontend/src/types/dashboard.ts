export interface AssetBalance {
  ccy: string;
  cash_bal: number;
  eq: number;
  eq_utd: number;
  avail_bal: number;
  upl: number;
}

export interface AccountSummary {
  equity: number;
  daily_pnl: number;
  cash_balance?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  fees_paid?: number;
  available_balance?: number;
  assets?: AssetBalance[];
  margin_ratio?: number;
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

export interface SnapshotDashboardWebSocketMessage extends DashboardMessageBase, DashboardSnapshot {
  type: 'snapshot';
  data?: DashboardSnapshot & Record<string, unknown>;
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
  | SnapshotDashboardWebSocketMessage
  | UnknownDashboardWebSocketMessage;
