import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useAccountContextStore } from './accountContext';
import type { ExecutionContext } from '@/types/executionContext';

const mocks = vi.hoisted(() => ({ context: vi.fn(), probe: vi.fn() }));
vi.mock('@/services/trading', () => ({ fetchTradingContext: mocks.context }));
vi.mock('@/services/settings', () => ({ checkExchangeAuthentication: mocks.probe }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const context: ExecutionContext = {
  account_scope_id: 'scope-A', exchange: 'okx', environment: 'sandbox', market_type: 'spot',
  identity_verified: false, execution_ready: false, available: true, reason: null,
};
const authenticated = {
  account_scope_id: 'scope-A', environment: 'sandbox', status: 'authenticated',
  reason: null, provider_code: null, checked_at: 1,
};

describe('account authentication observations', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    mocks.context.mockReset().mockResolvedValue({ ...context });
    mocks.probe.mockReset().mockResolvedValue({ ...authenticated });
  });

  it('deduplicates concurrent checks and caches the result without granting execution authority', async () => {
    const store = useAccountContextStore();
    await store.reload();
    const request = deferred<typeof authenticated>();
    mocks.probe.mockReturnValue(request.promise);
    const first = store.checkAuthentication();
    const second = store.checkAuthentication();
    expect(store.authenticationChecking).toBe(true);
    expect(mocks.probe).toHaveBeenCalledTimes(1);
    expect(mocks.probe).toHaveBeenCalledWith('scope-A');
    request.resolve(authenticated);
    await Promise.all([first, second]);
    expect(store.authentication).toEqual(authenticated);
    await store.checkAuthentication();
    expect(mocks.probe).toHaveBeenCalledTimes(1);
    expect(store.context?.identity_verified).toBe(false);
    expect(store.context?.execution_ready).toBe(false);
  });

  it('clears successful checks immediately and discards old responses after same-scope settings reload', async () => {
    const store = useAccountContextStore();
    await store.reload();
    await store.checkAuthentication();
    const old = deferred<typeof authenticated>();
    mocks.probe.mockReturnValueOnce(old.promise);
    const recheck = store.checkAuthentication(true);
    expect(store.authentication).toBeNull();
    store.invalidate();
    expect(store.authenticationChecking).toBe(false);
    await store.reload();
    mocks.probe.mockResolvedValueOnce({ ...authenticated, status: 'failed', reason: 'invalid_credentials', provider_code: '50119' });
    await store.checkAuthentication();
    old.resolve(authenticated);
    await recheck;
    expect(store.authentication?.status).toBe('failed');
    expect(store.authentication?.provider_code).toBe('50119');
  });

  it('retries saved credentials after a failed observation', async () => {
    const store = useAccountContextStore();
    await store.reload();
    mocks.probe.mockResolvedValueOnce({ ...authenticated, status: 'failed', reason: 'invalid_credentials', provider_code: '50119' });
    await store.checkAuthentication();
    await store.checkAuthentication(true);
    expect(store.authentication?.status).toBe('authenticated');
    expect(mocks.probe).toHaveBeenCalledTimes(2);
  });

  it('fails closed for wrong scopes and untrusted provider fields', async () => {
    const store = useAccountContextStore();
    await store.reload();
    mocks.probe.mockResolvedValueOnce({ ...authenticated, account_scope_id: 'scope-other' });
    await store.checkAuthentication();
    expect(store.authentication?.status).toBe('unavailable');
    expect(store.authentication?.reason).toBe('account_scope_changed');
    mocks.probe.mockResolvedValueOnce({ ...authenticated, status: 'failed', reason: 'secret from server', provider_code: '12345' });
    await store.checkAuthentication(true);
    expect(store.authentication?.reason).toBe('authentication_failed');
    expect(store.authentication?.provider_code).toBeNull();
  });

  it('turns transport errors into a curated unavailable result and never probes unconfigured contexts', async () => {
    const store = useAccountContextStore();
    await store.reload();
    mocks.probe.mockRejectedValueOnce(new Error('secret provider payload'));
    await store.checkAuthentication();
    expect(store.authentication?.reason).toBe('provider_unavailable');
    expect(JSON.stringify(store.authentication)).not.toContain('secret');
    mocks.context.mockResolvedValueOnce({ ...context, available: false, account_scope_id: null });
    await store.reload();
    await store.checkAuthentication();
    expect(mocks.probe).toHaveBeenCalledTimes(1);
    expect(store.authentication).toBeNull();
  });

  it('does not accept contradictory successful or malformed provider observations', async () => {
    const store = useAccountContextStore();
    await store.reload();
    mocks.probe.mockResolvedValueOnce({ ...authenticated, reason: 'invalid_credentials', provider_code: '50119' });
    await store.checkAuthentication();
    expect(store.authentication?.status).toBe('unavailable');
    expect(store.authentication?.reason).toBe('malformed_response');
    mocks.probe.mockResolvedValueOnce({ ...authenticated, checked_at: null });
    await store.checkAuthentication(true);
    expect(store.authentication?.status).toBe('unavailable');
    expect(store.authentication?.reason).toBe('malformed_response');
  });
});
