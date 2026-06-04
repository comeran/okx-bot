export interface TradeRecord {
  id: number | null;
  strategy: string;
  symbol: string;
  side: string;
  amount: number;
  price: number;
  fee: number;
  timestamp: number;
}

export interface TradesQuery {
  strategy?: string;
}
