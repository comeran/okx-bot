import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { deleteStrategyAllocation, activateStrategyAllocation, getAllocationContext, getStrategyAllocation, releaseStrategyAllocation, saveStrategyAllocation } from './strategyAllocations';
vi.mock('axios');
const http = vi.mocked(axios);
beforeEach(() => vi.resetAllMocks());
describe('allocation draft API', () => {
  it('reads the server context and nullable draft envelope', async () => {
    const context = { available: false, environment: 'sandbox', execution_mode: 'paper' };
    const envelope = { context, draft: null, strategy: 'desk/btc', can_edit: false };
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
});
