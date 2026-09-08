import { defineComponent, h } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount } from '@/test-utils/mount';
import type { BacktestMetrics, BacktestResult, BacktestResultDetail } from '@/types/backtest';
import type { StrategyConfig, StrategyDefinition, StrategyRuntimeSummary } from '@/types/strategy';

const services = vi.hoisted(() => ({
  runBacktest: vi.fn(),
  fetchBacktestResults: vi.fn(),
  fetchBacktestResultDetail: vi.fn(),
  listStrategies: vi.fn(),
  listStrategyTypes: vi.fn(),
  listStrategyConfigs: vi.fn(),
  messageError: vi.fn(),
  messageSuccess: vi.fn(),
}));

vi.mock('element-plus', async (importOriginal) => ({
  ...await importOriginal<typeof import('element-plus')>(),
  ElMessage: {
    error: services.messageError,
    success: services.messageSuccess,
  },
}));

vi.mock('@/services/backtest', () => ({
  runBacktest: services.runBacktest,
  fetchBacktestResults: services.fetchBacktestResults,
  fetchBacktestResultDetail: services.fetchBacktestResultDetail,
}));

vi.mock('@/services/strategies', () => ({
  listStrategies: services.listStrategies,
  listStrategyTypes: services.listStrategyTypes,
  listStrategyConfigs: services.listStrategyConfigs,
}));

vi.mock('@/components/backtest/BacktestForm.vue', () => ({
  default: defineComponent({
    name: 'BacktestForm',
    props: ['form', 'strategyOptions', 'strategyConflictMessage', 'strategyCatalogUnavailable', 'symbolOptions', 'timeframeOptions', 'strategiesLoading', 'running', 'validationError'],
    emits: ['run', 'retry-strategies'],
    setup(props, { emit, attrs }) {
      return () => h('backtest-form-stub', {
        ...attrs,
        form: props.form,
        'data-running': props.running,
        'data-strategy-options': (props.strategyOptions as unknown[]).length,
        'data-strategy-conflict-message': props.strategyConflictMessage ?? '',
        'data-strategy-catalog-unavailable': Boolean(props.strategyCatalogUnavailable),
        strategyOptions: props.strategyOptions,
        'data-symbol-options': (props.symbolOptions as unknown[]).length,
        'data-timeframe-options': (props.timeframeOptions as unknown[]).length,
        'data-strategies-loading': props.strategiesLoading,
        'data-validation-error': props.validationError ?? '',
        onRun: () => emit('run'),
        onRetryStrategies: () => emit('retry-strategies'),
      });
    },
  }),
}));

vi.mock('@/components/backtest/BacktestMetrics.vue', () => ({
  default: defineComponent({
    name: 'BacktestMetrics',
    props: ['metrics', 'loading'],
    setup(props) {
      return () => h('backtest-metrics-stub', {
        'data-loading': props.loading,
        'data-has-metrics': Boolean(props.metrics),
      });
    },
  }),
}));

vi.mock('@/components/backtest/BacktestResultsTable.vue', () => ({
  default: defineComponent({
    name: 'BacktestResultsTable',
    props: ['results', 'selectedResultId', 'loading'],
    emits: ['select-result', 'refresh'],
    setup(props, { emit }) {
      return () => h('backtest-results-table-stub', {
        'data-results': (props.results as unknown[]).length,
        'data-selected-result-id': props.selectedResultId ?? '',
        'data-loading': props.loading,
        onSelectResult: (resultId: string) => emit('select-result', resultId),
        onRefresh: () => emit('refresh'),
      });
    },
  }),
}));

vi.mock('@/components/backtest/BacktestResultDetail.vue', () => ({
  default: defineComponent({
    name: 'BacktestResultDetail',
    props: ['selectedDetail', 'selectedResultId', 'loading', 'error'],
    emits: ['retry'],
    setup(props, { emit }) {
      return () => h('backtest-result-detail-stub', {
        'data-loading': props.loading,
        'data-error': props.error ?? '',
        'data-selected-result-id': props.selectedResultId ?? '',
        'data-detail-result-id': props.selectedDetail?.result.id ?? '',
        'data-has-detail': Boolean(props.selectedDetail),
        onRetry: () => emit('retry'),
      });
    },
  }),
}));

const Backtest = (await import('./Backtest.vue')).default;

