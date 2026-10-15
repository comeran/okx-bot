import type { TradeRecord } from '@/types/trades';

export interface TradeFilters {
  strategy: string;
  symbol: string;
  side: string;
  search: string;
}

export interface TradeSummary {
  totalTrades: number;
  totalNotional: number | null;
  totalFees: number | null;
  positivePnlCount: number | null;
  negativePnlCount: number | null;
}

export interface TradeFilterOptions {
  strategies: string[];
  symbols: string[];
}

export const DEFAULT_TRADE_FILTERS: TradeFilters = {
  strategy: '',
  symbol: '',
  side: '',
  search: '',
};

function normalizeValue(value: string | null | undefined): string {
  return value?.trim().toLowerCase() ?? '';
}

function hasNumericValue(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function sumNumbers(values: Array<number | null | undefined>): number | null {
  let total = 0;
  let hasValue = false;

  for (const value of values) {
    if (!hasNumericValue(value)) continue;
    total += value;
    hasValue = true;
  }

  return hasValue ? Number(total.toFixed(8)) : null;
}

function matchesText(value: string | null | undefined, filter: string): boolean {
  const normalizedFilter = normalizeValue(filter);
  if (!normalizedFilter) return true;
  return normalizeValue(value).includes(normalizedFilter);
}

function containsSearchTerm(trade: TradeRecord, searchTerm: string): boolean {
  const normalizedSearch = normalizeValue(searchTerm);
  if (!normalizedSearch) return true;

  return [trade.strategy, trade.symbol, trade.side]
    .map((value) => normalizeValue(value))
    .some((value) => value.includes(normalizedSearch));
}

export function createTradeFilters(): TradeFilters {
  return { ...DEFAULT_TRADE_FILTERS };
}

export function filterTrades(trades: TradeRecord[], filters: TradeFilters): TradeRecord[] {
  return trades.filter((trade) => (
    matchesText(trade.strategy, filters.strategy)
    && matchesText(trade.symbol, filters.symbol)
    && matchesText(trade.side, filters.side)
    && containsSearchTerm(trade, filters.search)
  ));
}

export function summarizeTrades(trades: TradeRecord[]): TradeSummary {
  const totalNotional = sumNumbers(trades.map((trade) => (
    hasNumericValue(trade.amount) && hasNumericValue(trade.price)
      ? trade.amount * trade.price
      : null
  )));
  const totalFees = sumNumbers(trades.map((trade) => trade.fee));

  return {
    totalTrades: trades.length,
    totalNotional,
    totalFees,
    positivePnlCount: null,
    negativePnlCount: null,
  };
}

export function formatTradeNumber(value: number | null | undefined, locale = 'en', maximumFractionDigits = 8): string {
  if (!hasNumericValue(value)) return '—';
  return value.toLocaleString(locale, { maximumFractionDigits });
}

export function formatTradeTimestamp(value: number | null | undefined, locale = 'en'): string {
  if (!hasNumericValue(value)) return '—';
  return new Date(value).toLocaleString(locale);
}

export function buildTradeFilterOptions(trades: TradeRecord[]): TradeFilterOptions {
  const strategies = Array.from(new Set(trades.map((trade) => trade.strategy).filter((value): value is string => Boolean(value.trim())))).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
  const symbols = Array.from(new Set(trades.map((trade) => trade.symbol).filter((value): value is string => Boolean(value.trim())))).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

  return {
    strategies,
    symbols,
  };
}
