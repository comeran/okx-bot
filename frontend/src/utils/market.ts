import type { KlineQuery } from '@/types/market';

export const timeframeOptions = ['1m', '5m', '15m', '1h', '4h', '1d'] as const;
export const limitOptions = [50, 100, 200, 500] as const;
export const marketTypeOptions = ['spot', 'swap', 'future', 'option'] as const;

export type MarketType = typeof marketTypeOptions[number];

export function marketTypeForSymbol(value: unknown): MarketType | null {
  if (typeof value !== 'string') return null;
  const symbol = value.trim().toUpperCase();
  if (/^[A-Z0-9]+-[A-Z0-9]+-\d{6}-\d+(?:\.\d+)?-[CP]$/.test(symbol)
    || /^[A-Z0-9]+\/[A-Z0-9]+:[A-Z0-9]+-\d{6}-\d+(?:\.\d+)?-[CP]$/.test(symbol)) return 'option';
  if (/^[A-Z0-9]+-[A-Z0-9]+-\d{6}$/.test(symbol)
    || /^[A-Z0-9]+\/[A-Z0-9]+:[A-Z0-9]+-\d{6}$/.test(symbol)) return 'future';
  if (/^[A-Z0-9]+-[A-Z0-9]+-SWAP$/.test(symbol)
    || /^[A-Z0-9]+\/[A-Z0-9]+:[A-Z0-9]+$/.test(symbol)) return 'swap';
  if (/^[A-Z0-9]+[-/][A-Z0-9]+$/.test(symbol)) return 'spot';
  return null;
}

export const fallbackSymbolsByType: Record<string, string[]> = {
  spot: ['BTC-USDT', 'ETH-USDT', 'OKB-USDT', 'SOL-USDT'],
  swap: ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP'],
  future: [],
  option: [],
};

export type MarketKlineQueryError = 'symbolRequired' | 'incompleteRange' | 'invalidRange';

export function formatMarketDateTime(timestamp: number, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(timestamp));
}

export function buildMarketKlineQuery(input: {
  symbol: string;
  timeframe: string;
  limit: number;
  startTime: Date | null;
  endTime: Date | null;
  marketType: string;
}): { query: KlineQuery; rangeQuery: boolean } | { error: MarketKlineQueryError } {
  const symbol = input.symbol.trim();
  if (!symbol) {
    return { error: 'symbolRequired' };
  }

  const hasStart = input.startTime !== null;
  const hasEnd = input.endTime !== null;
  if (hasStart !== hasEnd) {
    return { error: 'incompleteRange' };
  }

  const query: KlineQuery = {
    symbol,
    timeframe: input.timeframe,
    limit: input.limit,
    market_type: input.marketType,
  };

  if (!hasStart && !hasEnd) {
    return { query, rangeQuery: false };
  }

  const startTime = input.startTime?.getTime() ?? 0;
  const endTime = input.endTime?.getTime() ?? 0;
  if (endTime <= startTime) {
    return { error: 'invalidRange' };
  }

  query.start_time = startTime;
  query.end_time = endTime;

  return { query, rangeQuery: true };
}
