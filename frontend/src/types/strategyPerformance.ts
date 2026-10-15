import type { Order, Position } from '@/types/dashboard';

export interface StrategyPerformance {
  strategy: string;
  initial_equity: number | null;
  equity: number | null;
  return_pct: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  fees_paid: number | null;
  position_notional: number | null;
  open_positions: number | null;
  order_count: number | null;
  filled_order_count: number | null;
  trade_count: number | null;
  closed_trade_count: number | null;
  winning_trade_count: number | null;
  losing_trade_count: number | null;
  win_rate: number | null;
  last_order_at: number | null;
  positions?: Position[];
  recent_orders?: Order[];
}
