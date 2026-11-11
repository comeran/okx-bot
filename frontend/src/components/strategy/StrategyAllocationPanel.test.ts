import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@/test-utils/mount';
import StrategyAllocationPanel from './StrategyAllocationPanel.vue';
const api = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), remove: vi.fn(), start: vi.fn() }));
vi.mock('@/services/strategyAllocations', () => ({ getStrategyAllocation: api.get, saveStrategyAllocation: api.save, deleteStrategyAllocation: api.remove }));
vi.mock('@/services/strategies', () => ({ startStrategy: api.start }));
function envelope(name = 'alpha', changes: Record<string, unknown> = {}) {
  return {
    context: { available: true, reason: null, scope_id: 'sandbox-account', exchange: 'okx', environment: 'sandbox', account_fingerprint: 'abc123', identity_verified: false, market_type: 'spot', execution_mode: 'paper', draft_only: true, execution_ready: false },
    strategy: name, symbol: 'BTC-USDT', currency: 'USDT', lifecycle_status: 'stopped', can_edit: true,
    draft: { strategy: name, symbol: 'BTC-USDT', currency: 'USDT', budget: 100, revision: 2, status: 'draft', scope_id: 'sandbox-account', environment: 'sandbox', account_fingerprint: 'abc123', market_type: 'spot', created_at: 1, updated_at: 2 },
    ...changes,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
async function mounted(strategyName = 'alpha') {
  const wrapper = await mount(StrategyAllocationPanel, { locale: 'en', props: { strategyName } });
  for (let i = 0; i < 4; i += 1) await wrapper.flush();
  return wrapper;
}
async function budget(wrapper: Awaited<ReturnType<typeof mounted>>, value: string) {
  await wrapper.trigger(wrapper.getByTestId('allocation-budget'), 'input', { target: { value } });
}
beforeEach(() => {
  vi.resetAllMocks();
  api.get.mockResolvedValue(envelope());
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
    api.get.mockResolvedValue(envelope('alpha', { context: { ...envelope().context, environment: 'live', execution_mode: 'live' } }));
    const wrapper = await mounted();
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
});
