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

export async function fetchTickers(): Promise<MarketTicker[]> {
  const { data } = await axios.get<RawMarketTicker[]>('/api/market/tickers');
  return data.map(normalizeTicker);
}
