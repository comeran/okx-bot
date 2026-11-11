import { createPinia } from 'pinia';
import { reactive } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@/test-utils/mount';
import { useAccountContextStore } from '@/stores/accountContext';
import StrategyAllocationReadiness from './StrategyAllocationReadiness.vue';
const api = vi.hoisted(() => ({ readiness: vi.fn() }));
vi.mock('@/services/strategyAllocations', () => ({ getStrategyAllocationReadiness: api.readiness }));
function context(id = 'account-a') {
  return { account_scope_id: id, exchange: 'okx' as const, environment: 'sandbox' as const,
    market_type: 'spot', identity_verified: false as const, available: true, reason: null,
    execution_ready: false as const };
}
function envelope(revision = 2, symbol = 'BTC-USDT') {
  return {
    execution_context: context(),
    context: { available: true, reason: null, scope_id: 'allocation-a', exchange: 'okx' as const,
      environment: 'sandbox' as const, account_fingerprint: 'fingerprint', identity_verified: false as const,
      market_type: 'spot', execution_mode: 'live', draft_only: true as const, execution_ready: false as const },
    strategy: 'alpha', symbol, currency: 'USDT', lifecycle_status: 'stopped', can_edit: true,
    draft: { strategy: 'alpha', symbol, currency: 'USDT', budget: 100, revision,
      status: 'draft' as const, scope_id: 'allocation-a', environment: 'sandbox' as const,
      account_fingerprint: 'fingerprint', market_type: 'spot', created_at: 1, updated_at: 2 },
  };
}
function result(revision = 2, symbol = 'BTC-USDT', amount = '100.000000000000000001') {
  return { execution_context: context(), allocation_scope_id: 'allocation-a', draft_revision: revision,
    strategy: 'alpha', symbol, currency: 'USDT', budget: amount, evaluated_at: 1,
    capacity: { observed_quote_available: '1000.000000000000000009', proposed_total: amount,
      comparison: 'within_observed', trusted: false }, ledger: null,
    checks: [{ code: 'quote_capacity', status: 'unknown' }, { code: 'activation_implemented', status: 'blocked' }],
    can_activate: false, execution_ready: false };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function setup(props: Record<string, unknown> = {}) {
  const pinia = createPinia();
  const store = useAccountContextStore(pinia);
  store.context = context();
  const wrapper = await mount(StrategyAllocationReadiness, {
    locale: 'en', plugins: [pinia], props: { allocation: envelope(), status: 'stopped', ...props },
  });
  for (let i = 0; i < 5; i += 1) await wrapper.flush();
  return { wrapper, store };
}
async function flush(wrapper: Awaited<ReturnType<typeof setup>>['wrapper']) {
  for (let i = 0; i < 5; i += 1) await wrapper.flush();
}
beforeEach(() => { vi.resetAllMocks(); api.readiness.mockResolvedValue(result()); });
describe('saved allocation readiness', () => {
  it('requests saved preconditions and renders exact strings without an activation action', async () => {
    const { wrapper } = await setup();
    expect(api.readiness).toHaveBeenCalledWith('alpha', {
      expected_account_scope_id: 'account-a', expected_scope_id: 'allocation-a',
      expected_revision: 2, expected_symbol: 'BTC-USDT',
    });
    for (const text of ['100.000000000000000001', '1000.000000000000000009', 'Unknown', 'Blocked', 'Saved draft', 'not initialized', 'Checked at']) expect(wrapper.text()).toContain(text);
    expect(wrapper.findAll((node) => node.type === 'button')).toHaveLength(1);
    wrapper.unmount();
  });
  it('skips a missing draft and an unavailable account', async () => {
    const { wrapper, store } = await setup({ allocation: { ...envelope(), draft: null } });
    expect(api.readiness).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Save a draft');
    store.context = { ...context(), available: false, account_scope_id: null };
    await wrapper.updateProps({ allocation: envelope() });
    expect(api.readiness).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it('distinguishes request errors from unknown evidence', async () => {
    api.readiness.mockRejectedValue(new Error('unavailable'));
    const { wrapper } = await setup();
    expect(wrapper.text()).toContain('Could not load readiness');
    expect(wrapper.findAll((node) => node.props.role === 'alert')).toHaveLength(1);
    expect(wrapper.text()).not.toContain('100.000000000000000001');
    wrapper.unmount();
  });
  it('renders prepared ledger balances, reservations, cost and fees as strings', async () => {
    api.readiness.mockResolvedValue({ ...result(), ledger: {
      strategy: 'alpha', symbol: 'BTC-USDT', currency: 'USDT', base_currency: 'BTC', status: 'prepared',
      quote_balance: '88.000000000000000007', quote_reserved: '8', quote_available: '80.000000000000000007',
      base_balance: '0.125', base_reserved: '0.025', base_available: '0.1', cost_basis_quote: '12',
      realized_pnl_quote: '-1', fees: { BTC: '0.000000000000000001', USDT: '0.2' },
    } });
    const { wrapper } = await setup();
    for (const amount of ['88.000000000000000007', '80.000000000000000007', '0.125', '0.025', '0.000000000000000001']) expect(wrapper.text()).toContain(amount);
    expect(wrapper.text()).toContain('Prepared');
    wrapper.unmount();
  });
  it('fences A to B to A and clears visible evidence on invalidation', async () => {
    const old = deferred<ReturnType<typeof result>>();
    api.readiness.mockReturnValueOnce(old.promise).mockResolvedValue(result(2, 'BTC-USDT', 'fresh-value'));
    const { wrapper, store } = await setup();
    store.invalidate(); store.context = context('account-b');
    store.invalidate(); store.context = context();
    await flush(wrapper);
    old.resolve(result(2, 'BTC-USDT', 'obsolete'));
    await flush(wrapper);
    expect(wrapper.text()).not.toContain('obsolete');
    expect(wrapper.text()).toContain('fresh-value');
    store.invalidate(); await flush(wrapper);
    expect(wrapper.text()).not.toContain('fresh-value');
    wrapper.unmount();
  });
  it.each(['success', 'error'])('fences old %s and finally after revision/symbol change', async (outcome) => {
    const old = deferred<ReturnType<typeof result>>();
    const current = deferred<ReturnType<typeof result>>();
    api.readiness.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    const { wrapper } = await setup();
    await wrapper.updateProps({ allocation: envelope(4, 'ETH-USDT') });
    if (outcome === 'success') old.resolve(result(2, 'BTC-USDT', 'obsolete'));
    else old.reject(new Error('obsolete'));
    await flush(wrapper);
    expect(wrapper.text()).not.toContain('obsolete');
    expect(wrapper.getByTestId('readiness-refresh').props.disabled).toBe(true);
    current.resolve(result(4, 'ETH-USDT', 'current')); await flush(wrapper);
    expect(wrapper.text()).toContain('current');
    wrapper.unmount();
  });
  it('delete and recreate cannot revive a prior draft response', async () => {
    const old = deferred<ReturnType<typeof result>>();
    api.readiness.mockReturnValueOnce(old.promise).mockResolvedValue(result(4, 'BTC-USDT', 'recreated-value'));
    const { wrapper } = await setup();
    await wrapper.updateProps({ allocation: { ...envelope(), draft: null } });
    await wrapper.updateProps({ allocation: envelope(4) });
    old.resolve(result(2, 'BTC-USDT', 'deleted-value')); await flush(wrapper);
    expect(wrapper.text()).toContain('recreated-value'); expect(wrapper.text()).not.toContain('deleted-value');
    wrapper.unmount();
  });
  it('lifecycle changes invalidate stopped evidence', async () => {
    const old = deferred<ReturnType<typeof result>>(); api.readiness.mockReturnValueOnce(old.promise);
    const { wrapper } = await setup(); await wrapper.updateProps({ status: 'running' });
    old.resolve(result(2, 'BTC-USDT', 'obsolete')); await flush(wrapper);
    expect(wrapper.text()).not.toContain('obsolete'); expect(api.readiness).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });
  it('rejects wrong response context and never changes account authority', async () => {
    api.readiness.mockResolvedValue({ ...result(), execution_context: context('different') });
    const { wrapper, store } = await setup();
    expect(wrapper.text()).toContain('Could not load readiness');
    expect(store.context?.account_scope_id).toBe('account-a'); wrapper.unmount();
  });
  it('does not query an envelope belonging to another authoritative account', async () => {
    const { wrapper } = await setup({ allocation: { ...envelope(), execution_context: context('other') } });
    expect(api.readiness).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it.each([
    { allocation_scope_id: 'other' }, { draft_revision: 99 }, { symbol: 'ETH-USDT' },
  ])('rejects changed saved response binding %o', async (change) => {
    api.readiness.mockResolvedValue({ ...result(), ...change });
    const { wrapper } = await setup();
    expect(wrapper.text()).toContain('Could not load readiness');
    expect(wrapper.text()).not.toContain('100.000000000000000001');
    wrapper.unmount();
  });
  it('ignores completion after unmount', async () => {
    const pending = deferred<ReturnType<typeof result>>(); api.readiness.mockReturnValueOnce(pending.promise);
    const { wrapper } = await setup(); wrapper.unmount(); pending.reject(new Error('obsolete'));
    await flush(wrapper); expect(wrapper.text()).not.toContain('obsolete');
  });
  it('removes an already visible result when lifecycle status changes', async () => {
    const { wrapper } = await setup();
    expect(wrapper.text()).toContain('100.000000000000000001');
    await wrapper.updateProps({ status: 'starting' });
    expect(wrapper.text()).not.toContain('100.000000000000000001');
    expect(wrapper.text()).toContain('Only a stopped strategy');
    wrapper.unmount();
  });

  it('clears old readiness if envelope symbol changes with an unchanged saved draft', async () => {
    const saved = reactive(envelope());
    const { wrapper } = await setup({ allocation: saved });
    expect(wrapper.text()).toContain('100.000000000000000001');
    saved.symbol = 'ETH-USDT';
    await flush(wrapper);
    expect(wrapper.text()).not.toContain('100.000000000000000001');
    expect(api.readiness).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

});
