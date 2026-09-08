import type { Order, Position } from '@/types/dashboard';

export interface StrategyPerformanceRuntimeSummary {
  name: string;
  status: string;
}

export interface StrategyPerformanceMetricRow {
  name?: string;
  strategy?: string;
  status?: string;
  equity?: number | null;
  return_pct?: number | null;
  realized_pnl?: number | null;
  unrealized_pnl?: number | null;
  position_notional?: number | null;
  open_positions?: number | null;
  closed_trade_count?: number | null;
  win_rate?: number | null;
  fees_paid?: number | null;
  order_count?: number | null;
  filled_order_count?: number | null;
  last_order_at?: number | null;
  [key: string]: unknown;
}

export interface StrategyPerformanceRow extends Omit<StrategyPerformanceMetricRow, 'name' | 'strategy'> {
  name: string;
  status: string;
  position_notional: number | null;
  open_positions: number;
  order_count: number;
  filled_order_count: number;
  last_order_at: number | null;
}

export interface StrategyPerformanceDisplayRow extends StrategyPerformanceRow {
  positions: Position[];
  recent_orders: Order[];
}

function rowName(row: StrategyPerformanceMetricRow): string {
  return row.name ?? row.strategy ?? '';
}

function normalizeOpenPositionCount(value: number | null | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function normalizeMetricOnlyRow(row: StrategyPerformanceMetricRow): StrategyPerformanceRow {
  const name = rowName(row);
  return {
    name,
    status: 'unknown',
    equity: row.equity ?? null,
    return_pct: row.return_pct ?? null,
    realized_pnl: row.realized_pnl ?? null,
    unrealized_pnl: row.unrealized_pnl ?? null,
    position_notional: row.position_notional ?? null,
    open_positions: normalizeOpenPositionCount(row.open_positions),
    closed_trade_count: row.closed_trade_count ?? null,
    win_rate: row.win_rate ?? null,
    fees_paid: row.fees_paid ?? null,
    order_count: row.order_count ?? 0,
    filled_order_count: row.filled_order_count ?? 0,
    last_order_at: row.last_order_at ?? null,
  };
}

function normalizeRuntimeOnlyRow(summary: StrategyPerformanceRuntimeSummary): StrategyPerformanceRow {
  return {
    name: summary.name,
    status: summary.status,
    equity: 0,
    return_pct: null,
    realized_pnl: 0,
    unrealized_pnl: 0,
    position_notional: 0,
    open_positions: 0,
    closed_trade_count: 0,
    win_rate: null,
    fees_paid: 0,
    order_count: 0,
    filled_order_count: 0,
    last_order_at: null,
  };
}

function mergeMatchedRow(
  summary: StrategyPerformanceRuntimeSummary,
  performanceRow: StrategyPerformanceMetricRow,
): StrategyPerformanceRow {
  return {
    name: summary.name,
    status: summary.status,
    equity: performanceRow.equity ?? null,
    return_pct: performanceRow.return_pct ?? null,
    realized_pnl: performanceRow.realized_pnl ?? null,
    unrealized_pnl: performanceRow.unrealized_pnl ?? null,
    position_notional: performanceRow.position_notional ?? null,
    open_positions: normalizeOpenPositionCount(performanceRow.open_positions),
    closed_trade_count: performanceRow.closed_trade_count ?? null,
    win_rate: performanceRow.win_rate ?? null,
    fees_paid: performanceRow.fees_paid ?? null,
    order_count: performanceRow.order_count ?? 0,
    filled_order_count: performanceRow.filled_order_count ?? 0,
    last_order_at: performanceRow.last_order_at ?? null,
  };
}

export function mergeStrategyPerformanceRows(
  runtimeSummaries: readonly StrategyPerformanceRuntimeSummary[],
  performanceRows: readonly StrategyPerformanceMetricRow[],
): StrategyPerformanceRow[] {
  const performanceByName = new Map<string, StrategyPerformanceMetricRow>();

  for (const row of performanceRows) {
    const name = rowName(row);
    if (name) {
      performanceByName.set(name, row);
    }
  }

  const mergedRows = runtimeSummaries.map((summary) => {
    const performanceRow = performanceByName.get(summary.name);
    if (performanceRow === undefined) {
      return normalizeRuntimeOnlyRow(summary);
    }

    performanceByName.delete(summary.name);
    return mergeMatchedRow(summary, performanceRow);
  });

  const metricOnlyRows = Array.from(performanceByName.values())
    .map((row) => normalizeMetricOnlyRow(row))
    .sort((left, right) => left.name.localeCompare(right.name));

  return [...mergedRows, ...metricOnlyRows];
}

function newestFirst<T extends { timestamp?: number }>(items: readonly T[]): T[] {
  return [...items].sort((left, right) => (right.timestamp ?? -Infinity) - (left.timestamp ?? -Infinity));
}

export function enrichStrategyPerformanceRows(
  runtimeSummaries: readonly StrategyPerformanceRuntimeSummary[],
  performanceRows: readonly StrategyPerformanceMetricRow[],
  positions: readonly Position[],
  orders: readonly Order[],
): StrategyPerformanceDisplayRow[] {
  return mergeStrategyPerformanceRows(runtimeSummaries, performanceRows).map((row) => ({
    ...row,
    positions: positions.filter((position) => position.strategy === row.name),
    recent_orders: newestFirst(orders.filter((order) => order.strategy === row.name)).slice(0, 20),
  }));
}
