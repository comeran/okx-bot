import type { Order, Position } from '@/types/dashboard';

export interface StrategyPerformanceRuntimeSummary {
  name: string;
  status: string;
}

export interface StrategyPerformanceMetricRow {
  name?: string;
  strategy?: string;
  status?: string;
  initial_equity?: number | null;
  equity?: number | null;
  return_pct?: number | null;
  realized_pnl?: number | null;
  unrealized_pnl?: number | null;
  fees_paid?: number | null;
  position_notional?: number | null;
  open_positions?: number | null;
  order_count?: number | null;
  filled_order_count?: number | null;
  trade_count?: number | null;
  closed_trade_count?: number | null;
  winning_trade_count?: number | null;
  losing_trade_count?: number | null;
  win_rate?: number | null;
  last_order_at?: number | null;
  positions?: Position[];
  recent_orders?: Order[];
  [key: string]: unknown;
}

export interface StrategyPerformanceRow extends Omit<StrategyPerformanceMetricRow, 'name' | 'strategy'> {
  name: string;
  status: string;
  initial_equity: number | null;
  position_notional: number | null;
  open_positions: number | null;
  order_count: number | null;
  filled_order_count: number | null;
  trade_count: number | null;
  winning_trade_count: number | null;
  losing_trade_count: number | null;
  last_order_at: number | null;
  positions?: Position[];
  recent_orders?: Order[];
}

export interface StrategyPerformanceDisplayRow extends StrategyPerformanceRow {
  positions: Position[];
  recent_orders: Order[];
}

function rowName(row: StrategyPerformanceMetricRow): string {
  return row.name ?? row.strategy ?? '';
}

function normalizeMetricOnlyRow(row: StrategyPerformanceMetricRow): StrategyPerformanceRow {
  const name = rowName(row);
  return {
    name,
    status: 'unknown',
    initial_equity: row.initial_equity ?? null,
    equity: row.equity ?? null,
    return_pct: row.return_pct ?? null,
    realized_pnl: row.realized_pnl ?? null,
    unrealized_pnl: row.unrealized_pnl ?? null,
    fees_paid: row.fees_paid ?? null,
    position_notional: row.position_notional ?? null,
    open_positions: row.open_positions ?? null,
    order_count: row.order_count ?? null,
    filled_order_count: row.filled_order_count ?? null,
    trade_count: row.trade_count ?? null,
    closed_trade_count: row.closed_trade_count ?? null,
    winning_trade_count: row.winning_trade_count ?? null,
    losing_trade_count: row.losing_trade_count ?? null,
    win_rate: row.win_rate ?? null,
    last_order_at: row.last_order_at ?? null,
    positions: row.positions,
    recent_orders: row.recent_orders,
  };
}

function normalizeRuntimeOnlyRow(summary: StrategyPerformanceRuntimeSummary): StrategyPerformanceRow {
  return {
    name: summary.name,
    status: summary.status,
    initial_equity: null,
    equity: null,
    return_pct: null,
    realized_pnl: null,
    unrealized_pnl: null,
    fees_paid: null,
    position_notional: null,
    open_positions: null,
    order_count: null,
    filled_order_count: null,
    trade_count: null,
    closed_trade_count: null,
    winning_trade_count: null,
    losing_trade_count: null,
    win_rate: null,
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
    initial_equity: performanceRow.initial_equity ?? null,
    equity: performanceRow.equity ?? null,
    return_pct: performanceRow.return_pct ?? null,
    realized_pnl: performanceRow.realized_pnl ?? null,
    unrealized_pnl: performanceRow.unrealized_pnl ?? null,
    fees_paid: performanceRow.fees_paid ?? null,
    position_notional: performanceRow.position_notional ?? null,
    open_positions: performanceRow.open_positions ?? null,
    order_count: performanceRow.order_count ?? null,
    filled_order_count: performanceRow.filled_order_count ?? null,
    trade_count: performanceRow.trade_count ?? null,
    closed_trade_count: performanceRow.closed_trade_count ?? null,
    winning_trade_count: performanceRow.winning_trade_count ?? null,
    losing_trade_count: performanceRow.losing_trade_count ?? null,
    win_rate: performanceRow.win_rate ?? null,
    last_order_at: performanceRow.last_order_at ?? null,
    positions: performanceRow.positions,
    recent_orders: performanceRow.recent_orders,
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
    positions: Array.isArray(row.positions)
      ? row.positions
      : positions.filter((position) => position.strategy === row.name),
    recent_orders: Array.isArray(row.recent_orders)
      ? row.recent_orders
      : newestFirst(orders.filter((order) => order.strategy === row.name)).slice(0, 20),
  }));
}
