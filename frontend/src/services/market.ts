import axios from 'axios';

import type { Kline, KlineQuery, MarketTicker, RawKline, RawMarketTicker } from '@/types/market';

const toNumber = (value: number | string | undefined, fallback = 0): number => {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : fallback;
  }

  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  return fallback;
};

const normalizeKline = (raw: RawKline, query: KlineQuery): Kline => ({
  symbol: raw.symbol ?? query.symbol,
  timeframe: raw.timeframe ?? query.timeframe,
  timestamp: toNumber(raw.timestamp ?? raw.ts),
  open: toNumber(raw.open),
  high: toNumber(raw.high),
  low: toNumber(raw.low),
  close: toNumber(raw.close),
  volume: toNumber(raw.volume ?? raw.vol),
});

const normalizeTicker = (raw: RawMarketTicker): MarketTicker => ({
  ...raw,
  symbol: raw.symbol || raw.instId || '',
});

export async function fetchKlines(query: KlineQuery): Promise<Kline[]> {
  const { data } = await axios.get<RawKline[]>('/api/market/klines', {
    params: query,
  });

  return data.map((item) => normalizeKline(item, query));
}

export interface MarketBoardLevel {
  price: number;
  amount: number;
}

export interface MarketBoardTrade {
  price: number | null;
  amount: number | null;
  side: string | null;
}

export interface MarketBoardStat {
  available: boolean;
  reason?: string;
  rate?: number | null;
  amount?: number | null;
}

export interface MarketBoard {
  symbol: string;
  market_type: string;
  order_book: { bids: MarketBoardLevel[]; asks: MarketBoardLevel[] };
  trades: MarketBoardTrade[];
  funding_rate: MarketBoardStat;
  open_interest: MarketBoardStat;
}

function normalizeLevel(row: unknown): MarketBoardLevel | null {
  if (!Array.isArray(row) || row.length < 2) return null;
  const price = Number(row[0]);
  const amount = Number(row[1]);
  if (!Number.isFinite(price) || !Number.isFinite(amount)) return null;
  return { price, amount };
}

export async function fetchMarketBoard(symbol: string, marketType: string): Promise<MarketBoard> {
  const { data } = await axios.get<Record<string, unknown>>('/api/market/board', {
    params: { symbol, market_type: marketType },
  });
  const book = (data.order_book ?? {}) as { bids?: unknown[]; asks?: unknown[] };
  const trades = Array.isArray(data.trades) ? data.trades : [];
  const funding = (data.funding_rate ?? {}) as MarketBoardStat;
  const interest = (data.open_interest ?? {}) as MarketBoardStat;
  return {
    symbol: String(data.symbol ?? symbol),
    market_type: String(data.market_type ?? marketType),
    order_book: {
      bids: book.bids?.map(normalizeLevel).filter((row): row is MarketBoardLevel => row !== null) ?? [],
      asks: book.asks?.map(normalizeLevel).filter((row): row is MarketBoardLevel => row !== null) ?? [],
    },
    trades: trades.map((row) => {
      const trade = row as Record<string, unknown>;
      return {
        price: typeof trade.price === 'number' ? trade.price : Number(trade.price) || null,
        amount: typeof trade.amount === 'number' ? trade.amount : Number(trade.amount) || null,
        side: typeof trade.side === 'string' ? trade.side : null,
      };
    }),
    funding_rate: { available: funding.available !== false, reason: funding.reason, rate: funding.rate ?? null },
    open_interest: { available: interest.available !== false, reason: interest.reason, amount: interest.amount ?? null },
  };
}

export async function fetchTickers(marketType = 'spot', symbols?: string[]): Promise<MarketTicker[]> {
  const { data } = await axios.get<RawMarketTicker[]>('/api/market/tickers', {
    params: {
      market_type: marketType,
      symbols,
    },
  });
  return data.map(normalizeTicker);
}
