import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

import { useStrategiesStore } from './strategies';
import * as strategyService from '@/services/strategies';
import type { StrategyConfig, StrategyDefinition, StrategyRuntimeSummary } from '@/types/strategy';

vi.mock('@/services/strategies');

const mockedService = vi.mocked(strategyService);

const definition: StrategyDefinition = {
  strategy_type: 'ma_cross',
  label: 'MA Cross',
  description: 'Moving average crossover',
  params: [
    {
      key: 'fast',
      label: 'Fast',
      description: 'Fast period',
      value_type: 'integer',
      required: true,
      default: 10,
      minimum: 1,
      maximum: 100,
      step: 1,
    },
  ],
};

const btcConfig: StrategyConfig = {
  name: 'btc_ma',
  strategy_type: 'ma_cross',
  symbol: 'BTC-USDT-SWAP',
  timeframe: '1m',
  enabled: true,
  params: { fast: 10, slow: 30 },
  created_at: 1700000000000,
  updated_at: 1700000000000,
};

const ethConfig: StrategyConfig = {
  ...btcConfig,
  name: 'eth_ma',
  symbol: 'ETH-USDT-SWAP',
};

const stoppedBtc: StrategyRuntimeSummary = { name: 'btc_ma', status: 'stopped' };

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  let reject: (reason?: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('strategies store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.resetAllMocks();
  });

  it('loads definitions, configs, and runtime statuses as initial strategy state', async () => {
    mockedService.listStrategyTypes.mockResolvedValueOnce([definition]);
    mockedService.listStrategyConfigs.mockResolvedValueOnce([btcConfig]);
    mockedService.listStrategies.mockResolvedValueOnce([stoppedBtc]);

    const store = useStrategiesStore();
    await store.loadInitialData();

    expect(store.definitions).toEqual([definition]);
    expect(store.configs).toEqual([btcConfig]);
    expect(store.statuses).toEqual({ btc_ma: stoppedBtc });
    expect(store.loadingInitial).toBe(false);
    expect(store.error).toBeNull();
  });

  it('rejects malformed initial REST config arrays without replacing canonical configs', async () => {
    const existing = { ...ethConfig, updated_at: 1700000001000 };
    mockedService.listStrategyTypes.mockResolvedValueOnce([definition]);
    mockedService.listStrategyConfigs.mockResolvedValueOnce([
      { ...btcConfig, params: { fast: { nested: true } } },
    ] as never);
    mockedService.listStrategies.mockResolvedValueOnce([stoppedBtc]);

    const store = useStrategiesStore();
    store.configs = [existing];
    await store.loadInitialData();

    expect(store.definitions).toEqual([definition]);
    expect(store.configs).toEqual([existing]);
    expect(store.statuses).toEqual({ btc_ma: stoppedBtc });
    expect(store.error).toBe('Invalid strategy config response');
    expect(store.configReconciliationError).toBeNull();
  });

  it('preserves canonical configs and exposes a config warning for malformed config reconciliation', async () => {
    mockedService.listStrategyConfigs.mockResolvedValueOnce([
      { ...btcConfig, created_at: Infinity },
    ] as never);
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    await store.refreshConfigsForReconciliation();

    expect(store.configs).toEqual([btcConfig]);
    expect(store.configReconciliationError).toBe('Invalid strategy config response');
    expect(store.reconciliationError).toBe('Invalid strategy config response');
  });

  it('rejects malformed initial REST runtime summaries without replacing canonical statuses or errors', async () => {
    mockedService.listStrategyTypes.mockResolvedValueOnce([definition]);
    mockedService.listStrategyConfigs.mockResolvedValueOnce([btcConfig]);
    mockedService.listStrategies.mockResolvedValueOnce([
      { name: 'btc_ma', status: 'running', error: { message: 'boom' } },
    ] as never);
    const store = useStrategiesStore();
    store.statuses = { eth_ma: { name: 'eth_ma', status: 'running' } };
    store.errors = { eth_ma: 'existing boom' };

    await store.loadInitialData();

    expect(store.configs).toEqual([btcConfig]);
    expect(store.statuses).toEqual({ eth_ma: { name: 'eth_ma', status: 'running' } });
    expect(store.errors).toEqual({ eth_ma: 'existing boom' });
    expect(store.error).toBe('Invalid strategy status response');
    expect(store.statusReconciliationError).toBeNull();
  });

  it('preserves canonical statuses and errors and exposes a status warning for malformed status reconciliation', async () => {
    mockedService.listStrategies.mockResolvedValueOnce([
      { name: 'btc_ma', status: 'running', error: ['boom'] },
    ] as never);
    const store = useStrategiesStore();
    store.statuses = { btc_ma: stoppedBtc };
    store.errors = { btc_ma: 'existing boom' };

    await store.refreshStatusesForReconciliation();

    expect(store.statuses).toEqual({ btc_ma: stoppedBtc });
    expect(store.errors).toEqual({ btc_ma: 'existing boom' });
    expect(store.statusReconciliationError).toBe('Invalid strategy status response');
    expect(store.reconciliationError).toBe('Invalid strategy status response');
  });

  it.each([
    ['created_at NaN', { ...btcConfig, created_at: Number.NaN }],
    ['created_at Infinity', { ...btcConfig, created_at: Infinity }],
    ['updated_at NaN', { ...btcConfig, updated_at: Number.NaN }],
    ['updated_at Infinity', { ...btcConfig, updated_at: Infinity }],
    ['object param', { ...btcConfig, params: { fast: { nested: true } } }],
    ['array param', { ...btcConfig, params: { fast: [10] } }],
    ['undefined param', { ...btcConfig, params: { fast: undefined } }],
    ['NaN param', { ...btcConfig, params: { fast: Number.NaN } }],
    ['Infinity param', { ...btcConfig, params: { fast: Infinity } }],
  ] as const)('rejects REST configs with invalid %s', async (_label, malformed) => {
    mockedService.listStrategyConfigs.mockResolvedValueOnce([malformed] as never);
    const store = useStrategiesStore();
    store.configs = [ethConfig];

    await store.refreshConfigsForReconciliation();

    expect(store.configs).toEqual([ethConfig]);
    expect(store.configReconciliationError).toBe('Invalid strategy config response');
  });

  it('does not let an invalid REST Infinity timestamp poison later finite updates', async () => {
    mockedService.listStrategyConfigs.mockResolvedValueOnce([
      { ...btcConfig, updated_at: Infinity },
    ] as never);
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    await store.refreshConfigsForReconciliation();
    const valid = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: valid });

    expect(store.configs).toEqual([valid]);
  });

  it('applies valid REST config and runtime reconciliation data', async () => {
    const updated = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    mockedService.listStrategyConfigs.mockResolvedValueOnce([updated]);
    mockedService.listStrategies.mockResolvedValueOnce([{ name: 'btc_ma', status: 'running', error: 'rest boom' }]);
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    await store.refreshConfigsForReconciliation();
    await store.refreshStatusesForReconciliation();

    expect(store.configs).toEqual([updated]);
    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'rest boom' });
    expect(store.reconciliationError).toBeNull();
  });

  it.each([
    {
      action: 'create' as const,
      target: 'created_ma',
      arrange: (malformed: unknown) => mockedService.createStrategyConfig.mockResolvedValueOnce(malformed as never),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.createConfig({ ...btcConfig, name: 'created_ma' }),
    },
    {
      action: 'update' as const,
      target: 'btc_ma',
      arrange: (malformed: unknown) => mockedService.updateStrategyConfig.mockResolvedValueOnce(malformed as never),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.updateConfig('btc_ma', { ...btcConfig, timeframe: '5m' }),
    },
    {
      action: 'clone' as const,
      target: 'cloned_ma',
      arrange: (malformed: unknown) => mockedService.cloneStrategyConfig.mockResolvedValueOnce(malformed as never),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.cloneConfig('btc_ma', { target_name: 'cloned_ma' }),
    },
  ])('rejects malformed $action REST results without mutating canonical configs', async ({ action, target, arrange, run }) => {
    arrange({ ...btcConfig, name: target, updated_at: Infinity });
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    const beforeConfigs = [...store.configs];

    await expect(run(store)).rejects.toThrow('Invalid strategy config response');

    expect(store.configs).toEqual(beforeConfigs);
    expect(store.statuses[target]).toBeUndefined();
    expect(store.mutationError(target, action)).toBe('Invalid strategy config response');
    expect(mockedService.listStrategyConfigs).not.toHaveBeenCalled();
  });

  it('distributes initial snapshot data without disturbing missing sections', () => {
    const store = useStrategiesStore();
    store.definitions = [definition];
    store.configs = [ethConfig];

    store.applyWebSocketMessage({
      type: 'snapshot',
      data: {
        strategies: [{ name: 'btc_ma', status: 'running', error: 'old' }],
        strategy_configs: [btcConfig],
        strategy_errors: { btc_ma: 'boom' },
      },
    });

    expect(store.definitions).toEqual([definition]);
    expect(store.configs).toEqual([btcConfig]);
    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'boom' });
  });

  it('rejects malformed inline snapshot errors without changing canonical runtime state', () => {
    const store = useStrategiesStore();
    store.statuses = { eth_ma: { name: 'eth_ma', status: 'running' } };
    store.errors = { eth_ma: 'existing boom' };

    store.applyWebSocketMessage({
      type: 'snapshot',
      data: {
        strategy_configs: [btcConfig],
        strategies: [
          { name: 'btc_ma', status: 'error', error: { message: 'boom' } },
        ],
      },
    } as Parameters<typeof store.applyWebSocketMessage>[0]);

    expect(store.configs).toEqual([btcConfig]);
    expect(store.statuses).toEqual({ eth_ma: { name: 'eth_ma', status: 'running' } });
    expect(store.errors).toEqual({ eth_ma: 'existing boom' });
  });

  it('applies idempotent status and error websocket reducers', () => {
    const store = useStrategiesStore();

    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running' });
    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running' });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'boom' });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'boom' });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'boom' });
  });

  it('upserts stale config CRUD events by name and deletes only that strategy state', () => {
    const store = useStrategiesStore();
    store.configs = [btcConfig, ethConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' }, eth_ma: { name: 'eth_ma', status: 'stopped' } };
    store.errors = { btc_ma: 'boom', eth_ma: 'fine' };
    store.actionLoading = { 'btc_ma:start': true, 'eth_ma:stop': true };

    const updated = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: updated });
    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: updated });
    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000002000 });

    expect(store.configs).toEqual([ethConfig]);
    expect(store.statuses).toEqual({ eth_ma: { name: 'eth_ma', status: 'stopped' } });
    expect(store.errors).toEqual({ eth_ma: 'fine' });
    expect(store.actionLoading).toEqual({ 'eth_ma:stop': true });
  });

  it('does not let stale initial REST status overwrite a newer websocket status', async () => {
    const definitions = deferred<StrategyDefinition[]>();
    const configs = deferred<StrategyConfig[]>();
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategyTypes.mockReturnValueOnce(definitions.promise);
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    const loadPromise = store.loadInitialData();
    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running' });

    definitions.resolve([definition]);
    configs.resolve([btcConfig]);
    statuses.resolve([stoppedBtc]);
    await loadPromise;

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
  });

  it('does not let stale initial REST status clear a newer websocket strategy error', async () => {
    const definitions = deferred<StrategyDefinition[]>();
    const configs = deferred<StrategyConfig[]>();
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategyTypes.mockReturnValueOnce(definitions.promise);
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    const loadPromise = store.loadInitialData();
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'new boom' });

    definitions.resolve([definition]);
    configs.resolve([btcConfig]);
    statuses.resolve([stoppedBtc]);
    await loadPromise;

    expect(store.statuses).toEqual({ btc_ma: stoppedBtc });
    expect(store.errors).toEqual({ btc_ma: 'new boom' });
  });

  it('creates default stopped status for remote config creation without replacing existing status', () => {
    const store = useStrategiesStore();

    store.applyWebSocketMessage({ type: 'strategy_config_created', strategy: 'btc_ma', config: btcConfig });
    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'stopped' } });

    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running' });
    store.applyWebSocketMessage({ type: 'strategy_config_created', strategy: 'btc_ma', config: { ...btcConfig, updated_at: 1700000001000 } });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
  });

  it('does not let delayed status or error events recreate runtime state after config deletion', () => {
    mockedService.listStrategies.mockResolvedValue([]);
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' } };
    store.errors = { btc_ma: 'boom' };

    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });
    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', timestamp: 1700000000500 });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'late boom', timestamp: 1700000000500 });

    expect(store.statuses).toEqual({});
    expect(store.errors).toEqual({});
    expect(mockedService.listStrategies).toHaveBeenCalledTimes(2);
  });

  it('rejects old-instance status and error events after same-name recreation but accepts newer authoritative events', () => {
    mockedService.listStrategies.mockResolvedValue([]);
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' } };
    store.errors = { btc_ma: 'boom' };
    const recreated = { ...btcConfig, updated_at: 1700000002000 };

    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });
    store.applyWebSocketMessage({ type: 'strategy_config_created', strategy: 'btc_ma', config: recreated, timestamp: 1700000002000 });
    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'stopped' } });

    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', timestamp: 1700000000500 });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'old boom', timestamp: 1700000000500 });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'stopped' } });
    expect(store.errors).toEqual({});
    expect(mockedService.listStrategies).toHaveBeenCalledTimes(2);

    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', timestamp: 1700000003000 });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'new boom', timestamp: 1700000003000 });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'new boom' });
  });

  it('uses shared received_at as the recreation boundary for timestamp-free config websocket events', () => {
    mockedService.listStrategies.mockResolvedValue([]);
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' } };
    store.errors = { btc_ma: 'boom' };
    const recreated = { ...btcConfig, updated_at: 1700000002000 };

    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', received_at: 1700000001000 });
    store.applyWebSocketMessage({ type: 'strategy_config_created', strategy: 'btc_ma', config: recreated, received_at: 1700000002000 });
    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', received_at: 1700000001500 });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'old boom', received_at: 1700000001500 });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'stopped' } });
    expect(store.errors).toEqual({});
    expect(mockedService.listStrategies).toHaveBeenCalledTimes(2);

    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', received_at: 1700000003000 });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'new boom', received_at: 1700000003000 });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'new boom' });
  });

  it.each([
    {
      deletion: 'newer websocket delete',
      deleteConfig: async (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({
          type: 'strategy_config_deleted',
          strategy: 'btc_ma',
          timestamp: 1700000003000,
        });
      },
    },
    {
      deletion: 'local delete',
      deleteConfig: async (store: ReturnType<typeof useStrategiesStore>) => {
        store.removeConfig('btc_ma');
      },
    },
    {
      deletion: 'authoritative REST omission',
      deleteConfig: async (store: ReturnType<typeof useStrategiesStore>) => {
        mockedService.listStrategyConfigs.mockResolvedValueOnce([]);
        await store.refreshConfigsForReconciliation();
      },
    },
    {
      deletion: 'authoritative config snapshot omission',
      deleteConfig: async (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({
          type: 'snapshot',
          received_at: 1700000003000,
          data: { strategy_configs: [] },
        });
      },
    },
  ])('rejects ordinary runtime events throughout $deletion after a prior recreation barrier', async ({ deleteConfig }) => {
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.applyWebSocketMessage({
      type: 'strategy_config_deleted',
      strategy: 'btc_ma',
      timestamp: 1700000001000,
    });
    store.applyWebSocketMessage({
      type: 'strategy_config_created',
      strategy: 'btc_ma',
      config: { ...btcConfig, updated_at: 1700000002000 },
      timestamp: 1700000002000,
    });

    await deleteConfig(store);
    const statusesAfterDeletion = { ...store.statuses };
    const errorsAfterDeletion = { ...store.errors };
    const reconcile = vi.spyOn(store, 'refreshStatusesForReconciliation').mockResolvedValue();
    store.applyWebSocketMessage({
      type: 'strategy_status',
      strategy: 'btc_ma',
      status: 'running',
      timestamp: 1700000004000,
    });
    store.applyWebSocketMessage({
      type: 'strategy_error',
      strategy: 'btc_ma',
      error: 'deleted boom',
      timestamp: 1700000004000,
    });

    expect(store.configTombstones.btc_ma).toBeDefined();
    expect(store.statuses).toEqual(statusesAfterDeletion);
    expect(store.errors).toEqual(errorsAfterDeletion);
    expect(reconcile).toHaveBeenCalledTimes(2);
  });

  it('uses received_at to reject runtime events during deletion and accept them only after recreation', () => {
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    store.applyWebSocketMessage({
      type: 'strategy_config_deleted',
      strategy: 'btc_ma',
      received_at: 1700000001000,
    });
    store.applyWebSocketMessage({
      type: 'strategy_status',
      strategy: 'btc_ma',
      status: 'running',
      received_at: 1700000002000,
    });
    store.applyWebSocketMessage({
      type: 'strategy_error',
      strategy: 'btc_ma',
      error: 'deleted boom',
      received_at: 1700000002000,
    });

    expect(store.statuses).toEqual({});
    expect(store.errors).toEqual({});

    store.applyWebSocketMessage({
      type: 'strategy_config_created',
      strategy: 'btc_ma',
      config: { ...btcConfig, updated_at: 1700000003000 },
      received_at: 1700000003000,
    });
    store.applyWebSocketMessage({
      type: 'strategy_status',
      strategy: 'btc_ma',
      status: 'running',
      received_at: 1700000003000,
    });
    store.applyWebSocketMessage({
      type: 'strategy_error',
      strategy: 'btc_ma',
      error: 'equal boom',
      received_at: 1700000003000,
    });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'stopped' } });
    expect(store.errors).toEqual({});

    store.applyWebSocketMessage({
      type: 'strategy_status',
      strategy: 'btc_ma',
      status: 'running',
      received_at: 1700000004000,
    });
    store.applyWebSocketMessage({
      type: 'strategy_error',
      strategy: 'btc_ma',
      error: 'current boom',
      received_at: 1700000004000,
    });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'current boom' });
  });

  it('lets authoritative runtime snapshots bypass the deletion-phase ordinary-event gate', async () => {
    mockedService.listStrategies.mockResolvedValueOnce([
      { name: 'btc_ma', status: 'running', error: 'REST boom' },
    ]);
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.removeConfig('btc_ma');

    await store.refreshStatusesForReconciliation();
    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'REST boom' });

    store.applyWebSocketMessage({
      type: 'snapshot',
      received_at: 1700000002000,
      data: {
        strategies: [{ name: 'btc_ma', status: 'stopped' }],
        strategy_errors: { btc_ma: 'snapshot boom' },
      },
    });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'stopped' } });
    expect(store.errors).toEqual({ btc_ma: 'snapshot boom' });
  });

  it('ignores older config events and treats equal updated_at events idempotently', () => {
    const store = useStrategiesStore();
    const newer = { ...btcConfig, timeframe: '5m', updated_at: 1700000002000 };
    const older = { ...btcConfig, timeframe: '15m', updated_at: 1700000001000 };
    const sameTimestampDuplicate = { ...btcConfig, timeframe: '1h', updated_at: 1700000002000 };

    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: newer });
    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: older });
    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: sameTimestampDuplicate });

    expect(store.configs).toEqual([newer]);
  });

  it('rejects non-finite config timestamps so websocket ordering remains recoverable', () => {
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    const poison = { ...btcConfig, timeframe: '5m', updated_at: Infinity } as StrategyConfig;
    const valid = { ...btcConfig, timeframe: '15m', updated_at: 1700000001000 };

    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: poison });
    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: valid });

    expect(store.configs).toEqual([valid]);
  });

  it('rejects object-valued config params from websocket payloads', () => {
    const store = useStrategiesStore();
    const malformed = {
      ...btcConfig,
      params: { fast: { nested: true } },
      updated_at: 1700000001000,
    } as unknown as StrategyConfig;

    store.applyWebSocketMessage({ type: 'strategy_config_created', strategy: 'btc_ma', config: malformed });

    expect(store.configs).toEqual([]);
    expect(store.statuses).toEqual({});
  });

  it('does not let stale initial REST configs resurrect a websocket-deleted config', async () => {
    const definitions = deferred<StrategyDefinition[]>();
    const configs = deferred<StrategyConfig[]>();
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategyTypes.mockReturnValueOnce(definitions.promise);
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    const loadPromise = store.loadInitialData();
    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });

    definitions.resolve([definition]);
    configs.resolve([btcConfig]);
    statuses.resolve([]);
    await loadPromise;

    expect(store.configs).toEqual([]);
  });

  it('does not let an authoritative empty config snapshot be overwritten by earlier initial REST configs', async () => {
    const definitions = deferred<StrategyDefinition[]>();
    const configs = deferred<StrategyConfig[]>();
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategyTypes.mockReturnValueOnce(definitions.promise);
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    const loadPromise = store.loadInitialData();
    store.applyWebSocketMessage({ type: 'snapshot', data: { strategy_configs: [] } });

    definitions.resolve([definition]);
    configs.resolve([btcConfig]);
    statuses.resolve([stoppedBtc]);
    await loadPromise;

    expect(store.configs).toEqual([]);
    expect(store.definitions).toEqual([definition]);
    expect(store.statuses).toEqual({ btc_ma: stoppedBtc });
  });

  it('does not let stale reconciliation refreshes overwrite newer config changes', async () => {
    const configs = deferred<StrategyConfig[]>();
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    const refreshPromise = store.refreshConfigsForReconciliation();
    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });

    configs.resolve([btcConfig]);
    await refreshPromise;

    expect(store.configs).toEqual([]);
  });

  it('does not let stale reconciliation statuses overwrite newer websocket status or error', async () => {
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    const refreshPromise = store.refreshStatusesForReconciliation();
    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running' });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'new boom' });

    statuses.resolve([{ name: 'btc_ma', status: 'stopped' }]);
    await refreshPromise;

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'new boom' });
  });

  it('keeps mutation success when later reconciliation refresh fails', async () => {
    const store = useStrategiesStore();
    mockedService.createStrategyConfig.mockResolvedValueOnce(btcConfig);
    mockedService.listStrategyConfigs.mockRejectedValueOnce(new Error('refresh failed'));

    await expect(store.createConfig(btcConfig)).resolves.toEqual(btcConfig);

    expect(store.configs).toEqual([btcConfig]);
    expect(store.reconciliationError).toBe('refresh failed');
  });

  it('replaces existing config from authoritative update success even with equal updated_at', async () => {
    const saved = { ...btcConfig, timeframe: '5m', updated_at: btcConfig.updated_at };
    mockedService.updateStrategyConfig.mockResolvedValueOnce(saved);
    mockedService.listStrategyConfigs.mockResolvedValueOnce([saved]);
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    await expect(store.updateConfig('btc_ma', saved)).resolves.toEqual(saved);

    expect(store.configs).toEqual([saved]);
  });

  it('keeps equal-timestamp reconciliation conservative for existing configs', async () => {
    const equalTimestamp = { ...btcConfig, timeframe: '5m', updated_at: btcConfig.updated_at };
    mockedService.listStrategyConfigs.mockResolvedValueOnce([equalTimestamp]);
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    await store.refreshConfigsForReconciliation();

    expect(store.configs).toEqual([btcConfig]);
  });

  it('does not let stale config reconciliation resurrect a config deleted by later snapshot', async () => {
    const configs = deferred<StrategyConfig[]>();
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    const refreshPromise = store.refreshConfigsForReconciliation();

    store.applyWebSocketMessage({ type: 'snapshot', data: { strategy_configs: [] } });
    configs.resolve([btcConfig]);
    await refreshPromise;

    expect(store.configs).toEqual([]);
  });

  it('does not let an authoritative empty config snapshot be overwritten by earlier config reconciliation', async () => {
    const configs = deferred<StrategyConfig[]>();
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);

    const store = useStrategiesStore();
    const refreshPromise = store.refreshConfigsForReconciliation();

    store.applyWebSocketMessage({ type: 'snapshot', data: { strategy_configs: [] } });
    configs.resolve([btcConfig]);
    await refreshPromise;

    expect(store.configs).toEqual([]);
  });

  it('applies inline snapshot errors without clearing explicit errors omitted from status snapshots', () => {
    const store = useStrategiesStore();
    store.statuses = {
      btc_ma: { name: 'btc_ma', status: 'running' },
      eth_ma: { name: 'eth_ma', status: 'stopped' },
    };
    store.errors = { btc_ma: 'old boom', eth_ma: 'omitted boom' };
    store.errorRevisions = { btc_ma: 4, eth_ma: 5 };
    store.nextRevision = 6;

    store.applyWebSocketMessage({
      type: 'snapshot',
      data: {
        strategies: [
          { name: 'btc_ma', status: 'running' },
          { name: 'sol_ma', status: 'stopped', error: 'inline boom' },
        ],
      },
    });

    expect(store.statuses).toEqual({
      btc_ma: { name: 'btc_ma', status: 'running' },
      sol_ma: { name: 'sol_ma', status: 'stopped' },
    });
    expect(store.errors).toEqual({ btc_ma: 'old boom', eth_ma: 'omitted boom', sol_ma: 'inline boom' });
    expect(store.errorRevisions.btc_ma).toBe(4);
    expect(store.errorRevisions.eth_ma).toBe(5);
    expect(store.errorRevisions.sol_ma).toBeGreaterThan(5);
  });

  it('lets explicit strategy errors remain authoritative after inline snapshot reconciliation', () => {
    const store = useStrategiesStore();
    store.errors = { eth_ma: 'old boom' };

    store.applyWebSocketMessage({
      type: 'snapshot',
      data: {
        strategies: [
          { name: 'btc_ma', status: 'running', error: 'inline boom' },
          { name: 'eth_ma', status: 'running' },
        ],
        strategy_errors: { eth_ma: 'explicit boom' },
      },
    });

    expect(store.errors).toEqual({ eth_ma: 'explicit boom' });
  });

  it('ignores CRUD completions from before reset while returning their service results', async () => {
    const created = { ...btcConfig, name: 'created_ma' };
    const updated = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    const cloned = { ...btcConfig, name: 'cloned_ma' };
    const operations = [
      {
        name: 'create',
        arrange: () => {
          const result = deferred<StrategyConfig>();
          mockedService.createStrategyConfig.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().createConfig(created) as Promise<unknown>, expected: created };
        },
      },
      {
        name: 'update',
        arrange: () => {
          const result = deferred<StrategyConfig>();
          mockedService.updateStrategyConfig.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().updateConfig('btc_ma', updated) as Promise<unknown>, expected: updated };
        },
      },
      {
        name: 'clone',
        arrange: () => {
          const result = deferred<StrategyConfig>();
          mockedService.cloneStrategyConfig.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().cloneConfig('btc_ma', { target_name: 'cloned_ma' }) as Promise<unknown>, expected: cloned };
        },
      },
      {
        name: 'delete',
        arrange: () => {
          const result = deferred<void>();
          mockedService.deleteStrategyConfig.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().deleteConfig('btc_ma') as Promise<unknown>, expected: undefined };
        },
      },
    ];

    for (const operation of operations) {
      setActivePinia(createPinia());
      vi.resetAllMocks();
      mockedService.listStrategyConfigs.mockResolvedValue([]);
      const store = useStrategiesStore();
      store.configs = [btcConfig];
      store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' } };
      store.errors = { btc_ma: 'boom' };
      store.actionLoading = { 'btc_ma:start': true };
      const { result, promise, expected } = operation.arrange();

      store.reset();
      result.resolve(expected as never);
      await expect(promise).resolves.toEqual(expected);

      expect(store.configs, operation.name).toEqual([]);
      expect(store.statuses, operation.name).toEqual({});
      expect(store.errors, operation.name).toEqual({});
      expect(store.actionLoading, operation.name).toEqual({});
      expect(mockedService.listStrategyConfigs, operation.name).not.toHaveBeenCalled();
    }
  });

  it('ignores start and stop completions from before reset without repopulating state or loading', async () => {
    const operations = [
      {
        name: 'start',
        arrange: () => {
          const result = deferred<{ status: string; strategy: string }>();
          mockedService.startStrategy.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().start('btc_ma') };
        },
      },
      {
        name: 'stop',
        arrange: () => {
          const result = deferred<{ status: string; strategy: string }>();
          mockedService.stopStrategy.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().stop('btc_ma') };
        },
      },
    ];

    for (const operation of operations) {
      setActivePinia(createPinia());
      vi.resetAllMocks();
      mockedService.listStrategies.mockResolvedValue([]);
      const store = useStrategiesStore();
      const { result, promise } = operation.arrange();
      expect(store.actionLoading).toEqual({ [`btc_ma:${operation.name}`]: true });

      store.reset();
      result.resolve({ status: `${operation.name}ed`, strategy: 'btc_ma' });
      await expect(promise).resolves.toBeUndefined();

      expect(store.statuses, operation.name).toEqual({});
      expect(store.errors, operation.name).toEqual({});
      expect(store.actionLoading, operation.name).toEqual({});
      expect(mockedService.listStrategies, operation.name).not.toHaveBeenCalled();
    }
  });

  it('keeps newer same-key start loading and status when an older start resolves first', async () => {
    const older = deferred<{ status: string; strategy: string }>();
    const newer = deferred<{ status: string; strategy: string }>();
    mockedService.startStrategy
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);
    mockedService.listStrategies.mockRejectedValue(new Error('refresh failed'));

    const store = useStrategiesStore();
    const olderPromise = store.start('btc_ma');
    const newerPromise = store.start('btc_ma');

    older.resolve({ status: 'started', strategy: 'btc_ma' });
    await olderPromise;

    expect(store.isActionLoading('btc_ma', 'start')).toBe(true);
    expect(store.statuses).toEqual({});
    expect(mockedService.listStrategies).not.toHaveBeenCalled();

    newer.resolve({ status: 'started', strategy: 'btc_ma' });
    await newerPromise;

    expect(store.isActionLoading('btc_ma', 'start')).toBe(false);
    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(mockedService.listStrategies).toHaveBeenCalledTimes(1);
  });

  it('tracks action-specific loading so independent instance actions do not block each other', async () => {
    let resolveStart: (value: { status: string; strategy: string }) => void = () => {};
    mockedService.startStrategy.mockImplementationOnce(() => new Promise((resolve) => { resolveStart = resolve; }));
    mockedService.stopStrategy.mockResolvedValueOnce({ status: 'stopped', strategy: 'eth_ma' });
    mockedService.listStrategies.mockResolvedValue([
      { name: 'btc_ma', status: 'running' },
      { name: 'eth_ma', status: 'stopped' },
    ]);

    const store = useStrategiesStore();
    const startPromise = store.start('btc_ma');

    expect(store.isActionLoading('btc_ma', 'start')).toBe(true);
    expect(store.isActionLoading('eth_ma', 'stop')).toBe(false);

    await store.stop('eth_ma');

    expect(store.isActionLoading('btc_ma', 'start')).toBe(true);
    expect(store.isActionLoading('eth_ma', 'stop')).toBe(false);

    resolveStart({ status: 'started', strategy: 'btc_ma' });
    await startPromise;

    expect(store.isActionLoading('btc_ma', 'start')).toBe(false);
    expect(store.statuses.btc_ma?.status).toBe('running');
    expect(store.statuses.eth_ma?.status).toBe('stopped');
  });

  it('tracks and clears mutation loading for every persisted strategy action', async () => {
    const created = { ...btcConfig, name: 'desk:created' };
    const cloned = { ...btcConfig, name: 'desk:cloned' };
    const operations = [
      {
        action: 'create' as const,
        target: created.name,
        arrange: () => {
          const result = deferred<StrategyConfig>();
          mockedService.createStrategyConfig.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().createConfig(created), expected: created };
        },
      },
      {
        action: 'update' as const,
        target: btcConfig.name,
        arrange: () => {
          const result = deferred<StrategyConfig>();
          mockedService.updateStrategyConfig.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().updateConfig(btcConfig.name, btcConfig), expected: btcConfig };
        },
      },
      {
        action: 'clone' as const,
        target: cloned.name,
        arrange: () => {
          const result = deferred<StrategyConfig>();
          mockedService.cloneStrategyConfig.mockReturnValueOnce(result.promise);
          return {
            result,
            promise: useStrategiesStore().cloneConfig(btcConfig.name, { target_name: cloned.name }),
            expected: cloned,
          };
        },
      },
      {
        action: 'delete' as const,
        target: btcConfig.name,
        arrange: () => {
          const result = deferred<void>();
          mockedService.deleteStrategyConfig.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().deleteConfig(btcConfig.name), expected: undefined };
        },
      },
      {
        action: 'start' as const,
        target: 'desk:btc',
        arrange: () => {
          const result = deferred<{ status: string; strategy: string }>();
          mockedService.startStrategy.mockReturnValueOnce(result.promise);
          return {
            result,
            promise: useStrategiesStore().start('desk:btc'),
            expected: { status: 'started', strategy: 'desk:btc' },
          };
        },
      },
      {
        action: 'stop' as const,
        target: 'desk:btc',
        arrange: () => {
          const result = deferred<{ status: string; strategy: string }>();
          mockedService.stopStrategy.mockReturnValueOnce(result.promise);
          return {
            result,
            promise: useStrategiesStore().stop('desk:btc'),
            expected: { status: 'stopped', strategy: 'desk:btc' },
          };
        },
      },
    ];

    for (const operation of operations) {
      setActivePinia(createPinia());
      vi.resetAllMocks();
      mockedService.listStrategyConfigs.mockResolvedValue([]);
      mockedService.listStrategies.mockResolvedValue([]);
      const store = useStrategiesStore();
      store.configs = [btcConfig];
      const { result, promise, expected } = operation.arrange();

      expect(store.isMutationLoading(operation.target, operation.action), operation.action).toBe(true);
      result.resolve(expected as never);
      await promise;
      expect(store.isMutationLoading(operation.target, operation.action), operation.action).toBe(false);
    }
  });

  it('lets the newer-started config reconciliation win when overlapping requests resolve out of order', async () => {
    const olderConfigs = deferred<StrategyConfig[]>();
    const newerConfigs = deferred<StrategyConfig[]>();
    mockedService.listStrategyConfigs
      .mockReturnValueOnce(olderConfigs.promise)
      .mockReturnValueOnce(newerConfigs.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    const olderPromise = store.refreshConfigsForReconciliation();
    const newerPromise = store.refreshConfigsForReconciliation();

    newerConfigs.resolve([{ ...btcConfig, timeframe: '5m', updated_at: 1700000001000 }]);
    await newerPromise;
    olderConfigs.resolve([]);
    await olderPromise;

    expect(store.configs).toEqual([{ ...btcConfig, timeframe: '5m', updated_at: 1700000001000 }]);
  });

  it('lets the newer-started status reconciliation win when overlapping requests resolve out of order', async () => {
    const olderStatuses = deferred<StrategyRuntimeSummary[]>();
    const newerStatuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategies
      .mockReturnValueOnce(olderStatuses.promise)
      .mockReturnValueOnce(newerStatuses.promise);

    const store = useStrategiesStore();
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'stopped' } };
    store.errors = { btc_ma: 'old boom' };
    const olderPromise = store.refreshStatusesForReconciliation();
    const newerPromise = store.refreshStatusesForReconciliation();

    newerStatuses.resolve([{ name: 'btc_ma', status: 'running' }]);
    await newerPromise;
    olderStatuses.resolve([{ name: 'btc_ma', status: 'stopped', error: 'stale boom' }]);
    await olderPromise;

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({});
  });

  it('allows a later authoritative config refresh to observe a legal same-name rebuild after deletion', async () => {
    mockedService.listStrategyConfigs
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ ...btcConfig, updated_at: 1700000005000 }]);

    const store = useStrategiesStore();
    store.configs = [btcConfig];

    await store.refreshConfigsForReconciliation();
    expect(store.configs).toEqual([]);

    await store.refreshConfigsForReconciliation();
    expect(store.configs).toEqual([{ ...btcConfig, updated_at: 1700000005000 }]);
  });

  it('removes authoritative REST omissions only when unchanged since request start', async () => {
    const configs = deferred<StrategyConfig[]>();
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig, ethConfig];
    const refreshPromise = store.refreshConfigsForReconciliation();
    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'eth_ma', config: { ...ethConfig, timeframe: '5m', updated_at: 1700000001000 } });

    configs.resolve([]);
    await refreshPromise;

    expect(store.configs).toEqual([{ ...ethConfig, timeframe: '5m', updated_at: 1700000001000 }]);
  });

  it('removes authoritative REST status and error omissions only when unchanged since request start', async () => {
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    store.statuses = {
      btc_ma: { name: 'btc_ma', status: 'running' },
      eth_ma: { name: 'eth_ma', status: 'stopped' },
    };
    store.errors = { btc_ma: 'old boom', eth_ma: 'keep boom' };
    const refreshPromise = store.refreshStatusesForReconciliation();
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'eth_ma', error: 'new boom' });

    statuses.resolve([]);
    await refreshPromise;

    expect(store.statuses).toEqual({});
    expect(store.errors).toEqual({ eth_ma: 'new boom' });
  });

  it('applies websocket snapshots as authoritative without replacing newer configs or leaving omitted errors', async () => {
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    const newerBtcConfig = { ...btcConfig, timeframe: '5m', updated_at: 1700000005000 };
    store.configs = [newerBtcConfig, ethConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' }, eth_ma: { name: 'eth_ma', status: 'running' } };
    store.errors = { btc_ma: 'old boom', eth_ma: 'stale boom' };
    const refreshPromise = store.refreshStatusesForReconciliation();

    store.applyWebSocketMessage({
      type: 'snapshot',
      data: {
        strategy_configs: [{ ...btcConfig, timeframe: '15m', updated_at: 1700000001000 }],
        strategies: [{ name: 'btc_ma', status: 'stopped' }],
        strategy_errors: {},
      },
    });
    statuses.resolve([{ name: 'eth_ma', status: 'running', error: 'resurrected' }]);
    await refreshPromise;

    expect(store.configs).toEqual([newerBtcConfig]);
    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'stopped' } });
    expect(store.errors).toEqual({});
  });

  it('ignores load and reconciliation completions from before reset', async () => {
    const definitions = deferred<StrategyDefinition[]>();
    const initialConfigs = deferred<StrategyConfig[]>();
    const initialStatuses = deferred<StrategyRuntimeSummary[]>();
    const refreshConfigs = deferred<StrategyConfig[]>();
    const refreshStatuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategyTypes.mockReturnValueOnce(definitions.promise);
    mockedService.listStrategyConfigs
      .mockReturnValueOnce(initialConfigs.promise)
      .mockReturnValueOnce(refreshConfigs.promise);
    mockedService.listStrategies
      .mockReturnValueOnce(initialStatuses.promise)
      .mockReturnValueOnce(refreshStatuses.promise);

    const store = useStrategiesStore();
    const loadPromise = store.loadInitialData();
    const configPromise = store.refreshConfigsForReconciliation();
    const statusPromise = store.refreshStatusesForReconciliation();

    store.reset();
    definitions.resolve([definition]);
    initialConfigs.resolve([btcConfig]);
    initialStatuses.resolve([stoppedBtc]);
    refreshConfigs.reject(new Error('config failed'));
    refreshStatuses.resolve([{ name: 'btc_ma', status: 'running', error: 'boom' }]);
    await Promise.all([loadPromise, configPromise, statusPromise]);

    expect(store.definitions).toEqual([]);
    expect(store.configs).toEqual([]);
    expect(store.statuses).toEqual({});
    expect(store.errors).toEqual({});
    expect(store.loadingInitial).toBe(false);
    expect(store.error).toBeNull();
    expect(store.reconciliationError).toBeNull();
  });

  it('treats empty runtime and error snapshots as barriers for older unknown REST statuses', async () => {
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    const refreshPromise = store.refreshStatusesForReconciliation();
    store.applyWebSocketMessage({ type: 'snapshot', data: { strategies: [], strategy_errors: {} } });

    statuses.resolve([{ name: 'btc_ma', status: 'running', error: 'stale boom' }]);
    await refreshPromise;

    expect(store.statuses).toEqual({});
    expect(store.errors).toEqual({});
  });

  it('lets initial definitions and configs apply when an empty runtime snapshot bars only old statuses', async () => {
    const definitions = deferred<StrategyDefinition[]>();
    const configs = deferred<StrategyConfig[]>();
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategyTypes.mockReturnValueOnce(definitions.promise);
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    const loadPromise = store.loadInitialData();
    store.applyWebSocketMessage({ type: 'snapshot', data: { strategies: [] } });

    definitions.resolve([definition]);
    configs.resolve([btcConfig]);
    statuses.resolve([{ name: 'btc_ma', status: 'running', error: 'stale boom' }]);
    await loadPromise;

    expect(store.definitions).toEqual([definition]);
    expect(store.configs).toEqual([btcConfig]);
    expect(store.statuses).toEqual({});
    expect(store.errors).toEqual({});
  });

  it('lets old status data apply after an explicit error snapshot while blocking inline REST errors', async () => {
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    const refreshPromise = store.refreshStatusesForReconciliation();
    store.applyWebSocketMessage({ type: 'snapshot', data: { strategy_errors: {} } });

    statuses.resolve([{ name: 'btc_ma', status: 'running', error: 'stale boom' }]);
    await refreshPromise;

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({});
  });

  it('does not let an update completion after deletion resurrect local state or reconcile', async () => {
    vi.resetAllMocks();
    const updated = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    const result = deferred<StrategyConfig>();
    mockedService.updateStrategyConfig.mockReturnValueOnce(result.promise);
    mockedService.listStrategyConfigs.mockResolvedValueOnce([]);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' } };
    const updatePromise = store.updateConfig('btc_ma', updated);
    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });

    result.resolve(updated);
    await expect(updatePromise).resolves.toEqual(updated);

    expect(store.configs).toEqual([]);
    expect(store.statuses).toEqual({});
    expect(store.configTombstones.btc_ma).toBeDefined();
    expect(mockedService.listStrategyConfigs).not.toHaveBeenCalled();
  });

  it('does not let create or clone completions after target deletion recreate state or reconcile', async () => {
    const operations = [
      {
        name: 'create',
        target: 'created_ma',
        saved: { ...btcConfig, name: 'created_ma' },
        arrange: (saved: StrategyConfig) => {
          const result = deferred<StrategyConfig>();
          mockedService.createStrategyConfig.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().createConfig(saved) };
        },
      },
      {
        name: 'clone',
        target: 'cloned_ma',
        saved: { ...btcConfig, name: 'cloned_ma' },
        arrange: (saved: StrategyConfig) => {
          const result = deferred<StrategyConfig>();
          mockedService.cloneStrategyConfig.mockReturnValueOnce(result.promise);
          return { result, promise: useStrategiesStore().cloneConfig('btc_ma', { target_name: saved.name }) };
        },
      },
    ];

    for (const operation of operations) {
      setActivePinia(createPinia());
      vi.resetAllMocks();
      mockedService.listStrategyConfigs.mockResolvedValueOnce([]);
      const store = useStrategiesStore();
      const { result, promise } = operation.arrange(operation.saved);
      store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: operation.target });

      result.resolve(operation.saved);
      await expect(promise, operation.name).resolves.toEqual(operation.saved);

      expect(store.configs, operation.name).toEqual([]);
      expect(store.statuses, operation.name).toEqual({});
      expect(store.configTombstones[operation.target], operation.name).toBeDefined();
      expect(mockedService.listStrategyConfigs, operation.name).not.toHaveBeenCalled();
    }
  });

  it.each([
    {
      name: 'create',
      arrange: (saved: StrategyConfig) => {
        const result = deferred<StrategyConfig>();
        mockedService.createStrategyConfig.mockReturnValueOnce(result.promise);
        return { result, promise: useStrategiesStore().createConfig(saved) };
      },
    },
    {
      name: 'update',
      arrange: (saved: StrategyConfig) => {
        const result = deferred<StrategyConfig>();
        mockedService.updateStrategyConfig.mockReturnValueOnce(result.promise);
        return { result, promise: useStrategiesStore().updateConfig(saved.name, saved) };
      },
    },
    {
      name: 'clone',
      arrange: (saved: StrategyConfig) => {
        const result = deferred<StrategyConfig>();
        mockedService.cloneStrategyConfig.mockReturnValueOnce(result.promise);
        return { result, promise: useStrategiesStore().cloneConfig('source_ma', { target_name: saved.name }) };
      },
    },
  ])('does not let pending $name resurrect config after newer authoritative delete updates an existing tombstone', async ({ arrange }) => {
    const resultConfig = { ...btcConfig, timeframe: '5m', updated_at: 1700000003000 };
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' } };

    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });
    const { result, promise } = arrange(resultConfig);
    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000002000 });

    result.resolve(resultConfig);
    await expect(promise).resolves.toEqual(resultConfig);

    expect(store.configs).toEqual([]);
    expect(store.statuses).toEqual({});
    expect(mockedService.listStrategyConfigs).not.toHaveBeenCalled();
  });

  it('does not let an older delete completion remove a newer same-name config event', async () => {
    const result = deferred<void>();
    const newer = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    mockedService.deleteStrategyConfig.mockReturnValueOnce(result.promise);
    mockedService.listStrategyConfigs.mockResolvedValueOnce([]);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    const deletePromise = store.deleteConfig('btc_ma');
    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: newer });

    result.resolve();
    await deletePromise;

    expect(store.configs).toEqual([newer]);
    expect(store.configTombstones.btc_ma).toBeUndefined();
    expect(mockedService.listStrategyConfigs).not.toHaveBeenCalled();
  });

  it('allows requests started after deletion to observe legitimate same-name recreation', async () => {
    await Promise.resolve();
    await Promise.resolve();
    vi.resetAllMocks();
    const recreated = { ...btcConfig, updated_at: 1700000005000 };
    const updatedRecreation = { ...recreated, timeframe: '5m', updated_at: 1700000006000 };
    const createRefresh = deferred<StrategyConfig[]>();
    const updateRefresh = deferred<StrategyConfig[]>();
    mockedService.createStrategyConfig.mockResolvedValueOnce(recreated);
    mockedService.updateStrategyConfig.mockResolvedValueOnce(updatedRecreation);
    mockedService.listStrategyConfigs
      .mockReturnValueOnce(createRefresh.promise)
      .mockResolvedValueOnce([recreated])
      .mockReturnValueOnce(updateRefresh.promise);

    const store = useStrategiesStore();
    store.removeConfig('btc_ma');

    await expect(store.createConfig(btcConfig)).resolves.toEqual(recreated);
    expect(store.configs).toEqual([recreated]);
    expect(store.configTombstones.btc_ma).toBeUndefined();

    store.removeConfig('btc_ma');
    await store.refreshConfigsForReconciliation();
    expect(store.configs).toEqual([recreated]);
    expect(store.configTombstones.btc_ma).toBeUndefined();

    store.removeConfig('btc_ma');
    await expect(store.updateConfig('btc_ma', btcConfig)).resolves.toEqual(updatedRecreation);
    expect(store.configs).toEqual([updatedRecreation]);
    expect(store.configTombstones.btc_ma).toBeUndefined();
  });

  it.each(['start', 'stop'] as const)('ignores pending %s completion after config deletion', async (action) => {
    const result = deferred<{ status: string; strategy: string }>();
    const service = action === 'start' ? mockedService.startStrategy : mockedService.stopStrategy;
    service.mockReturnValueOnce(result.promise);
    mockedService.listStrategies.mockResolvedValueOnce([]);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: stoppedBtc };
    const actionPromise = store[action]('btc_ma');

    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });
    result.resolve({ status: action === 'start' ? 'started' : 'stopped', strategy: 'btc_ma' });
    await actionPromise;

    expect(store.configs).toEqual([]);
    expect(store.statuses).toEqual({});
    expect(store.actionLoading).toEqual({});
    expect(mockedService.listStrategies).not.toHaveBeenCalled();
  });

  it.each([
    {
      action: 'start' as const,
      caseName: 'unchanged-target authoritative config snapshot',
      initialStatus: 'stopped' as const,
      initialErrors: {} as Record<string, string>,
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({ type: 'snapshot', data: { strategy_configs: [btcConfig] } });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'stopped' } },
      expectedErrors: {},
    },
    {
      action: 'stop' as const,
      caseName: 'unchanged-target authoritative config snapshot',
      initialStatus: 'running' as const,
      initialErrors: {} as Record<string, string>,
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({ type: 'snapshot', data: { strategy_configs: [btcConfig] } });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'running' } },
      expectedErrors: {},
    },
    {
      action: 'start' as const,
      caseName: 'same-value authoritative status snapshot',
      initialStatus: 'stopped' as const,
      initialErrors: {} as Record<string, string>,
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({
          type: 'snapshot',
          received_at: 1700000001000,
          data: { strategies: [{ name: 'btc_ma', status: 'stopped' }] },
        });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'stopped' } },
      expectedErrors: {},
    },
    {
      action: 'stop' as const,
      caseName: 'same-value authoritative status snapshot',
      initialStatus: 'running' as const,
      initialErrors: {} as Record<string, string>,
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({
          type: 'snapshot',
          received_at: 1700000001000,
          data: { strategies: [{ name: 'btc_ma', status: 'running' }] },
        });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'running' } },
      expectedErrors: {},
    },
    {
      action: 'start' as const,
      caseName: 'empty authoritative status snapshot',
      initialStatus: 'stopped' as const,
      initialErrors: {} as Record<string, string>,
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({ type: 'snapshot', received_at: 1700000001000, data: { strategies: [] } });
      },
      expectedStatuses: {},
      expectedErrors: {},
    },
    {
      action: 'stop' as const,
      caseName: 'empty authoritative status snapshot',
      initialStatus: 'running' as const,
      initialErrors: {} as Record<string, string>,
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({ type: 'snapshot', received_at: 1700000001000, data: { strategies: [] } });
      },
      expectedStatuses: {},
      expectedErrors: {},
    },
    {
      action: 'start' as const,
      caseName: 'same-value authoritative error snapshot',
      initialStatus: 'stopped' as const,
      initialErrors: { btc_ma: 'boom' },
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({
          type: 'snapshot',
          received_at: 1700000001000,
          data: { strategy_errors: { btc_ma: 'boom' } },
        });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'stopped' } },
      expectedErrors: { btc_ma: 'boom' },
    },
    {
      action: 'stop' as const,
      caseName: 'same-value authoritative error snapshot',
      initialStatus: 'running' as const,
      initialErrors: { btc_ma: 'boom' },
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({
          type: 'snapshot',
          received_at: 1700000001000,
          data: { strategy_errors: { btc_ma: 'boom' } },
        });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'running' } },
      expectedErrors: { btc_ma: 'boom' },
    },
    {
      action: 'start' as const,
      caseName: 'empty authoritative error snapshot',
      initialStatus: 'stopped' as const,
      initialErrors: { btc_ma: 'boom' },
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({ type: 'snapshot', received_at: 1700000001000, data: { strategy_errors: {} } });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'stopped' } },
      expectedErrors: {},
    },
    {
      action: 'stop' as const,
      caseName: 'empty authoritative error snapshot',
      initialStatus: 'running' as const,
      initialErrors: { btc_ma: 'boom' },
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({ type: 'snapshot', received_at: 1700000001000, data: { strategy_errors: {} } });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'running' } },
      expectedErrors: {},
    },
    {
      action: 'start' as const,
      caseName: 'newer strategy_error event',
      initialStatus: 'stopped' as const,
      initialErrors: { btc_ma: 'old boom' },
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'new boom' });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'stopped' } },
      expectedErrors: { btc_ma: 'new boom' },
    },
    {
      action: 'stop' as const,
      caseName: 'newer strategy_error event',
      initialStatus: 'running' as const,
      initialErrors: { btc_ma: 'old boom' },
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => {
        store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'new boom' });
      },
      expectedStatuses: { btc_ma: { name: 'btc_ma', status: 'running' } },
      expectedErrors: { btc_ma: 'new boom' },
    },
  ])('ignores pending $action completion after $caseName', async ({
    action,
    initialStatus,
    initialErrors,
    invalidate,
    expectedStatuses,
    expectedErrors,
  }) => {
    const result = deferred<{ status: string; strategy: string }>();
    const service = action === 'start' ? mockedService.startStrategy : mockedService.stopStrategy;
    service.mockReturnValueOnce(result.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: initialStatus } };
    store.errors = initialErrors;
    const actionPromise = store[action]('btc_ma');

    invalidate(store);
    const reconcile = vi.spyOn(store, 'refreshStatusesForReconciliation').mockResolvedValue();
    result.resolve({ status: action === 'start' ? 'started' : 'stopped', strategy: 'btc_ma' });
    await expect(actionPromise).resolves.toBeUndefined();

    expect(store.configs).toEqual([btcConfig]);
    expect(store.statuses).toEqual(expectedStatuses);
    expect(store.errors).toEqual(expectedErrors);
    expect(store.actionLoading).toEqual({});
    expect(reconcile).not.toHaveBeenCalled();
    expect(mockedService.listStrategies).not.toHaveBeenCalled();
  });

  it('does not let deletion-stale action finally clear newer same-key loading', async () => {
    const stale = deferred<{ status: string; strategy: string }>();
    const current = deferred<{ status: string; strategy: string }>();
    mockedService.startStrategy
      .mockReturnValueOnce(stale.promise)
      .mockReturnValueOnce(current.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    const stalePromise = store.start('btc_ma');
    store.removeConfig('btc_ma');
    store.applyConfig({ ...btcConfig, updated_at: 1700000001000 });
    const currentPromise = store.start('btc_ma');

    stale.resolve({ status: 'started', strategy: 'btc_ma' });
    await stalePromise;
    expect(store.isActionLoading('btc_ma', 'start')).toBe(true);

    current.resolve({ status: 'started', strategy: 'btc_ma' });
    await currentPromise;
    expect(store.isActionLoading('btc_ma', 'start')).toBe(false);
  });

  it.each([
    ['status event', (store: ReturnType<typeof useStrategiesStore>) => store.applyStatus('btc_ma', 'stopped')],
    ['status snapshot', (store: ReturnType<typeof useStrategiesStore>) => store.applySnapshot({ strategies: [{ name: 'btc_ma', status: 'stopped' }] })],
  ] as const)('does not let pending start overwrite a newer authoritative %s', async (_label, applyNewerStatus) => {
    const result = deferred<{ status: string; strategy: string }>();
    mockedService.startStrategy.mockReturnValueOnce(result.promise);

    const store = useStrategiesStore();
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' } };
    const startPromise = store.start('btc_ma');
    applyNewerStatus(store);

    result.resolve({ status: 'started', strategy: 'btc_ma' });
    await startPromise;

    expect(store.statuses).toEqual({ btc_ma: stoppedBtc });
    expect(mockedService.listStrategies).not.toHaveBeenCalled();
  });

  it.each([
    { older: 'start', newer: 'stop', finalStatus: 'stopped' },
    { older: 'stop', newer: 'start', finalStatus: 'running' },
  ] as const)('lets newer $newer win when opposite lifecycle completions resolve out of order', async ({ older, newer, finalStatus }) => {
    const olderResult = deferred<{ status: string; strategy: string }>();
    const newerResult = deferred<{ status: string; strategy: string }>();
    const olderService = older === 'start' ? mockedService.startStrategy : mockedService.stopStrategy;
    const newerService = newer === 'start' ? mockedService.startStrategy : mockedService.stopStrategy;
    olderService.mockReturnValueOnce(olderResult.promise);
    newerService.mockReturnValueOnce(newerResult.promise);
    mockedService.listStrategies.mockResolvedValue([{ name: 'btc_ma', status: finalStatus }]);

    const store = useStrategiesStore();
    store.statuses = { btc_ma: stoppedBtc };
    const olderPromise = store[older]('btc_ma');
    const newerPromise = store[newer]('btc_ma');

    newerResult.resolve({ status: `${newer}ed`, strategy: 'btc_ma' });
    await newerPromise;
    olderResult.resolve({ status: `${older}ed`, strategy: 'btc_ma' });
    await olderPromise;

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: finalStatus } });
    expect(mockedService.listStrategies).toHaveBeenCalledTimes(1);
  });

  it('does not let pending lifecycle completion overwrite a newer config instance', async () => {
    const result = deferred<{ status: string; strategy: string }>();
    mockedService.startStrategy.mockReturnValueOnce(result.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: stoppedBtc };
    const startPromise = store.start('btc_ma');
    store.applyConfig({ ...btcConfig, timeframe: '5m', updated_at: 1700000001000 });

    result.resolve({ status: 'started', strategy: 'btc_ma' });
    await startPromise;

    expect(store.statuses).toEqual({ btc_ma: stoppedBtc });
    expect(mockedService.listStrategies).not.toHaveBeenCalled();
  });

  it.each(['create', 'clone'] as const)('bars pending unknown-target %s with an authoritative empty config snapshot', async (operation) => {
    const saved = { ...btcConfig, name: `${operation}_target` };
    const result = deferred<StrategyConfig>();
    const store = useStrategiesStore();
    const promise = operation === 'create'
      ? (mockedService.createStrategyConfig.mockReturnValueOnce(result.promise), store.createConfig(saved))
      : (mockedService.cloneStrategyConfig.mockReturnValueOnce(result.promise), store.cloneConfig('btc_ma', { target_name: saved.name }));

    store.applySnapshot({ strategy_configs: [] });
    result.resolve(saved);
    await expect(promise).resolves.toEqual(saved);

    expect(store.configs).toEqual([]);
    expect(store.statuses).toEqual({});
    expect(store.configTombstones[saved.name]).toBeUndefined();
    expect(mockedService.listStrategyConfigs).not.toHaveBeenCalled();
  });

  it('does not let a duplicate delete invalidate post-deletion recreation', async () => {
    const saved = { ...btcConfig, updated_at: 1700000005000 };
    const result = deferred<StrategyConfig>();
    mockedService.createStrategyConfig.mockReturnValueOnce(result.promise);
    mockedService.listStrategyConfigs.mockResolvedValueOnce([saved]);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.removeConfig('btc_ma');
    const createPromise = store.createConfig(btcConfig);
    const tombstone = store.configTombstones.btc_ma;
    store.removeConfig('btc_ma');

    expect(store.configTombstones.btc_ma).toBe(tombstone);
    result.resolve(saved);
    await createPromise;

    expect(store.configs).toEqual([saved]);
  });

  it('keeps per-name revisions stable across repeated equal config snapshots', async () => {
    const updated = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    const result = deferred<StrategyConfig>();
    mockedService.updateStrategyConfig.mockReturnValueOnce(result.promise);

    const store = useStrategiesStore();
    store.applySnapshot({ strategy_configs: [btcConfig] });
    const revision = store.configRevisions.btc_ma;
    const updatePromise = store.updateConfig('btc_ma', updated);
    store.applySnapshot({ strategy_configs: [btcConfig] });
    result.resolve(updated);
    await updatePromise;

    expect(store.configRevisions.btc_ma).toBe(revision);
    expect(store.configs).toEqual([btcConfig]);
    expect(mockedService.listStrategyConfigs).not.toHaveBeenCalled();
  });

  it('keeps equal status and error snapshots revision-idempotent', () => {
    const store = useStrategiesStore();
    store.applySnapshot({
      strategies: [{ name: 'btc_ma', status: 'running', error: 'boom' }],
      strategy_errors: { btc_ma: 'boom' },
    });
    const statusRevision = store.statusRevisions.btc_ma;
    const errorRevision = store.errorRevisions.btc_ma;

    store.applySnapshot({
      strategies: [{ name: 'btc_ma', status: 'running', error: 'boom' }],
      strategy_errors: { btc_ma: 'boom' },
    });

    expect(store.statusRevisions.btc_ma).toBe(statusRevision);
    expect(store.errorRevisions.btc_ma).toBe(errorRevision);
  });

  it('allows unknown-target CRUD started after a config snapshot', async () => {
    const saved = { ...btcConfig, name: 'post_snapshot' };
    mockedService.createStrategyConfig.mockResolvedValueOnce(saved);
    mockedService.listStrategyConfigs.mockResolvedValueOnce([saved]);

    const store = useStrategiesStore();
    store.applySnapshot({ strategy_configs: [] });
    await store.createConfig(saved);

    expect(store.configs).toEqual([saved]);
    expect(store.statuses).toEqual({ post_snapshot: { name: 'post_snapshot', status: 'stopped' } });
  });

  it('keeps status summaries error-free while explicit errors replace inline snapshot errors', () => {
    const store = useStrategiesStore();
    store.applySnapshot({ strategies: [{ name: 'btc_ma', status: 'running', error: 'inline boom' }] });
    store.applySnapshot({ strategy_errors: { btc_ma: 'explicit boom' } });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'event boom' });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'event boom' });
  });

  it('stores error-only REST reconciliation in the canonical error map', async () => {
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    store.statuses = { btc_ma: stoppedBtc };
    const refreshPromise = store.refreshStatusesForReconciliation();
    store.applyStatus('btc_ma', 'running');
    statuses.resolve([{ name: 'btc_ma', status: 'stopped', error: 'rest boom' }]);
    await refreshPromise;

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.errors).toEqual({ btc_ma: 'rest boom' });
    expect(store.statuses.btc_ma).not.toHaveProperty('error');
  });

  it('preserves config reconciliation failure when status reconciliation later succeeds', async () => {
    mockedService.listStrategyConfigs.mockRejectedValueOnce(new Error('config failed'));
    mockedService.listStrategies.mockResolvedValueOnce([]);
    const store = useStrategiesStore();

    await store.refreshConfigsForReconciliation();
    await store.refreshStatusesForReconciliation();

    expect(store.reconciliationError).toBe('config failed');
  });

  it('preserves status reconciliation failure when config reconciliation later succeeds', async () => {
    mockedService.listStrategies.mockRejectedValueOnce(new Error('status failed'));
    mockedService.listStrategyConfigs.mockResolvedValueOnce([]);
    const store = useStrategiesStore();

    await store.refreshStatusesForReconciliation();
    await store.refreshConfigsForReconciliation();

    expect(store.reconciliationError).toBe('status failed');
  });

  it('deleting foo preserves exact loading state for foo:bar', () => {
    const store = useStrategiesStore();
    store.actionLoading = {
      'foo:start': true,
      'foo:stop': true,
      'foo:bar:start': true,
      'foo:bar:stop': true,
    };

    store.removeConfig('foo');

    expect(store.actionLoading).toEqual({
      'foo:bar:start': true,
      'foo:bar:stop': true,
    });
  });

  it('does not resurrect a deleted config from older, equal, or timestamp-less websocket config events', async () => {
    mockedService.listStrategyConfigs.mockResolvedValue([]);
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });
    store.applyWebSocketMessage({
      type: 'strategy_config_updated',
      strategy: 'btc_ma',
      config: { ...btcConfig, timeframe: '5m', updated_at: 1700000000000 },
      timestamp: 1700000000000,
    });
    store.applyWebSocketMessage({
      type: 'strategy_config_created',
      strategy: 'btc_ma',
      config: { ...btcConfig, timeframe: '15m', updated_at: 1700000001000 },
      timestamp: 1700000001000,
    });
    store.applyWebSocketMessage({
      type: 'strategy_config_created',
      strategy: 'btc_ma',
      config: { ...btcConfig, timeframe: '1h', updated_at: 1700000002000 },
    });
    await Promise.resolve();

    expect(store.configs).toEqual([]);
    expect(mockedService.listStrategyConfigs).toHaveBeenCalledTimes(1);
  });

  it('allows unambiguous websocket recreation after deletion', () => {
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });
    const recreated = { ...btcConfig, timeframe: '5m', updated_at: 1700000002000 };
    store.applyWebSocketMessage({
      type: 'strategy_config_created',
      strategy: 'btc_ma',
      config: recreated,
      timestamp: 1700000003000,
    });

    expect(store.configs).toEqual([recreated]);
    expect(store.configTombstones.btc_ma).toBeUndefined();
  });

  it('does not let a replayed old delete remove a completed same-name recreation', async () => {
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });
    const recreated = { ...btcConfig, timeframe: '5m', updated_at: 1700000002000 };
    store.applyWebSocketMessage({
      type: 'strategy_config_created',
      strategy: 'btc_ma',
      config: recreated,
      timestamp: 1700000003000,
    });
    const revision = store.configRevisions.btc_ma;
    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });
    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000002000 });
    await Promise.resolve();

    expect(store.configs).toEqual([recreated]);
    expect(store.configRevisions.btc_ma).toBe(revision);
  });

  it('reconciles ambiguous current websocket delete without immediately removing config', async () => {
    const reconciled = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    mockedService.listStrategyConfigs.mockResolvedValueOnce([reconciled]);
    const store = useStrategiesStore();
    store.configs = [reconciled];

    store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: 'btc_ma', timestamp: 1700000001000 });
    await Promise.resolve();

    expect(store.configs).toEqual([reconciled]);
    expect(mockedService.listStrategyConfigs).toHaveBeenCalledTimes(1);
  });

  it('does not apply local status or clear a newer strategy error when pending start completes', async () => {
    const result = deferred<{ status: string; strategy: string }>();
    mockedService.startStrategy.mockReturnValueOnce(result.promise);
    const store = useStrategiesStore();
    store.errors = { btc_ma: 'old boom' };
    store.errorRevisions = { btc_ma: 1 };
    store.nextRevision = 2;

    const startPromise = store.start('btc_ma');
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'new boom' });
    result.resolve({ status: 'started', strategy: 'btc_ma' });
    await startPromise;

    expect(store.statuses).toEqual({});
    expect(store.errors).toEqual({ btc_ma: 'new boom' });
    expect(mockedService.listStrategies).not.toHaveBeenCalled();
  });

  it('does not clear a newer same-text timestamped strategy error when pending start completes', async () => {
    const result = deferred<{ status: string; strategy: string }>();
    mockedService.startStrategy.mockReturnValueOnce(result.promise);
    const store = useStrategiesStore();
    store.errors = { btc_ma: 'boom' };
    store.errorRevisions = { btc_ma: 1 };
    store.nextRevision = 2;

    const startPromise = store.start('btc_ma');
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'boom', timestamp: 1700000001000 });
    result.resolve({ status: 'started', strategy: 'btc_ma' });
    await startPromise;

    expect(store.errors).toEqual({ btc_ma: 'boom' });
  });

  it('keeps exact replayed timestamped strategy errors revision-idempotent', () => {
    const store = useStrategiesStore();

    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'boom', timestamp: 1700000001000 });
    const revision = store.errorRevisions.btc_ma;
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'boom', timestamp: 1700000001000 });

    expect(store.errors).toEqual({ btc_ma: 'boom' });
    expect(store.errorRevisions.btc_ma).toBe(revision);
  });

  it('reconciles but does not overwrite ambiguous equal-authority different strategy errors', async () => {
    const store = useStrategiesStore();
    const reconcile = vi.spyOn(store, 'refreshStatusesForReconciliation').mockResolvedValue();

    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'new failure', timestamp: 1700000001000 });
    const revision = store.errorRevisions.btc_ma;
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'delayed different failure', timestamp: 1700000001000 });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'delayed different failure', timestamp: 1700000001000 });
    await Promise.resolve();

    expect(store.errors).toEqual({ btc_ma: 'new failure' });
    expect(store.errorAuthorities).toEqual({ btc_ma: 1700000001000 });
    expect(store.errorRevisions.btc_ma).toBe(revision);
    expect(reconcile).toHaveBeenCalled();
  });

  it('uses received_at as timestamp-free strategy error authority without clearing it', () => {
    const store = useStrategiesStore();

    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'boom', received_at: 1700000001000 });
    const revision = store.errorRevisions.btc_ma;
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'stale boom', received_at: 1700000000000 });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'boom', received_at: 1700000002000 });

    expect(store.errors).toEqual({ btc_ma: 'boom' });
    expect(store.errorAuthorities).toEqual({
      btc_ma: { timestamp: undefined, receivedAt: 1700000002000 },
    });
    expect(store.errorRevisions.btc_ma).toBeGreaterThan(revision);
  });

  it('does not let ordinary running status or status snapshot clear explicit errors', () => {
    const store = useStrategiesStore();
    store.errors = { btc_ma: 'explicit boom' };

    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running' });
    store.applySnapshot({ strategies: [{ name: 'btc_ma', status: 'running' }] });

    expect(store.errors).toEqual({ btc_ma: 'explicit boom' });
  });

  it('clears old error on successful start only when error revision is unchanged', async () => {
    const result = deferred<{ status: string; strategy: string }>();
    mockedService.startStrategy.mockReturnValueOnce(result.promise);
    const store = useStrategiesStore();
    store.errors = { btc_ma: 'old boom' };
    store.errorRevisions = { btc_ma: 1 };
    store.nextRevision = 2;

    const startPromise = store.start('btc_ma');
    result.resolve({ status: 'started', strategy: 'btc_ma' });
    await startPromise;

    expect(store.errors).toEqual({});
  });

  it('keeps config warning when initial definitions and statuses fail before config succeeds', async () => {
    mockedService.listStrategyTypes.mockRejectedValueOnce(new Error('definitions failed'));
    mockedService.listStrategyConfigs.mockRejectedValueOnce(new Error('config failed'));
    mockedService.listStrategies.mockRejectedValueOnce(new Error('status failed'));
    const store = useStrategiesStore();
    store.configReconciliationError = 'existing config warning';
    store.statusReconciliationError = 'existing status warning';
    store.syncReconciliationError();

    await store.loadInitialData();

    expect(store.configReconciliationError).toBe('existing config warning');
    expect(store.statusReconciliationError).toBe('existing status warning');
    expect(store.reconciliationError).toBe('existing config warning');
    expect(store.error).toBe('definitions failed');
  });

  it('clears only successful initial resource warnings', async () => {
    mockedService.listStrategyTypes.mockRejectedValueOnce(new Error('definitions failed'));
    mockedService.listStrategyConfigs.mockResolvedValueOnce([btcConfig]);
    mockedService.listStrategies.mockRejectedValueOnce(new Error('status failed'));
    const store = useStrategiesStore();
    store.configReconciliationError = 'existing config warning';
    store.statusReconciliationError = 'existing status warning';
    store.syncReconciliationError();

    await store.loadInitialData();

    expect(store.configReconciliationError).toBeNull();
    expect(store.statusReconciliationError).toBe('existing status warning');
    expect(store.reconciliationError).toBe('existing status warning');
    expect(store.configs).toEqual([btcConfig]);
  });

  it('clears only successful initial status warning', async () => {
    mockedService.listStrategyTypes.mockRejectedValueOnce(new Error('definitions failed'));
    mockedService.listStrategyConfigs.mockRejectedValueOnce(new Error('config failed'));
    mockedService.listStrategies.mockResolvedValueOnce([stoppedBtc]);
    const store = useStrategiesStore();
    store.configReconciliationError = 'existing config warning';
    store.statusReconciliationError = 'existing status warning';
    store.syncReconciliationError();

    await store.loadInitialData();

    expect(store.configReconciliationError).toBe('existing config warning');
    expect(store.statusReconciliationError).toBeNull();
    expect(store.reconciliationError).toBe('existing config warning');
    expect(store.statuses).toEqual({ btc_ma: stoppedBtc });
  });

  it('does not let stale initial completion clear newer reconciliation warnings', async () => {
    const definitions = deferred<StrategyDefinition[]>();
    const configs = deferred<StrategyConfig[]>();
    const statuses = deferred<StrategyRuntimeSummary[]>();
    mockedService.listStrategyTypes.mockReturnValueOnce(definitions.promise);
    mockedService.listStrategyConfigs.mockReturnValueOnce(configs.promise);
    mockedService.listStrategies.mockReturnValueOnce(statuses.promise);

    const store = useStrategiesStore();
    const loadPromise = store.loadInitialData();
    store.configReconciliationError = 'new config warning';
    store.statusReconciliationError = 'new status warning';
    store.configRequestSeq += 1;
    store.statusRequestSeq += 1;
    store.errorRequestSeq += 1;
    store.syncReconciliationError();

    definitions.resolve([definition]);
    configs.resolve([btcConfig]);
    statuses.resolve([stoppedBtc]);
    await loadPromise;

    expect(store.configReconciliationError).toBe('new config warning');
    expect(store.statusReconciliationError).toBe('new status warning');
    expect(store.reconciliationError).toBe('new config warning');
  });

  it('orders strategy status events by server timestamp', () => {
    const store = useStrategiesStore();

    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', timestamp: 200 });
    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'stopped', timestamp: 100 });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
  });

  it('keeps exact status replays idempotent and reconciles equal-authority conflicts', async () => {
    const store = useStrategiesStore();
    const reconcile = vi.spyOn(store, 'refreshStatusesForReconciliation').mockResolvedValue();

    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', timestamp: 200 });
    const revision = store.statusRevisions.btc_ma;
    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', timestamp: 200 });
    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'stopped', timestamp: 200 });
    await Promise.resolve();

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
    expect(store.statusRevisions.btc_ma).toBe(revision);
    expect(reconcile).toHaveBeenCalledTimes(1);
  });

  it('uses shared received_at to order timestamp-free status events', () => {
    const store = useStrategiesStore();

    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', received_at: 200 });
    store.applyWebSocketMessage({ type: 'strategy_status', strategy: 'btc_ma', status: 'stopped', received_at: 100 });

    expect(store.statuses).toEqual({ btc_ma: { name: 'btc_ma', status: 'running' } });
  });

  it('uses authoritative empty error snapshot received_at as a barrier to delayed errors', async () => {
    const store = useStrategiesStore();
    const reconcile = vi.spyOn(store, 'refreshStatusesForReconciliation').mockResolvedValue();

    store.applyWebSocketMessage({ type: 'snapshot', received_at: 200, data: { strategy_errors: {} } });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'stale boom', timestamp: 100 });
    await Promise.resolve();

    expect(store.errors).toEqual({});
    expect(reconcile).toHaveBeenCalledTimes(1);
  });

  it('does not let delayed timestamped error delivery cross a newer empty error snapshot', async () => {
    const store = useStrategiesStore();
    const reconcile = vi.spyOn(store, 'refreshStatusesForReconciliation').mockResolvedValue();

    store.applyWebSocketMessage({ type: 'snapshot', received_at: 200, data: { strategy_errors: {} } });
    store.applyWebSocketMessage({
      type: 'strategy_error',
      strategy: 'btc_ma',
      error: 'stale boom',
      timestamp: 100,
      received_at: 300,
    });
    await Promise.resolve();

    expect(store.errors).toEqual({});
    expect(reconcile).toHaveBeenCalledTimes(1);
  });

  it('does not let delayed timestamped status delivery cross a newer empty status snapshot', async () => {
    const store = useStrategiesStore();
    const reconcile = vi.spyOn(store, 'refreshStatusesForReconciliation').mockResolvedValue();

    store.applyWebSocketMessage({ type: 'snapshot', received_at: 200, data: { strategies: [] } });
    store.applyWebSocketMessage({
      type: 'strategy_status',
      strategy: 'btc_ma',
      status: 'running',
      timestamp: 100,
      received_at: 300,
    });
    await Promise.resolve();

    expect(store.statuses).toEqual({});
    expect(reconcile).toHaveBeenCalledTimes(1);
  });

  it('orders timestamp-free errors against authoritative snapshots by shared received_at', () => {
    const store = useStrategiesStore();

    store.applyWebSocketMessage({ type: 'snapshot', received_at: 200, data: { strategy_errors: {} } });
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'stale boom', received_at: 100 });
    expect(store.errors).toEqual({});

    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'current boom', received_at: 300 });
    expect(store.errors).toEqual({ btc_ma: 'current boom' });
  });

  it('does not record an update failure after a newer config websocket event invalidates the request', async () => {
    const result = deferred<StrategyConfig>();
    mockedService.updateStrategyConfig.mockReturnValueOnce(result.promise);
    const store = useStrategiesStore();
    store.configs = [btcConfig];

    const promise = store.updateConfig('btc_ma', { ...btcConfig, timeframe: '5m' });
    store.applyWebSocketMessage({
      type: 'strategy_config_updated',
      config: { ...btcConfig, timeframe: '15m', updated_at: 1700000001000 },
    });
    result.reject(new Error('update failed'));

    await expect(promise).rejects.toThrow('update failed');
    expect(store.mutationError('btc_ma', 'update')).toBeNull();
  });

  it.each(['create', 'clone'] as const)('does not record a %s failure after authoritative invalidation', async (operation) => {
    const target = `${operation}_target`;
    const result = deferred<StrategyConfig>();
    const store = useStrategiesStore();
    const promise = operation === 'create'
      ? (mockedService.createStrategyConfig.mockReturnValueOnce(result.promise), store.createConfig({ ...btcConfig, name: target }))
      : (mockedService.cloneStrategyConfig.mockReturnValueOnce(result.promise), store.cloneConfig('btc_ma', { target_name: target }));

    if (operation === 'create') {
      store.applySnapshot({ strategy_configs: [] });
    } else {
      store.applyWebSocketMessage({ type: 'strategy_config_deleted', strategy: target });
    }
    result.reject(new Error(`${operation} failed`));

    await expect(promise).rejects.toThrow(`${operation} failed`);
    expect(store.mutationError(target, operation)).toBeNull();
  });

  it.each([
    {
      action: 'start',
      invalidation: 'status event',
      initialStatus: 'stopped',
      run: (store: ReturnType<typeof useStrategiesStore>) => store.start('btc_ma'),
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.startStrategy.mockReturnValueOnce(promise),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'strategy_status',
        strategy: 'btc_ma',
        status: 'stopped',
        timestamp: 1700000001000,
      }),
    },
    {
      action: 'stop',
      invalidation: 'status snapshot',
      initialStatus: 'running',
      run: (store: ReturnType<typeof useStrategiesStore>) => store.stop('btc_ma'),
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.stopStrategy.mockReturnValueOnce(promise),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applySnapshot({
        strategies: [{ name: 'btc_ma', status: 'stopped' }],
      }),
    },
    {
      action: 'start',
      invalidation: 'config event',
      initialStatus: 'stopped',
      run: (store: ReturnType<typeof useStrategiesStore>) => store.start('btc_ma'),
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.startStrategy.mockReturnValueOnce(promise),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'strategy_config_updated',
        config: { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 },
      }),
    },
    {
      action: 'stop',
      invalidation: 'config snapshot',
      initialStatus: 'running',
      run: (store: ReturnType<typeof useStrategiesStore>) => store.stop('btc_ma'),
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.stopStrategy.mockReturnValueOnce(promise),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applySnapshot({
        strategy_configs: [{ ...btcConfig, timeframe: '15m', updated_at: 1700000002000 }],
      }),
    },
  ] as const)('does not record a stale $action failure after newer $invalidation invalidation', async ({ action, initialStatus, run, arrangeService, invalidate }) => {
    const result = deferred<{ status: string; strategy: string }>();
    arrangeService(result.promise);
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: initialStatus } };

    const promise = run(store);
    invalidate(store);
    result.reject(new Error(`${action} failed`));

    await expect(promise).rejects.toThrow(`${action} failed`);
    expect(store.mutationError('btc_ma', action)).toBeNull();
  });

  it('does not record a stale stop failure after newer strategy error invalidation', async () => {
    const result = deferred<{ status: string; strategy: string }>();
    mockedService.stopStrategy.mockReturnValueOnce(result.promise);
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' } };

    const promise = store.stop('btc_ma');
    store.applyWebSocketMessage({ type: 'strategy_error', strategy: 'btc_ma', error: 'new boom' });
    result.reject(new Error('stop failed'));

    await expect(promise).rejects.toThrow('stop failed');
    expect(store.mutationError('btc_ma', 'stop')).toBeNull();
  });

  it.each([
    {
      action: 'start',
      invalidation: 'unchanged-target config snapshot',
      initialStatus: 'stopped',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.startStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.start('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applySnapshot({ strategy_configs: [btcConfig] }),
    },
    {
      action: 'stop',
      invalidation: 'unchanged-target config snapshot',
      initialStatus: 'running',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.stopStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.stop('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applySnapshot({ strategy_configs: [btcConfig] }),
    },
    {
      action: 'start',
      invalidation: 'same-value status snapshot authority',
      initialStatus: 'stopped',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.startStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.start('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'snapshot',
        received_at: 1700000001000,
        data: { strategies: [{ name: 'btc_ma', status: 'stopped' }] },
      }),
    },
    {
      action: 'stop',
      invalidation: 'same-value status snapshot authority',
      initialStatus: 'running',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.stopStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.stop('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'snapshot',
        received_at: 1700000001000,
        data: { strategies: [{ name: 'btc_ma', status: 'running' }] },
      }),
    },
    {
      action: 'start',
      invalidation: 'empty status snapshot authority',
      initialStatus: 'stopped',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.startStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.start('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'snapshot',
        received_at: 1700000001000,
        data: { strategies: [] },
      }),
    },
    {
      action: 'stop',
      invalidation: 'empty status snapshot authority',
      initialStatus: 'running',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.stopStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.stop('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'snapshot',
        received_at: 1700000001000,
        data: { strategies: [] },
      }),
    },
    {
      action: 'start',
      invalidation: 'same-value error snapshot authority',
      initialStatus: 'stopped',
      initialError: 'boom',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.startStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.start('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'snapshot',
        received_at: 1700000001000,
        data: { strategy_errors: { btc_ma: 'boom' } },
      }),
    },
    {
      action: 'stop',
      invalidation: 'same-value error snapshot authority',
      initialStatus: 'running',
      initialError: 'boom',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.stopStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.stop('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'snapshot',
        received_at: 1700000001000,
        data: { strategy_errors: { btc_ma: 'boom' } },
      }),
    },
    {
      action: 'start',
      invalidation: 'empty error snapshot authority',
      initialStatus: 'stopped',
      initialError: 'boom',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.startStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.start('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'snapshot',
        received_at: 1700000001000,
        data: { strategy_errors: {} },
      }),
    },
    {
      action: 'stop',
      invalidation: 'empty error snapshot authority',
      initialStatus: 'running',
      initialError: 'boom',
      arrangeService: (promise: Promise<{ status: string; strategy: string }>) => mockedService.stopStrategy.mockReturnValueOnce(promise),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.stop('btc_ma'),
      invalidate: (store: ReturnType<typeof useStrategiesStore>) => store.applyWebSocketMessage({
        type: 'snapshot',
        received_at: 1700000001000,
        data: { strategy_errors: {} },
      }),
    },
  ] as const)('does not record a stale $action failure after $invalidation invalidation', async ({ action, initialStatus, initialError, arrangeService, run, invalidate }) => {
    const result = deferred<{ status: string; strategy: string }>();
    arrangeService(result.promise);
    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: initialStatus } };
    if (initialError) {
      store.errors = { btc_ma: initialError };
    }

    const promise = run(store);
    invalidate(store);
    result.reject(new Error(`${action} failed`));

    await expect(promise).rejects.toThrow(`${action} failed`);
    expect(store.mutationError('btc_ma', action)).toBeNull();
  });

  it('preserves local config and reconciles once for equal-updated_at different websocket config', async () => {
    const store = useStrategiesStore();
    const reconcile = vi.spyOn(store, 'refreshConfigsForReconciliation').mockResolvedValue();
    store.configs = [btcConfig];

    store.applyWebSocketMessage({
      type: 'strategy_config_updated',
      strategy: 'btc_ma',
      config: { ...btcConfig, timeframe: '5m', updated_at: btcConfig.updated_at },
    });
    await Promise.resolve();

    expect(store.configs).toEqual([btcConfig]);
    expect(reconcile).toHaveBeenCalledTimes(1);
  });

  it('treats exact equal-updated_at websocket config replay idempotently without reconciliation', async () => {
    const store = useStrategiesStore();
    const reconcile = vi.spyOn(store, 'refreshConfigsForReconciliation').mockResolvedValue();
    store.configs = [btcConfig];

    store.applyWebSocketMessage({ type: 'strategy_config_updated', strategy: 'btc_ma', config: { ...btcConfig } });
    await Promise.resolve();

    expect(store.configs).toEqual([btcConfig]);
    expect(reconcile).not.toHaveBeenCalled();
  });

  it.each([
    {
      action: 'create',
      name: 'created:ma',
      fail: () => mockedService.createStrategyConfig.mockRejectedValueOnce(new Error('create failed')),
      succeed: () => mockedService.createStrategyConfig.mockResolvedValueOnce({ ...btcConfig, name: 'created:ma' }),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.createConfig({ ...btcConfig, name: 'created:ma' }),
    },
    {
      action: 'update',
      name: 'btc:ma',
      fail: () => mockedService.updateStrategyConfig.mockRejectedValueOnce(new Error('update failed')),
      succeed: () => mockedService.updateStrategyConfig.mockResolvedValueOnce({ ...btcConfig, name: 'btc:ma' }),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.updateConfig('btc:ma', { ...btcConfig, name: 'btc:ma' }),
    },
    {
      action: 'clone',
      name: 'cloned:ma',
      fail: () => mockedService.cloneStrategyConfig.mockRejectedValueOnce(new Error('clone failed')),
      succeed: () => mockedService.cloneStrategyConfig.mockResolvedValueOnce({ ...btcConfig, name: 'cloned:ma' }),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.cloneConfig('btc:ma', { target_name: 'cloned:ma' }),
    },
    {
      action: 'delete',
      name: 'btc:ma',
      fail: () => mockedService.deleteStrategyConfig.mockRejectedValueOnce(new Error('delete failed')),
      succeed: () => mockedService.deleteStrategyConfig.mockResolvedValueOnce(),
      run: (store: ReturnType<typeof useStrategiesStore>) => store.deleteConfig('btc:ma'),
    },
  ] as const)('records failed $action canonically until the relevant retry succeeds', async ({ action, name, fail, succeed, run }) => {
    mockedService.listStrategyConfigs.mockResolvedValue([]);
    const store = useStrategiesStore();
    fail();

    await expect(run(store)).rejects.toThrow(`${action} failed`);
    expect(store.mutationError(name, action)).toBe(`${action} failed`);

    succeed();
    await run(store);
    expect(store.mutationError(name, action)).toBeNull();
  });

  it('records lifecycle failures independently and clears only the retried action', async () => {
    mockedService.startStrategy.mockRejectedValueOnce(new Error('start failed'));
    mockedService.stopStrategy.mockRejectedValueOnce(new Error('stop failed'));
    mockedService.listStrategies.mockResolvedValue([]);
    const store = useStrategiesStore();

    await expect(store.start('btc:ma')).rejects.toThrow('start failed');
    await expect(store.stop('btc:ma')).rejects.toThrow('stop failed');
    expect(store.mutationError('btc:ma', 'start')).toBe('start failed');
    expect(store.mutationError('btc:ma', 'stop')).toBe('stop failed');

    mockedService.startStrategy.mockResolvedValueOnce({ status: 'started', strategy: 'btc:ma' });
    await store.start('btc:ma');

    expect(store.mutationError('btc:ma', 'start')).toBeNull();
    expect(store.mutationError('btc:ma', 'stop')).toBe('stop failed');
  });

  it('ignores an older update success when a newer update for the same target is active', async () => {
    const older = deferred<StrategyConfig>();
    const newer = deferred<StrategyConfig>();
    const olderSaved = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    const newerSaved = { ...btcConfig, timeframe: '15m', updated_at: 1700000002000 };
    mockedService.updateStrategyConfig
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    const reconcile = vi.spyOn(store, 'refreshConfigsForReconciliation').mockResolvedValue();
    const olderPromise = store.updateConfig('btc_ma', olderSaved);
    const newerPromise = store.updateConfig('btc_ma', newerSaved);

    older.resolve(olderSaved);
    await expect(olderPromise).resolves.toEqual(olderSaved);

    expect(store.configs).toEqual([btcConfig]);
    expect(reconcile).not.toHaveBeenCalled();

    newer.resolve(newerSaved);
    await expect(newerPromise).resolves.toEqual(newerSaved);

    expect(store.configs).toEqual([newerSaved]);
    expect(reconcile).toHaveBeenCalledTimes(1);
  });

  it('orders create and clone successes by their shared target operation sequence', async () => {
    const createResult = deferred<StrategyConfig>();
    const cloneResult = deferred<StrategyConfig>();
    const created = { ...btcConfig, name: 'collision_ma', timeframe: '5m' };
    const cloned = { ...btcConfig, name: 'collision_ma', timeframe: '15m' };
    mockedService.createStrategyConfig.mockReturnValueOnce(createResult.promise);
    mockedService.cloneStrategyConfig.mockReturnValueOnce(cloneResult.promise);

    const store = useStrategiesStore();
    const reconcile = vi.spyOn(store, 'refreshConfigsForReconciliation').mockResolvedValue();
    const createPromise = store.createConfig(created);
    const clonePromise = store.cloneConfig('btc_ma', { target_name: 'collision_ma' });

    createResult.resolve(created);
    await expect(createPromise).resolves.toEqual(created);

    expect(store.configs).toEqual([]);
    expect(store.statuses).toEqual({});
    expect(reconcile).not.toHaveBeenCalled();

    cloneResult.resolve(cloned);
    await expect(clonePromise).resolves.toEqual(cloned);

    expect(store.configs).toEqual([cloned]);
    expect(store.statuses).toEqual({ collision_ma: { name: 'collision_ma', status: 'stopped' } });
    expect(reconcile).toHaveBeenCalledTimes(1);
  });

  it('does not let an older delete success disturb a newer update for the same target', async () => {
    const deleteResult = deferred<void>();
    const updateResult = deferred<StrategyConfig>();
    const updated = { ...btcConfig, timeframe: '5m', updated_at: 1700000001000 };
    mockedService.deleteStrategyConfig.mockReturnValueOnce(deleteResult.promise);
    mockedService.updateStrategyConfig.mockReturnValueOnce(updateResult.promise);

    const store = useStrategiesStore();
    store.configs = [btcConfig];
    store.statuses = { btc_ma: stoppedBtc };
    store.mutationErrors = { '["btc_ma","update"]': 'newer operation marker' };
    const reconcile = vi.spyOn(store, 'refreshConfigsForReconciliation').mockResolvedValue();
    const deletePromise = store.deleteConfig('btc_ma');
    const updatePromise = store.updateConfig('btc_ma', updated);
    store.mutationErrors = { '["btc_ma","update"]': 'newer operation marker' };

    deleteResult.resolve();
    await expect(deletePromise).resolves.toBeUndefined();

    expect(store.configs).toEqual([btcConfig]);
    expect(store.statuses).toEqual({ btc_ma: stoppedBtc });
    expect(store.mutationError('btc_ma', 'update')).toBe('newer operation marker');
    expect(reconcile).not.toHaveBeenCalled();

    updateResult.resolve(updated);
    await expect(updatePromise).resolves.toEqual(updated);

    expect(store.configs).toEqual([updated]);
    expect(reconcile).toHaveBeenCalledTimes(1);
  });

  it('does not record an older cross-action failure after a newer target operation starts', async () => {
    const createResult = deferred<StrategyConfig>();
    const cloneResult = deferred<StrategyConfig>();
    mockedService.createStrategyConfig.mockReturnValueOnce(createResult.promise);
    mockedService.cloneStrategyConfig.mockReturnValueOnce(cloneResult.promise);

    const store = useStrategiesStore();
    const createPromise = store.createConfig({ ...btcConfig, name: 'collision_ma' });
    const clonePromise = store.cloneConfig('btc_ma', { target_name: 'collision_ma' });

    cloneResult.reject(new Error('current clone failed'));
    await expect(clonePromise).rejects.toThrow('current clone failed');
    createResult.reject(new Error('stale create failed'));
    await expect(createPromise).rejects.toThrow('stale create failed');

    expect(store.mutationError('collision_ma', 'clone')).toBe('current clone failed');
    expect(store.mutationError('collision_ma', 'create')).toBeNull();
  });

  it('does not let stale concurrent or pre-reset failures overwrite current mutation errors', async () => {
    const stale = deferred<StrategyConfig>();
    const current = deferred<StrategyConfig>();
    mockedService.updateStrategyConfig
      .mockReturnValueOnce(stale.promise)
      .mockReturnValueOnce(current.promise);
    const store = useStrategiesStore();

    const stalePromise = store.updateConfig('btc:ma', { ...btcConfig, name: 'btc:ma' });
    const currentPromise = store.updateConfig('btc:ma', { ...btcConfig, name: 'btc:ma' });
    current.reject(new Error('current failed'));
    await expect(currentPromise).rejects.toThrow('current failed');
    stale.reject(new Error('stale failed'));
    await expect(stalePromise).rejects.toThrow('stale failed');
    expect(store.mutationError('btc:ma', 'update')).toBe('current failed');

    const beforeReset = deferred<StrategyConfig>();
    mockedService.updateStrategyConfig.mockReturnValueOnce(beforeReset.promise);
    const beforeResetPromise = store.updateConfig('btc:ma', { ...btcConfig, name: 'btc:ma' });
    store.reset();
    beforeReset.reject(new Error('before reset failed'));
    await expect(beforeResetPromise).rejects.toThrow('before reset failed');
    expect(store.mutationErrors).toEqual({});
  });

  it('resets all strategy-owned state', () => {
    const store = useStrategiesStore();
    store.definitions = [definition];
    store.configs = [btcConfig];
    store.statuses = { btc_ma: { name: 'btc_ma', status: 'running' } };
    store.errors = { btc_ma: 'boom' };
    store.loadingInitial = true;
    store.actionLoading = { 'btc_ma:start': true };
    store.error = 'failed';
    store.reconciliationError = 'stale';
    store.configReconciliationError = 'config stale';
    store.statusReconciliationError = 'status stale';
    store.configRevisions = { btc_ma: 1 };
    store.configTombstones = { eth_ma: 1 };
    store.configAuthorities = { btc_ma: 1700000000000 };
    store.configDeletionAuthorities = { eth_ma: { deletedUpdatedAt: 1700000000000, deleteTimestamp: 1700000001000 } };
    store.runtimeBarriers = { eth_ma: 1700000001000 };
    store.statusRevisions = { btc_ma: 2 };
    store.errorRevisions = { btc_ma: 3 };
    store.mutationErrors = { '["btc_ma","start"]': 'failed start' };
    store.mutationLoading = { '["btc_ma","start"]': true };
    store.mutationRequestSeq = { '["btc_ma","start"]': 1 };
    store.targetCrudRequestSeq = { btc_ma: 2 };
    store.statusAuthorities = { btc_ma: 1700000000000 };
    store.statusSnapshotAuthority = { receivedAt: 1700000000500 };
    store.errorAuthorities = { btc_ma: 1700000001000 };
    store.errorSnapshotAuthority = { receivedAt: 1700000001500 };
    store.nextRevision = 4;
    store.generation = 7;
    store.initialRequestSeq = 8;
    store.configRequestSeq = 9;
    store.statusRequestSeq = 10;
    store.errorRequestSeq = 11;
    store.configSnapshotEpoch = 12;
    store.actionRequestSeq = { 'btc_ma:start': 13 };
    store.lifecycleRequestSeq = { btc_ma: 14 };

    store.reset();

    expect(store.$state).toEqual({
      definitions: [],
      configs: [],
      statuses: {},
      errors: {},
      loadingInitial: false,
      actionLoading: {},
      error: null,
      reconciliationError: null,
      configReconciliationError: null,
      statusReconciliationError: null,
      configRevisions: {},
      configTombstones: {},
      configAuthorities: {},
      configDeletionAuthorities: {},
      runtimeBarriers: {},
      statusRevisions: {},
      errorRevisions: {},
      mutationErrors: {},
      mutationLoading: {},
      mutationRequestSeq: {},
      targetCrudRequestSeq: {},
      statusAuthorities: {},
      statusSnapshotAuthority: undefined,
      errorAuthorities: {},
      errorSnapshotAuthority: undefined,
      nextRevision: 1,
      generation: 8,
      initialRequestSeq: 0,
      configRequestSeq: 0,
      statusRequestSeq: 0,
      errorRequestSeq: 0,
      configSnapshotEpoch: 0,
      actionRequestSeq: {},
      lifecycleRequestSeq: {},
    });
  });
});
