import { createPinia } from 'pinia';
import { useAccountContextStore } from '@/stores/accountContext';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@/test-utils/mount';
import StrategyAllocationPanel from './StrategyAllocationPanel.vue';
const api = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), remove: vi.fn(), start: vi.fn(), readiness: vi.fn() }));
vi.mock('@/services/strategyAllocations', () => ({ getStrategyAllocation: api.get, saveStrategyAllocation: api.save, deleteStrategyAllocation: api.remove, getStrategyAllocationReadiness: api.readiness }));
vi.mock('@/services/strategies', () => ({ startStrategy: api.start }));
function executionContext(id = 'execution-a') {
  return { account_scope_id: id, exchange: 'okx' as const, environment: 'sandbox' as const,
    market_type: 'spot', available: true, reason: null, identity_verified: false as const, execution_ready: false as const };
}
let accountStore: ReturnType<typeof useAccountContextStore>;
function envelope(name = 'alpha', changes: Record<string, unknown> = {}) {
  return {
    execution_context: executionContext(),
    context: { available: true, reason: null, scope_id: 'sandbox-account', exchange: 'okx', environment: 'sandbox', account_fingerprint: 'abc123', identity_verified: false, market_type: 'spot', execution_mode: 'paper', draft_only: true, execution_ready: false },
    strategy: name, symbol: 'BTC-USDT', currency: 'USDT', lifecycle_status: 'stopped', can_edit: true,
    draft: { strategy: name, symbol: 'BTC-USDT', currency: 'USDT', budget: 100, revision: 2, status: 'draft', scope_id: 'sandbox-account', environment: 'sandbox', account_fingerprint: 'abc123', market_type: 'spot', created_at: 1, updated_at: 2 },
    ...changes,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function mounted(strategyName = 'alpha') {
  const pinia = createPinia();
  accountStore = useAccountContextStore(pinia);
  accountStore.context = executionContext();
  const wrapper = await mount(StrategyAllocationPanel, { locale: 'en', plugins: [pinia], props: { strategyName } });
  for (let i = 0; i < 4; i += 1) await wrapper.flush();
  return wrapper;
}
async function budget(wrapper: Awaited<ReturnType<typeof mounted>>, value: string) {
  await wrapper.trigger(wrapper.getByTestId('allocation-budget'), 'input', { target: { value } });
}
beforeEach(() => {
  vi.resetAllMocks();
  api.get.mockResolvedValue(envelope());
  api.readiness.mockResolvedValue({ execution_context: executionContext(), allocation_scope_id: 'sandbox-account', draft_revision: 2, strategy: 'alpha', symbol: 'BTC-USDT', currency: 'USDT', budget: '100', evaluated_at: 1, capacity: { observed_quote_available: null, proposed_total: '100', comparison: 'unavailable', trusted: false }, ledger: null, checks: [], can_activate: false, execution_ready: false });
  api.save.mockResolvedValue(envelope());
  api.remove.mockResolvedValue(envelope('alpha', { draft: null }));
});
describe('StrategyAllocationPanel', () => {
  it('shows a draft and keeps exchange sandbox separate from local paper execution', async () => {
    const wrapper = await mounted();
    for (const text of ['OKX sandbox', 'Local paper', 'abc123', 'USDT', 'Draft', 'Balance and position verification and activation are still required', 'unverified']) expect(wrapper.text()).toContain(text);
    expect(wrapper.getByTestId('allocation-budget').props.value).toBe('100');
    wrapper.unmount();
  });
  it('shows actual exchange routing separately from its live environment', async () => {
    api.get.mockResolvedValue(envelope('alpha', { context: { ...envelope().context, environment: 'live', execution_mode: 'live' }, execution_context: { ...executionContext(), environment: 'live' } }));
    const wrapper = await mounted();
    accountStore.context = { ...executionContext(), environment: 'live' };
    for (let i = 0; i < 4; i += 1) await wrapper.flush();
    expect(wrapper.text()).toContain('OKX live');
    expect(wrapper.text()).toContain('Exchange routing');
    expect(wrapper.text()).not.toContain('Local paper');
    wrapper.unmount();
  });
  it('shows unavailable context without inventing an account or enabling mutations', async () => {
    api.get.mockResolvedValue(envelope('alpha', { context: { ...envelope().context, available: false, reason: 'credentials_unavailable', account_fingerprint: null, scope_id: null }, draft: null, can_edit: false }));
    const wrapper = await mounted();
    expect(wrapper.text()).toContain('Configure OKX credentials');
    expect(wrapper.text()).not.toContain('abc123');
    expect(wrapper.getByTestId('allocation-save').props.disabled).toBe(true);
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    expect(api.save).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it.each(['', '0', '-1', 'Infinity', 'NaN'])('rejects invalid budget %s', async (value) => {
    const wrapper = await mounted();
    await budget(wrapper, value);
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    expect(wrapper.text()).toContain('positive finite');
    expect(api.save).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it('creates from a nullable draft with revision zero and never starts a strategy', async () => {
    api.get.mockResolvedValue(envelope('alpha', { draft: null }));
    const wrapper = await mounted();
    await budget(wrapper, '500');
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    expect(api.save).toHaveBeenCalledWith('alpha', { budget: 500, expected_revision: 0, expected_scope_id: 'sandbox-account', expected_symbol: 'BTC-USDT' });
    expect(api.start).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Draft saved');
    wrapper.unmount();
  });
  it('updates and deletes using the server revision', async () => {
    api.save.mockResolvedValue(envelope('alpha', { draft: { ...envelope().draft, budget: 500, revision: 3 } }));
    const wrapper = await mounted();
    await budget(wrapper, '500');
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    expect(api.save).toHaveBeenCalledWith('alpha', { budget: 500, expected_revision: 2, expected_scope_id: 'sandbox-account', expected_symbol: 'BTC-USDT' });
    await wrapper.trigger(wrapper.getByTestId('allocation-delete'), 'click');
    expect(api.remove).toHaveBeenCalledWith('alpha', 3, 'sandbox-account', 'BTC-USDT');
    expect(wrapper.text()).toContain('No allocation draft');
    expect(api.start).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it('shows a revision conflict and requires refresh before another mutation', async () => {
    api.save.mockRejectedValue({ response: { status: 409, data: { detail: { code: 'stale_revision', message: 'Revision changed' } } } });
    const wrapper = await mounted();
    await budget(wrapper, '500');
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    expect(wrapper.text()).toContain('Revision changed');
    expect(wrapper.text()).toContain('Refresh');
    expect(wrapper.getByTestId('allocation-save').props.disabled).toBe(true);
    await wrapper.trigger(wrapper.getByTestId('allocation-refresh'), 'click');
    expect(wrapper.getByTestId('allocation-save').props.disabled).toBe(false);
    wrapper.unmount();
  });
  it('sends the loaded account scope and never retries a conflict against a changed account', async () => {
    api.save.mockRejectedValue({ response: { status: 409, data: { detail: { code: 'allocation_scope_changed', message: 'Configured account changed. Reload allocation context.' } } } });
    const wrapper = await mounted();
    await budget(wrapper, '500');
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    expect(api.save).toHaveBeenCalledWith('alpha', { budget: 500, expected_revision: 2, expected_scope_id: 'sandbox-account', expected_symbol: 'BTC-USDT' });
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain('Configured account changed');
    expect(wrapper.getByTestId('allocation-save').props.disabled).toBe(true);
    expect(wrapper.getByTestId('allocation-delete').props.disabled).toBe(true);
    api.get.mockResolvedValueOnce(envelope('alpha', { context: { ...envelope().context, scope_id: 'new-live-scope', environment: 'live' }, draft: null }));
    await wrapper.trigger(wrapper.getByTestId('allocation-refresh'), 'click');
    await budget(wrapper, '600');
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    expect(api.save).toHaveBeenLastCalledWith('alpha', { budget: 600, expected_revision: 0, expected_scope_id: 'new-live-scope', expected_symbol: 'BTC-USDT' });
    wrapper.unmount();
  });

  it('sends the loaded symbol and never retries a changed-symbol conflict before refresh', async () => {
    api.get.mockResolvedValueOnce(envelope('alpha', { symbol: 'ETH-USDT', draft: { ...envelope().draft, symbol: 'ETH-USDT' } }));
    api.save.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: 'allocation_symbol_changed', message: 'Strategy symbol changed. Reload allocation context.' } } } });
    const wrapper = await mounted();
    await budget(wrapper, '500');
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    expect(api.save).toHaveBeenCalledWith('alpha', { budget: 500, expected_revision: 2, expected_scope_id: 'sandbox-account', expected_symbol: 'ETH-USDT' });
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    await wrapper.trigger(wrapper.getByTestId('allocation-delete'), 'click');
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(api.remove).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Strategy symbol changed');
    expect(wrapper.getByTestId('allocation-save').props.disabled).toBe(true);
    expect(wrapper.getByTestId('allocation-delete').props.disabled).toBe(true);
    api.get.mockResolvedValueOnce(envelope('alpha', { symbol: 'SOL-USDT', draft: null }));
    await wrapper.trigger(wrapper.getByTestId('allocation-refresh'), 'click');
    await budget(wrapper, '600');
    await wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    expect(api.save).toHaveBeenLastCalledWith('alpha', { budget: 600, expected_revision: 0, expected_scope_id: 'sandbox-account', expected_symbol: 'SOL-USDT' });
    wrapper.unmount();
  });

  it('does not let an older strategy load overwrite a newly selected strategy', async () => {
    const first = deferred<ReturnType<typeof envelope>>();
    api.get.mockReturnValueOnce(first.promise).mockResolvedValueOnce(envelope('beta', { draft: { ...envelope('beta').draft, budget: 222 } }));
    const wrapper = await mounted();
    await wrapper.updateProps({ strategyName: 'beta' });
    for (let i = 0; i < 4; i += 1) await wrapper.flush();
    first.resolve(envelope());
    for (let i = 0; i < 4; i += 1) await wrapper.flush();
    expect(wrapper.text()).toContain('beta');
    expect(wrapper.getByTestId('allocation-budget').props.value).toBe('222');
    wrapper.unmount();
  });
  it('does not let an earlier strategy save overwrite the next selection', async () => {
    const saving = deferred<ReturnType<typeof envelope>>();
    api.save.mockReturnValueOnce(saving.promise);
    const wrapper = await mounted();
    await budget(wrapper, '500');
    const save = wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    api.get.mockResolvedValueOnce(envelope('beta', { draft: null }));
    await wrapper.updateProps({ strategyName: 'beta' });
    for (let i = 0; i < 4; i += 1) await wrapper.flush();
    saving.resolve(envelope());
    await save;
    expect(wrapper.text()).toContain('beta');
    expect(wrapper.text()).toContain('No allocation draft');
    expect(wrapper.getByTestId('allocation-budget').props.value).toBe('');
    wrapper.unmount();
  });
  it.each(['running', 'starting', 'unknown'])('keeps %s strategy allocations read-only', async (lifecycle) => {
    api.get.mockResolvedValue(envelope('alpha', { lifecycle_status: lifecycle, can_edit: false }));
    const wrapper = await mounted();
    expect(wrapper.text()).toContain('Only a stopped strategy');
    expect(wrapper.getByTestId('allocation-save').props.disabled).toBe(true);
    expect(wrapper.getByTestId('allocation-delete').props.disabled).toBe(true);
    wrapper.unmount();
  });
  it('evaluates only the saved budget while the input has unsaved edits', async () => {
    const wrapper = await mounted();
    const calls = api.readiness.mock.calls.length;
    await budget(wrapper, '999');
    expect(api.readiness.mock.calls.length).toBe(calls);
    expect(wrapper.text()).toContain('Saved draft only');
    expect(wrapper.text()).toContain('100 USDT');
    wrapper.unmount();
  });
  it('clears the envelope immediately when account authority is invalidated', async () => {
    const wrapper = await mounted();
    accountStore.invalidate();
    await wrapper.flush();
    expect(wrapper.text()).not.toContain('abc123');
    expect(wrapper.findAll((node) => node.props['data-testid'] === 'allocation-save')).toHaveLength(0);
    wrapper.unmount();
  });
  it.each(['get', 'save', 'remove'])('ignores an old %s completion through account A to B to A', async (operation) => {
    const stale = deferred<ReturnType<typeof envelope>>();
    if (operation === 'get') api.get.mockReturnValueOnce(stale.promise);
    const wrapper = await mounted();
    if (operation === 'save') {
      api.save.mockReturnValueOnce(stale.promise);
      void wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    } else if (operation === 'remove') {
      api.remove.mockReturnValueOnce(stale.promise);
      void wrapper.trigger(wrapper.getByTestId('allocation-delete'), 'click');
    }
    api.get.mockResolvedValue(envelope('alpha', { draft: { ...envelope().draft, budget: 333, revision: 4 } }));
    accountStore.invalidate();
    accountStore.context = executionContext('execution-b');
    accountStore.invalidate();
    accountStore.context = executionContext();
    for (let i = 0; i < 5; i += 1) await wrapper.flush();
    stale.resolve(envelope('alpha', { draft: { ...envelope().draft, budget: 999 } }));
    for (let i = 0; i < 5; i += 1) await wrapper.flush();
    expect(wrapper.getByTestId('allocation-budget').props.value).toBe('333');
    wrapper.unmount();
  });
  it('rejects a draft envelope for another account rather than enabling edits', async () => {
    api.get.mockResolvedValue(envelope('alpha', { execution_context: executionContext('different') }));
    const wrapper = await mounted();
    expect(wrapper.text()).not.toContain('abc123');
    expect(wrapper.text()).toContain('Could not load');
    wrapper.unmount();
  });

  it.each(['get', 'save', 'remove'])('ignores stale %s error/finally while the current account draft is loading', async (operation) => {
    const old = deferred<ReturnType<typeof envelope>>();
    const current = deferred<ReturnType<typeof envelope>>();
    if (operation === 'get') api.get.mockReturnValueOnce(old.promise);
    const wrapper = await mounted();
    if (operation === 'save') {
      api.save.mockReturnValueOnce(old.promise);
      void wrapper.trigger(wrapper.getByTestId('allocation-form'), 'submit');
    } else if (operation === 'remove') {
      api.remove.mockReturnValueOnce(old.promise);
      void wrapper.trigger(wrapper.getByTestId('allocation-delete'), 'click');
    }
    api.get.mockReturnValue(current.promise);
    accountStore.invalidate(); accountStore.context = executionContext();
    old.reject({ response: { status: 409, data: { detail: { message: 'obsolete failure' } } } });
    for (let i = 0; i < 5; i += 1) await wrapper.flush();
    expect(wrapper.text()).not.toContain('obsolete failure');
    expect(wrapper.getByTestId('allocation-refresh').props.disabled).toBe(true);
    current.resolve(envelope());
    for (let i = 0; i < 5; i += 1) await wrapper.flush();
    expect(wrapper.getByTestId('allocation-save').props.disabled).toBe(false);
    wrapper.unmount();
  });
  it('keeps no-credentials draft display but never requests readiness or enables edit', async () => {
    const wrapper = await mounted();
    const unavailable = { ...executionContext(), available: false, account_scope_id: null, reason: 'credentials_unavailable' };
    api.get.mockResolvedValue(envelope('alpha', { execution_context: unavailable,
      context: { ...envelope().context, available: false, scope_id: null, account_fingerprint: null, reason: 'credentials_unavailable' }, draft: null, can_edit: false }));
    const readinessCalls = api.readiness.mock.calls.length;
    accountStore.invalidate(); accountStore.context = unavailable;
    for (let i = 0; i < 5; i += 1) await wrapper.flush();
    expect(wrapper.text()).toContain('Configure OKX credentials');
    expect(wrapper.getByTestId('allocation-save').props.disabled).toBe(true);
    expect(api.readiness.mock.calls.length).toBe(readinessCalls);
    wrapper.unmount();
  });

  it('reloads when the saved strategy symbol changes while name and lifecycle stay the same', async () => {
    const wrapper = await mounted();
    await wrapper.updateProps({ strategySymbol: 'BTC-USDT' });
    api.get.mockResolvedValue(envelope('alpha', { symbol: 'ETH-USDT', draft: { ...envelope().draft, symbol: 'BTC-USDT' } }));
    const previous = api.get.mock.calls.length;
    await wrapper.updateProps({ strategySymbol: 'ETH-USDT' });
    for (let i = 0; i < 4; i += 1) await wrapper.flush();
    expect(api.get.mock.calls.length).toBeGreaterThan(previous);
    expect(wrapper.text()).toContain('ETH-USDT');
    wrapper.unmount();
  });

});
