export interface TradeRecord {
  id: number | null;
  strategy: string;
  symbol: string;
  side: string;
  amount: number;
  price: number;
  fee: number;
  timestamp: number;
  notional_value?: number | null;
}

export interface TradesQuery {
  strategy?: string;
}
