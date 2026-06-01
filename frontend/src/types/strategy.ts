export type StrategyStatus = 'running' | 'stopped' | 'error' | string;

export interface StrategySummary {
  name: string;
  status: StrategyStatus;
}

export interface StrategyYamlForm {
  name: string;
  symbol: string;
  timeframe: string;
  capitalPct: number;
  maxPositionPct: number;
  stopLossPct: number;
  takeProfitPct: number;
}