function createResult(id: string, createdAt: number): BacktestResult {
  return {
    id,
    strategy: 'ma_cross',
    symbol: 'BTC-USDT',
    timeframe: '1h',
    start_time: new Date('2026-01-01T00:00:00Z').getTime(),
    end_time: new Date('2026-01-02T00:00:00Z').getTime(),
    initial_capital: 100000,
    total_return: 0.1,
    sharpe_ratio: 1.1,
    max_drawdown: 0.02,
    win_rate: 0.6,
    total_trades: 10,
    created_at: createdAt,
  };
}

function createDetail(result: BacktestResult): BacktestResultDetail {
  return {
    result,
    klines: [{ symbol: result.symbol, timeframe: result.timeframe, timestamp: result.start_time, open: 1, high: 2, low: 0.5, close: 1.5, volume: 100 }],
    markers: [],
  };
}

function strategyOption(
  value: string,
  label = value,
  disabled: boolean | undefined = undefined,
  backendValue = value,
  id = value,
) {
  return {
    id,
    value,
    backendValue,
    label,
    ...(disabled === undefined ? {} : { disabled }),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('Backtest view', () => {
  beforeEach(() => {
    services.runBacktest.mockReset();
    services.fetchBacktestResults.mockReset();
    services.fetchBacktestResultDetail.mockReset();
    services.listStrategies.mockReset();
    services.listStrategyTypes.mockReset();
    services.listStrategyConfigs.mockReset();
    services.messageError.mockReset();
    services.messageSuccess.mockReset();
    services.listStrategyTypes.mockResolvedValue([
      { strategy_type: 'ma_cross', label: 'Moving Average Cross', description: 'desc', params: [] },
      { strategy_type: 'donchian_breakout', label: 'Donchian Breakout', description: 'desc', params: [] },
      { strategy_type: 'bollinger_mean_reversion', label: 'Bollinger Mean Reversion', description: 'desc', params: [] },
      { strategy_type: 'rsi_mean_reversion', label: 'RSI Mean Reversion', description: 'desc', params: [] },
    ]);
    services.listStrategyConfigs.mockResolvedValue([
      {
        name: 'saved_bollinger',
        strategy_type: 'bollinger_mean_reversion',
        symbol: 'ETH-USDT',
        timeframe: '4h',
        enabled: false,
        params: {},
        created_at: new Date('2026-01-01T00:00:00Z').getTime(),
        updated_at: new Date('2026-01-02T00:00:00Z').getTime(),
      },
    ]);
    services.listStrategies.mockResolvedValue([
      { name: 'saved_bollinger', status: 'stopped' },
      { name: 'rsi_mean_reversion', status: 'running' },
    ]);
    services.fetchBacktestResults.mockResolvedValue([createResult('result-a', new Date('2026-01-03T00:00:00Z').getTime())]);
  });

  it('keeps built-ins, saved configs, and runtime status labels distinct', async () => {
    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();

    const form = wrapper.find((node) => node.type === 'backtest-form-stub');
    expect(form.props['data-strategy-options']).toBe(5);
    expect(form.props['data-strategy-conflict-message']).toBe('');
    expect(form.props.strategyOptions).toEqual([
      strategyOption('ma_cross', 'ma_cross · Built-in strategy', undefined, 'ma_cross', 'builtin:ma_cross'),
      strategyOption('donchian_breakout', 'donchian_breakout · Built-in strategy', undefined, 'donchian_breakout', 'builtin:donchian_breakout'),
      strategyOption('bollinger_mean_reversion', 'bollinger_mean_reversion · Built-in strategy', undefined, 'bollinger_mean_reversion', 'builtin:bollinger_mean_reversion'),
      strategyOption('rsi_mean_reversion', 'rsi_mean_reversion · Built-in strategy · Running', undefined, 'rsi_mean_reversion', 'builtin:rsi_mean_reversion'),
      strategyOption('saved_bollinger', 'saved_bollinger · Saved config · Stopped', false, 'saved_bollinger', 'config:saved_bollinger'),
    ]);
  });

  it('disables colliding saved configs and explains the backtest resolution rule', async () => {
    services.listStrategyConfigs.mockResolvedValueOnce([
      {
        name: 'ma_cross',
        strategy_type: 'bollinger_mean_reversion',
        symbol: 'ETH-USDT',
        timeframe: '4h',
        enabled: false,
        params: {},
        created_at: new Date('2026-01-01T00:00:00Z').getTime(),
        updated_at: new Date('2026-01-02T00:00:00Z').getTime(),
      },
    ]);
    services.listStrategies.mockResolvedValueOnce([
      { name: 'ma_cross', status: 'stopped' },
    ]);

    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();

    const form = wrapper.find((node) => node.type === 'backtest-form-stub');
    expect(form.props['data-strategy-options']).toBe(5);
    expect(form.props['data-strategy-conflict-message']).toContain('ma_cross');
    expect(form.props['data-strategy-conflict-message']).toContain('backtest API resolves plain names to built-ins first');
    expect(form.props.strategyOptions).toContainEqual(strategyOption('ma_cross', 'ma_cross · Built-in strategy · Stopped', undefined, 'ma_cross', 'builtin:ma_cross'));
    expect(form.props.strategyOptions).toContainEqual(strategyOption(
      'config:ma_cross',
      'ma_cross · Saved config · Stopped · Disabled',
      true,
      'ma_cross',
      'config:ma_cross',
    ));
    const savedConfigOption = (form.props.strategyOptions as ReturnType<typeof strategyOption>[])
      .find((option) => option.label.includes('Saved config'));
    expect(savedConfigOption?.disabled).toBe(true);
  });

  it('treats the strategy catalog as unavailable until retry restores it', async () => {
    const collidingConfig = {
      name: 'ma_cross',
      strategy_type: 'bollinger_mean_reversion',
      symbol: 'ETH-USDT',
      timeframe: '4h',
      enabled: false,
      params: {},
      created_at: new Date('2026-01-01T00:00:00Z').getTime(),
      updated_at: new Date('2026-01-02T00:00:00Z').getTime(),
    };
    const savedConfig = {
      name: 'saved_bollinger',
      strategy_type: 'bollinger_mean_reversion',
      symbol: 'ETH-USDT',
      timeframe: '4h',
      enabled: false,
      params: {},
      created_at: new Date('2026-01-01T12:00:00Z').getTime(),
      updated_at: new Date('2026-01-02T12:00:00Z').getTime(),
    };

    services.runBacktest.mockResolvedValue({
      total_return: 0.1,
      sharpe_ratio: 1.2,
      max_drawdown: 0.03,
      win_rate: 0.5,
      total_trades: 11,
    });
    services.listStrategyTypes.mockRejectedValueOnce(new Error('strategy types unavailable'));
    services.listStrategyConfigs.mockResolvedValueOnce([collidingConfig, savedConfig]);
    services.listStrategies.mockResolvedValueOnce([
      { name: 'ma_cross', status: 'stopped' },
      { name: 'saved_bollinger', status: 'running' },
    ]);

    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();

    const form = wrapper.find((node) => node.type === 'backtest-form-stub');
    const unavailableStrategyOptions = form.props.strategyOptions as Array<{ disabled?: boolean; id: string }>;
    expect(form.props['data-strategy-catalog-unavailable']).toBe(true);
    expect(form.props['data-strategy-options']).toBe(2);
    expect(form.props['data-strategy-conflict-message']).toBe('');
    expect(unavailableStrategyOptions.every((option) => option.disabled)).toBe(true);
    expect(unavailableStrategyOptions.map((option) => option.id)).toEqual([
      'config:ma_cross',
      'config:saved_bollinger',
    ]);

    await wrapper.invoke(form, 'onRun');
    expect(services.runBacktest).not.toHaveBeenCalled();
    expect(services.messageError).toHaveBeenCalledWith(
      'Strategy types could not be loaded. Saved configs are disabled until the catalog is available again. Retry loading strategies to restore them.',
    );

    services.listStrategyTypes.mockResolvedValueOnce([
      { strategy_type: 'ma_cross', label: 'Moving Average Cross', description: 'desc', params: [] },
      { strategy_type: 'donchian_breakout', label: 'Donchian Breakout', description: 'desc', params: [] },
      { strategy_type: 'bollinger_mean_reversion', label: 'Bollinger Mean Reversion', description: 'desc', params: [] },
      { strategy_type: 'rsi_mean_reversion', label: 'RSI Mean Reversion', description: 'desc', params: [] },
    ]);
    services.listStrategyConfigs.mockResolvedValueOnce([collidingConfig, savedConfig]);
    services.listStrategies.mockResolvedValueOnce([
      { name: 'ma_cross', status: 'stopped' },
      { name: 'saved_bollinger', status: 'running' },
    ]);

    await wrapper.invoke(form, 'onRetryStrategies');
    await wrapper.flush();

    const recoveredForm = wrapper.find((node) => node.type === 'backtest-form-stub');
    expect(recoveredForm.props['data-strategy-catalog-unavailable']).toBe(false);
    expect(recoveredForm.props['data-strategy-options']).toBe(6);
    expect(recoveredForm.props['data-strategy-conflict-message']).toContain('backtest API resolves plain names to built-ins first');
    expect(recoveredForm.props.strategyOptions).toContainEqual(strategyOption(
      'ma_cross',
      'ma_cross · Built-in strategy · Stopped',
      undefined,
      'ma_cross',
      'builtin:ma_cross',
    ));
    expect(recoveredForm.props.strategyOptions).toContainEqual(strategyOption(
      'saved_bollinger',
      'saved_bollinger · Saved config · Running',
      false,
      'saved_bollinger',
      'config:saved_bollinger',
    ));
    expect(recoveredForm.props.strategyOptions).toContainEqual(strategyOption(
      'config:ma_cross',
      'ma_cross · Saved config · Stopped · Disabled',
      true,
      'ma_cross',
      'config:ma_cross',
    ));

    (recoveredForm.props.form as { strategy: string }).strategy = 'saved_bollinger';
    await wrapper.flush();
    await wrapper.invoke(wrapper.find((node) => node.type === 'backtest-form-stub'), 'onRun');

    expect(services.runBacktest).toHaveBeenCalledTimes(1);
    expect(services.runBacktest).toHaveBeenCalledWith(expect.objectContaining({ strategy: 'saved_bollinger' }));
    expect(services.messageSuccess).toHaveBeenCalledWith('Backtest completed');

    (wrapper.find((node) => node.type === 'backtest-form-stub').props.form as { strategy: string }).strategy = 'unknown_strategy';
    await wrapper.flush();
    await wrapper.invoke(wrapper.find((node) => node.type === 'backtest-form-stub'), 'onRun');

    expect(services.runBacktest).toHaveBeenCalledTimes(1);
    expect(services.messageError).toHaveBeenCalledWith('Selected strategy is unavailable. Please choose another strategy.');
  });

  it('ignores stale catalog responses while a retry is loading', async () => {
    const staleTypes = deferred<StrategyDefinition[]>();
    const staleConfigs = deferred<StrategyConfig[]>();
    const staleStrategies = deferred<StrategyRuntimeSummary[]>();
    const retryTypes = deferred<StrategyDefinition[]>();
    const retryConfigs = deferred<StrategyConfig[]>();
    const retryStrategies = deferred<StrategyRuntimeSummary[]>();

    services.listStrategyTypes
      .mockImplementationOnce(() => staleTypes.promise)
      .mockImplementationOnce(() => retryTypes.promise);
    services.listStrategyConfigs
      .mockImplementationOnce(() => staleConfigs.promise)
      .mockImplementationOnce(() => retryConfigs.promise);
    services.listStrategies
      .mockImplementationOnce(() => staleStrategies.promise)
      .mockImplementationOnce(() => retryStrategies.promise);

    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();

    const form = wrapper.find((node) => node.type === 'backtest-form-stub');
    expect(form.props['data-strategies-loading']).toBe(true);
    expect(form.props['data-strategy-options']).toBe(0);

    void (form.props.onRetryStrategies as () => Promise<void>)();
    await wrapper.flush();
    expect(wrapper.find((node) => node.type === 'backtest-form-stub').props['data-strategies-loading']).toBe(true);

    staleTypes.resolve([
      { strategy_type: 'stale_only', label: 'Stale only', description: 'desc', params: [] },
    ]);
    staleConfigs.resolve([]);
    staleStrategies.resolve([]);
    await wrapper.flush();

    const staleForm = wrapper.find((node) => node.type === 'backtest-form-stub');
    expect(staleForm.props['data-strategies-loading']).toBe(true);
    expect(staleForm.props['data-strategy-options']).toBe(0);
    expect(services.messageError).not.toHaveBeenCalled();

    retryTypes.resolve([
      { strategy_type: 'ma_cross', label: 'Moving Average Cross', description: 'desc', params: [] },
      { strategy_type: 'donchian_breakout', label: 'Donchian Breakout', description: 'desc', params: [] },
      { strategy_type: 'bollinger_mean_reversion', label: 'Bollinger Mean Reversion', description: 'desc', params: [] },
      { strategy_type: 'rsi_mean_reversion', label: 'RSI Mean Reversion', description: 'desc', params: [] },
    ]);
    retryConfigs.resolve([
      {
        name: 'ma_cross',
        strategy_type: 'bollinger_mean_reversion',
        symbol: 'ETH-USDT',
        timeframe: '4h',
        enabled: false,
        params: {},
        created_at: new Date('2026-01-01T00:00:00Z').getTime(),
        updated_at: new Date('2026-01-02T00:00:00Z').getTime(),
      },
    ]);
    retryStrategies.resolve([
      { name: 'ma_cross', status: 'stopped' },
      { name: 'saved_bollinger', status: 'running' },
    ]);
    await wrapper.flush();

    const recoveredForm = wrapper.find((node) => node.type === 'backtest-form-stub');
    expect(recoveredForm.props['data-strategies-loading']).toBe(false);
    expect(recoveredForm.props['data-strategy-catalog-unavailable']).toBe(false);
    expect(recoveredForm.props['data-strategy-options']).toBe(5);
    expect(recoveredForm.props['data-strategy-conflict-message']).toContain('backtest API resolves plain names to built-ins first');
    expect(recoveredForm.props.strategyOptions).toContainEqual(strategyOption('ma_cross', 'ma_cross · Built-in strategy · Stopped', undefined, 'ma_cross', 'builtin:ma_cross'));
    expect(recoveredForm.props.strategyOptions).toContainEqual(strategyOption(
      'config:ma_cross',
      'ma_cross · Saved config · Stopped · Disabled',
      true,
      'ma_cross',
      'config:ma_cross',
    ));

    (recoveredForm.props.form as { strategy: string }).strategy = 'config:ma_cross';
    await wrapper.flush();
    await wrapper.invoke(wrapper.find((node) => node.type === 'backtest-form-stub'), 'onRun');

    expect(services.runBacktest).not.toHaveBeenCalled();
    expect(services.messageError).toHaveBeenCalledWith(
      'Saved configs with names that match built-in strategies are disabled because the backtest API resolves plain names to built-ins first: ma_cross.',
    );
  });

  it('blocks running a colliding saved config even when the option is forced', async () => {
    const collidingConfig = {
      name: 'ma_cross',
      strategy_type: 'bollinger_mean_reversion',
      symbol: 'ETH-USDT',
      timeframe: '4h',
      enabled: false,
      params: {},
      created_at: new Date('2026-01-01T00:00:00Z').getTime(),
      updated_at: new Date('2026-01-02T00:00:00Z').getTime(),
    };

    services.listStrategyConfigs.mockResolvedValueOnce([collidingConfig]);
    services.listStrategies.mockResolvedValueOnce([
      { name: 'ma_cross', status: 'stopped' },
    ]);

    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();

    const form = wrapper.find((node) => node.type === 'backtest-form-stub');
    (form.props.form as { strategy: string }).strategy = 'config:ma_cross';
    await wrapper.flush();

    const refreshedForm = wrapper.find((node) => node.type === 'backtest-form-stub');
    await wrapper.invoke(refreshedForm, 'onRun');

    expect(services.runBacktest).not.toHaveBeenCalled();
    expect(services.messageError).toHaveBeenCalledWith(
      'Saved configs with names that match built-in strategies are disabled because the backtest API resolves plain names to built-ins first: ma_cross.',
    );
  });

  it('keeps invalid date submissions from running the backtest', async () => {
    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();

    const form = wrapper.find((node) => node.type === 'backtest-form-stub');
    (form.props.form as { endTime: Date | null }).endTime = new Date('invalid');
    await wrapper.flush();

    const refreshedForm = wrapper.find((node) => node.type === 'backtest-form-stub');
    expect(refreshedForm.props['data-validation-error']).toBe('timeRequired');
    await wrapper.invoke(refreshedForm, 'onRun');

    expect(services.runBacktest).not.toHaveBeenCalled();
    expect(services.messageError).toHaveBeenCalledWith('Start time and end time are required');
  });

  it('keeps missing or invalid initial capital from running the backtest', async () => {
    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();

    const form = wrapper.find((node) => node.type === 'backtest-form-stub');
    (form.props.form as { initialCapital: number | null }).initialCapital = null;
    await wrapper.flush();

    const refreshedForm = wrapper.find((node) => node.type === 'backtest-form-stub');
    expect(refreshedForm.props['data-validation-error']).toBe('initialCapitalPositive');
    await wrapper.invoke(refreshedForm, 'onRun');

    expect(services.runBacktest).not.toHaveBeenCalled();
    expect(services.messageSuccess).not.toHaveBeenCalled();
    expect(services.messageError).toHaveBeenCalledWith('Initial capital must be greater than 0');
  });

  it('reloads history after a successful run', async () => {
    services.runBacktest.mockResolvedValue({
      total_return: 0.1,
      sharpe_ratio: 1.2,
      max_drawdown: 0.03,
      win_rate: 0.5,
      total_trades: 11,
    });
    services.fetchBacktestResults
      .mockResolvedValueOnce([createResult('result-a', new Date('2026-01-03T00:00:00Z').getTime())])
      .mockResolvedValueOnce([createResult('result-a', new Date('2026-01-03T00:00:00Z').getTime()), createResult('result-b', new Date('2026-01-04T00:00:00Z').getTime())]);

    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();
    const form = wrapper.find((node) => node.type === 'backtest-form-stub');
    await wrapper.invoke(form, 'onRun');

    expect(services.runBacktest).toHaveBeenCalledTimes(1);
    expect(services.fetchBacktestResults).toHaveBeenCalledTimes(2);
    expect(services.messageSuccess).toHaveBeenCalledWith('Backtest completed');
  });

  it('keeps the latest history request in control of loading state', async () => {
    const initialHistory = deferred<BacktestResult[]>();
    const refreshedHistory = deferred<BacktestResult[]>();
    services.fetchBacktestResults
      .mockImplementationOnce(() => initialHistory.promise)
      .mockImplementationOnce(() => refreshedHistory.promise);

    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();
    const table = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    expect(table.props['data-loading']).toBe(true);

    await wrapper.invoke(table, 'onRefresh');
    expect(wrapper.find((node) => node.type === 'backtest-results-table-stub').props['data-loading']).toBe(true);

    initialHistory.resolve([createResult('result-old', new Date('2026-01-02T00:00:00Z').getTime())]);
    await wrapper.flush();

    const loadingTable = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    expect(loadingTable.props['data-loading']).toBe(true);
    expect(loadingTable.props['data-results']).toBe(0);

    refreshedHistory.resolve([createResult('result-new', new Date('2026-01-03T00:00:00Z').getTime())]);
    await wrapper.flush();

    const settledTable = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    expect(settledTable.props['data-loading']).toBe(false);
    expect(settledTable.props['data-results']).toBe(1);
  });

  it('ignores stale history responses that resolve after a newer refresh', async () => {
    const initialHistory = deferred<BacktestResult[]>();
    const refreshedHistory = deferred<BacktestResult[]>();
    services.fetchBacktestResults
      .mockImplementationOnce(() => initialHistory.promise)
      .mockImplementationOnce(() => refreshedHistory.promise);

    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();
    const table = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    await wrapper.invoke(table, 'onRefresh');

    refreshedHistory.resolve([createResult('result-new', new Date('2026-01-03T00:00:00Z').getTime())]);
    await wrapper.flush();

    let refreshedTable = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    expect(refreshedTable.props['data-loading']).toBe(false);
    expect(refreshedTable.props['data-results']).toBe(1);

    initialHistory.resolve([createResult('result-old', new Date('2026-01-02T00:00:00Z').getTime())]);
    await wrapper.flush();

    refreshedTable = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    expect(refreshedTable.props['data-loading']).toBe(false);
    expect(refreshedTable.props['data-results']).toBe(1);
  });

  it('shows detail loading, stale protection, and retryable errors without clearing history', async () => {
    const firstDetail = deferred<BacktestResultDetail>();
    const secondDetail = deferred<BacktestResultDetail>();
    services.fetchBacktestResults.mockResolvedValue([
      createResult('result-a', new Date('2026-01-03T00:00:00Z').getTime()),
      createResult('result-b', new Date('2026-01-04T00:00:00Z').getTime()),
    ]);
    services.fetchBacktestResultDetail.mockImplementation((id: string) => {
      if (id === 'result-a') return firstDetail.promise;
      if (id === 'result-b') return secondDetail.promise;
      return Promise.reject(new Error('unexpected'));
    });

    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();
    const table = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    await wrapper.invoke(table, 'onSelectResult', 'result-a');
    expect(wrapper.find((node) => node.type === 'backtest-result-detail-stub').props['data-loading']).toBe(true);

    await wrapper.invoke(table, 'onSelectResult', 'result-b');
    secondDetail.resolve(createDetail(createResult('result-b', new Date('2026-01-04T00:00:00Z').getTime())));
    await wrapper.flush();
    firstDetail.resolve(createDetail(createResult('result-a', new Date('2026-01-03T00:00:00Z').getTime())));
    await wrapper.flush();

    const detail = wrapper.find((node) => node.type === 'backtest-result-detail-stub');
    expect(detail.props['data-selected-result-id']).toBe('result-b');
    expect(detail.props['data-detail-result-id']).toBe('result-b');
    expect(detail.props['data-has-detail']).toBe(true);

    const refresh = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    const newResults = [createResult('result-b', new Date('2026-01-04T00:00:00Z').getTime())];
    services.fetchBacktestResults.mockResolvedValueOnce(newResults);
    await wrapper.invoke(refresh, 'onRefresh');
    expect(wrapper.find((node) => node.type === 'backtest-results-table-stub').props['data-selected-result-id']).toBe('result-b');

    services.fetchBacktestResultDetail.mockReset();
    services.fetchBacktestResultDetail.mockRejectedValueOnce(new Error('detail failed'));
    await wrapper.invoke(refresh, 'onSelectResult', 'result-b');
    await wrapper.flush();
    expect(wrapper.find((node) => node.type === 'backtest-result-detail-stub').props['data-error']).toBe('Failed to load backtest detail chart. Please try another result or refresh.');
    expect(wrapper.find((node) => node.type === 'backtest-results-table-stub').props['data-results']).toBe(1);
  });

  it('clears selection when refreshed history no longer contains the selected result', async () => {
    services.fetchBacktestResults
      .mockResolvedValueOnce([
        createResult('result-a', new Date('2026-01-03T00:00:00Z').getTime()),
        createResult('result-b', new Date('2026-01-04T00:00:00Z').getTime()),
      ])
      .mockResolvedValueOnce([createResult('result-a', new Date('2026-01-03T00:00:00Z').getTime())]);
    services.fetchBacktestResultDetail.mockResolvedValue(createDetail(createResult('result-b', new Date('2026-01-04T00:00:00Z').getTime())));

    const wrapper = await mount(Backtest, {
      locale: 'en',
      components: {
        ElButton: defineHostComponent('el-button'),
        ElForm: defineHostComponent('el-form'),
        ElFormItem: defineHostComponent('el-form-item'),
        ElSelect: defineHostComponent('el-select'),
        ElOption: defineHostComponent('el-option'),
        ElDatePicker: defineHostComponent('el-date-picker'),
        ElInputNumber: defineHostComponent('el-input-number'),
        ElTable: defineHostComponent('el-table'),
        ElTableColumn: defineHostComponent('el-table-column'),
      },
    });

    await wrapper.flush();
    const table = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    await wrapper.invoke(table, 'onSelectResult', 'result-b');
    await wrapper.flush();

    services.fetchBacktestResults.mockResolvedValueOnce([createResult('result-a', new Date('2026-01-03T00:00:00Z').getTime())]);
    await wrapper.invoke(table, 'onRefresh');

    const refreshedTable = wrapper.find((node) => node.type === 'backtest-results-table-stub');
    const detail = wrapper.find((node) => node.type === 'backtest-result-detail-stub');
    expect(refreshedTable.props['data-selected-result-id']).toBe('');
    expect(detail.props['data-selected-result-id']).toBe('');
    expect(detail.props['data-has-detail']).toBe(false);
  });
});
