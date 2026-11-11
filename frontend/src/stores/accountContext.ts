import { defineStore } from 'pinia';
import { fetchTradingContext } from '@/services/trading';
import type { ExecutionContext } from '@/types/executionContext';

const pendingContexts = new WeakMap<object, Promise<ExecutionContext | null>>();

export function matchesExecutionContext(
  current: ExecutionContext | null,
  incoming: unknown,
): boolean {
  if (!current?.available || !current.account_scope_id
    || typeof incoming !== 'object' || incoming === null) {
    return false;
  }
  const value = incoming as Partial<ExecutionContext>;
  return value.available === true
    && value.account_scope_id === current.account_scope_id
    && value.exchange === current.exchange
    && value.environment === current.environment
    && value.market_type === current.market_type;
}

function isExecutionContext(value: unknown): value is ExecutionContext {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const context = value as ExecutionContext;
  return context.exchange === 'okx' && ['sandbox', 'live'].includes(context.environment)
    && typeof context.market_type === 'string' && typeof context.available === 'boolean'
    && context.identity_verified === false && context.execution_ready === false
    && (context.available
      ? typeof context.account_scope_id === 'string' && context.account_scope_id.length > 0
      : context.account_scope_id === null);
}

export const useAccountContextStore = defineStore('accountContext', {
  state: () => ({
    context: null as ExecutionContext | null,
    epoch: 0,
    loading: false,
    error: null as string | null,
  }),
  actions: {
    invalidate(): void {
      this.context = null;
      this.epoch += 1;
      this.loading = false;
      this.error = null;
      pendingContexts.delete(this);
    },
    reload(): Promise<ExecutionContext | null> {
      this.invalidate();
      const epoch = this.epoch;
      this.loading = true;
      this.error = null;
      const request = (async () => {
        try {
          const context = await fetchTradingContext();
          if (this.epoch !== epoch) {
            return null;
          }
          if (!isExecutionContext(context)) {
            throw new Error('Invalid account context');
          }
          this.context = context;
          return context;
        } catch (error) {
          if (this.epoch === epoch) {
            this.error = error instanceof Error ? error.message : 'Could not load account context';
          }
          return null;
        } finally {
          if (this.epoch === epoch) {
            this.loading = false;
            pendingContexts.delete(this);
          }
        }
      })();
      pendingContexts.set(this, request);
      return request;
    },
    ensure(): Promise<ExecutionContext | null> {
      return pendingContexts.get(this)
        ?? (this.context ? Promise.resolve(this.context) : this.reload());
    },
  },
});
