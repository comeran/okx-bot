import type { DashboardWebSocketMessage } from '@/types/dashboard';

export const EMPTY_RUNTIME_VALUE = '—';

export function formatRuntimeCurrency(value?: number): string {
  if (value === undefined || !Number.isFinite(value)) {
    return EMPTY_RUNTIME_VALUE;
  }

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatTickerPrice(value?: number | string): string {
  if (value === undefined || value === '') {
    return EMPTY_RUNTIME_VALUE;
  }

  const numberValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numberValue)) {
    return EMPTY_RUNTIME_VALUE;
  }

  return numberValue.toLocaleString('en-US', { maximumFractionDigits: 4 });
}

export function formatRuntimeNumber(value?: number): string {
  if (value === undefined || !Number.isFinite(value)) {
    return EMPTY_RUNTIME_VALUE;
  }

  return value.toLocaleString('en-US', { maximumFractionDigits: 8 });
}

export function formatRuntimeText(value?: string): string {
  return value || EMPTY_RUNTIME_VALUE;
}

export function formatRuntimeTime(timestamp?: number, locale = 'en-US'): string {
  if (timestamp === undefined || !Number.isFinite(timestamp)) {
    return EMPTY_RUNTIME_VALUE;
  }

  return new Date(timestamp).toLocaleString(locale);
}

export function formatRuntimePayloadPreview(message: DashboardWebSocketMessage): string {
  const { type, received_at, ...payload } = message;

  if (Object.keys(payload).length === 0) {
    return EMPTY_RUNTIME_VALUE;
  }

  const preview = JSON.stringify(payload);
  return preview.length > 120 ? `${preview.slice(0, 120)}…` : preview;
}

export function getDashboardStrategyStatusTagType(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'running') return 'success';
  if (status === 'stopped') return 'info';
  if (status === 'error') return 'danger';
  return 'warning';
}
