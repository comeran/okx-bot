export interface StrategyPerformance {
  strategy: string;
  initial_equity: number;
  equity: number;
  return_pct: number | null;
  realized_pnl: number;
  unrealized_pnl: number;
  fees_paid: number;
  position_notional: number;
  open_positions: number;
  order_count: number;
  filled_order_count: number;
  trade_count: number;
  closed_trade_count: number;
  winning_trade_count: number;
  losing_trade_count: number;
  win_rate: number | null;
  last_order_at: number | null;
}
