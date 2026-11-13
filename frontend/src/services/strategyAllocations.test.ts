import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  activateStrategyAllocation,
  deleteStrategyAllocation,
  getAllocationContext,
  getStrategyAllocation,
  getStrategyAllocationReadiness,
  releaseStrategyAllocation,
  saveStrategyAllocation,
} from './strategyAllocations';
vi.mock('axios');
const http = vi.mocked(axios);
beforeEach(() => vi.resetAllMocks());
describe('allocation draft API', () => {
  it('reads the server context and nullable draft envelope', async () => {
    const context = { available: false, environment: 'sandbox', execution_mode: 'paper' };
    const execution_context = {
      account_scope_id: null, exchange: 'okx', environment: 'sandbox', market_type: 'spot',
      identity_verified: false, available: false, reason: 'credentials_unavailable', execution_ready: false,
    };
    const envelope = { context, execution_context, draft: null, strategy: 'desk/btc', can_edit: false };
    http.get.mockResolvedValueOnce({ data: context }).mockResolvedValueOnce({ data: envelope });
    await expect(getAllocationContext()).resolves.toEqual(context);
    await expect(getStrategyAllocation('desk/btc')).resolves.toEqual(envelope);
    expect(http.get).toHaveBeenNthCalledWith(1, '/api/allocations/context');
    expect(http.get).toHaveBeenNthCalledWith(2, '/api/allocations/desk%2Fbtc');
  });
  it('uses expected revision for create and update without sending identity or strategy params', async () => {
    http.put.mockResolvedValue({ data: { draft: { revision: 2, status: 'draft' } } });
    await saveStrategyAllocation('desk/btc', { budget: 500, expected_revision: 0, expected_scope_id: 'loaded-sandbox-scope', expected_symbol: 'BTC-USDT' });
    await expect(saveStrategyAllocation('desk/btc', { budget: 600, expected_revision: 1, expected_scope_id: 'loaded-sandbox-scope', expected_symbol: 'BTC-USDT' })).resolves.toEqual({ draft: { revision: 2, status: 'draft' } });
    expect(http.put).toHaveBeenNthCalledWith(1, '/api/allocations/desk%2Fbtc', { budget: 500, expected_revision: 0, expected_scope_id: 'loaded-sandbox-scope', expected_symbol: 'BTC-USDT' });
    expect(http.put).toHaveBeenNthCalledWith(2, '/api/allocations/desk%2Fbtc', { budget: 600, expected_revision: 1, expected_scope_id: 'loaded-sandbox-scope', expected_symbol: 'BTC-USDT' });
    expect(http.post).not.toHaveBeenCalled();
  });
  it('deletes the current revision and returns the empty draft envelope', async () => {
    http.delete.mockResolvedValueOnce({ data: { draft: null } });
    await expect(deleteStrategyAllocation('desk/btc', 7, 'loaded-sandbox-scope', 'BTC-USDT')).resolves.toEqual({ draft: null });
    expect(http.delete).toHaveBeenCalledWith('/api/allocations/desk%2Fbtc', { params: { revision: 7, expected_scope_id: 'loaded-sandbox-scope', expected_symbol: 'BTC-USDT' } });
  });
  it('activates and releases the loaded revision without sending a budget', async () => {
    const update = { expected_revision: 2, expected_scope_id: 'loaded-sandbox-scope', expected_symbol: 'BTC-USDT' };
    http.post.mockResolvedValue({ data: { reservation: { revision: 3, status: 'active' } } });
    await expect(activateStrategyAllocation('desk/btc', update)).resolves.toEqual({ reservation: { revision: 3, status: 'active' } });
    await releaseStrategyAllocation('desk/btc', update);
    expect(http.post).toHaveBeenNthCalledWith(1, '/api/allocations/desk%2Fbtc/activate', update);
    expect(http.post).toHaveBeenNthCalledWith(2, '/api/allocations/desk%2Fbtc/release', update);
  });

  it('reads saved-draft readiness with all account and draft preconditions without mutations', async () => {
    const readiness = {
      execution_context: {
        account_scope_id: 'execution-account', exchange: 'okx', environment: 'sandbox',
        market_type: 'spot', identity_verified: false, available: true,
        reason: null, execution_ready: false,
      },
      allocation_scope_id: 'allocation-scope',
      draft_revision: 3,
      strategy: 'desk/btc',
      symbol: 'BTC-USDT',
      currency: 'USDT',
      budget: '100.3',
      evaluated_at: 123,
      capacity: {
        observed_quote_available: '150',
        proposed_total: '100.3',
        comparison: 'within_observed',
        trusted: false,
      },
      ledger: {
        strategy: 'desk/btc', symbol: 'BTC-USDT', currency: 'USDT', base_currency: 'BTC',
        allocation_scope_id: 'allocation-scope', draft_revision: 3, status: 'prepared',
        execution_ready: false, initial_quote: '100.3', quote_balance: '100.3',
        base_balance: '0', cost_basis_quote: '0', realized_pnl_quote: '0', version: 1,
        updated_at: 123, quote_reserved: '0', quote_available: '100.3', base_reserved: '0',
        base_available: '0', fees: { USDT: '0', BTC: '0' },
      },
      checks: [{ code: 'strategy_stopped', status: 'passed' }],
      can_activate: false,
      execution_ready: false,
    };
    const request = {
      expected_account_scope_id: 'execution-account',
      expected_scope_id: 'allocation-scope',
      expected_revision: 3,
      expected_symbol: 'BTC-USDT',
    };
    http.get.mockResolvedValueOnce({ data: readiness });

    await expect(getStrategyAllocationReadiness('desk/btc', request)).resolves.toEqual(readiness);
    expect(http.get).toHaveBeenCalledWith('/api/allocations/desk%2Fbtc/readiness', { params: request });
    expect(http.post).not.toHaveBeenCalled();
    expect(http.put).not.toHaveBeenCalled();
    expect(http.delete).not.toHaveBeenCalled();
  });
});
