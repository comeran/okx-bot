import type { Kline } from '@/types/market';

export interface BacktestRequest {
  strategy: string;
  symbol: string;
  timeframe: string;
  start_time: number;
  end_time: number;
  initial_capital: number;
}

export interface BacktestMetrics {
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
}

export interface BacktestResult extends BacktestRequest, BacktestMetrics {
  id: string;
  created_at: number;
}

export interface BacktestMarker {
  symbol: string;
  side: 'buy' | 'sell';
  timestamp: number;
  price: number;
  amount: number;
  fee: number;
  pnl: number;
}

export interface BacktestEquityPoint {
  seq: number;
  timestamp: number;
  equity: number;
  drawdown: number;
}

export interface BacktestRejectedOrder {
  symbol: string;
  side: string;
  timestamp: number;
  amount: number;
  price: number | null;
  reason: string;
}

export interface BacktestResultDetail {
  result: BacktestResult;
  klines: Kline[];
  markers: BacktestMarker[];
  equity_curve?: BacktestEquityPoint[];
  rejected_orders?: BacktestRejectedOrder[];
}
