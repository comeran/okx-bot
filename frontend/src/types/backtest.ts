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

export interface BacktestResult extends BacktestRequest, BacktestMetrics {}
