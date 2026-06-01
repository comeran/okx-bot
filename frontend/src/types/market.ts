export interface Kline {
  symbol: string;
  timeframe: string;
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface RawKline {
  symbol?: string;
  timeframe?: string;
  timestamp?: number | string;
  ts?: number | string;
  open?: number | string;
  high?: number | string;
  low?: number | string;
  close?: number | string;
  volume?: number | string;
  vol?: number | string;
}

export interface RawMarketTicker {
  symbol?: string;
  instId?: string;
  last?: string;
  lastPrice?: string;
  askPx?: string;
  bidPx?: string;
  volume24h?: string;
  vol24h?: string;
  change24h?: string;
}

export interface MarketTicker extends RawMarketTicker {
  symbol: string;
}

export interface KlineQuery {
  symbol: string;
  timeframe: string;
  limit: number;
}
