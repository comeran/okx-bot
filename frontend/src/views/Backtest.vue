<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useI18n } from 'vue-i18n';

import AppPageHeader from '@/components/ui/AppPageHeader.vue';
import BacktestForm, { type BacktestFormModel } from '@/components/backtest/BacktestForm.vue';
import BacktestMetrics from '@/components/backtest/BacktestMetrics.vue';
import BacktestResultsTable from '@/components/backtest/BacktestResultsTable.vue';
import BacktestResultDetail from '@/components/backtest/BacktestResultDetail.vue';
import {
  runBacktest,
  fetchBacktestResultDetail,
  fetchBacktestResults,
} from '@/services/backtest';
import { listStrategies, listStrategyConfigs, listStrategyTypes } from '@/services/strategies';
import type {
  BacktestMetrics as BacktestMetricsData,
  BacktestRequest,
  BacktestResult,
  BacktestResultDetail as BacktestResultDetailData,
} from '@/types/backtest';
import type {
  StrategyConfig,
  StrategyDefinition,
  StrategyRuntimeSummary,
} from '@/types/strategy';
import { getBacktestApiErrorMessage, getBacktestValidationError } from '@/utils/backtest';

type BacktestStrategyOptionSource = 'builtin' | 'config';

interface BacktestStrategyOption {
  id: string;
  value: string;
  backendValue: string;
  label: string;
  disabled?: boolean;
}

const { t, locale } = useI18n();

const timeframeOptions = ['1m', '5m', '15m', '1h', '4h', '1d'];
const symbolOptions = ['BTC-USDT', 'ETH-USDT', 'OKB-USDT', 'SOL-USDT'];
const defaultEndTime = new Date();
const defaultStartTime = new Date(defaultEndTime.getTime() - 30 * 24 * 60 * 60 * 1000);

const form = reactive<BacktestFormModel>({
  strategy: 'ma_cross',
  symbol: 'BTC-USDT',
  timeframe: '1h',
  startTime: defaultStartTime,
  endTime: defaultEndTime,
  initialCapital: 100000,
});

const validationError = computed(() => getBacktestValidationError(form.startTime, form.endTime, form.initialCapital));

const strategyTypes = ref<StrategyDefinition[]>([]);
const strategyTypesState = ref<'loading' | 'loaded' | 'failed'>('loading');
const strategyConfigs = ref<StrategyConfig[]>([]);
const strategies = ref<StrategyRuntimeSummary[]>([]);

const latestMetrics = ref<BacktestMetricsData | null>(null);
const results = ref<BacktestResult[]>([]);
const selectedResultId = ref<string | null>(null);
const selectedDetail = ref<BacktestResultDetailData | null>(null);
const strategiesLoading = ref(false);
const running = ref(false);
const resultsLoading = ref(false);
const detailLoading = ref(false);
const detailError = ref<string | null>(null);
let detailRequestToken = 0;
let historyRequestToken = 0;
let strategyCatalogRequestToken = 0;

const runtimeStatusByName = computed(() => new Map(
  strategies.value.map((strategy) => [strategy.name, strategy.status]),
));

const strategyTypeCatalog = computed(() => (
  strategyTypesState.value === 'loaded' ? strategyTypes.value : []
));

const builtInNames = computed(() => new Set(
  strategyTypeCatalog.value.map((strategyType) => strategyType.strategy_type),
));

function strategySourceLabel(source: BacktestStrategyOptionSource): string {
  if (locale.value.startsWith('zh')) {
    return source === 'builtin' ? '内置策略' : '保存配置';
  }
  return source === 'builtin' ? 'Built-in strategy' : 'Saved config';
}

function strategyStatusLabel(status: StrategyRuntimeSummary['status']): string {
  const normalizedStatus = status === 'running' || status === 'stopped' || status === 'starting' || status === 'error'
    ? status
    : 'unknown';
  return t(`common.${normalizedStatus}`);
}

function strategyOptionLabel(
  name: string,
  source: BacktestStrategyOptionSource,
  status?: StrategyRuntimeSummary['status'],
  disabled = false,
): string {
  const parts = [name, strategySourceLabel(source)];
  if (status) {
    parts.push(strategyStatusLabel(status));
  }
  if (disabled) {
    parts.push(locale.value.startsWith('zh') ? '已禁用' : 'Disabled');
  }
  return parts.join(' · ');
}

const strategyConflictNames = computed(() => {
  if (strategyTypesState.value !== 'loaded') {
    return [];
  }

  return strategyConfigs.value
    .filter((config) => builtInNames.value.has(config.name))
    .map((config) => config.name);
});

const strategyConflictMessage = computed(() => {
  if (strategyTypesState.value !== 'loaded' || strategyConflictNames.value.length === 0) {
    return null;
  }

  const names = strategyConflictNames.value.join(', ');
  return t('backtest.strategyConflict', { names });
});

const strategyOptions = computed<BacktestStrategyOption[]>(() => {
  const options: BacktestStrategyOption[] = [];
  const catalogUnavailable = strategyTypesState.value !== 'loaded';

  if (!catalogUnavailable) {
    for (const strategyType of strategyTypeCatalog.value) {
      const status = runtimeStatusByName.value.get(strategyType.strategy_type);
      options.push({
        id: `builtin:${strategyType.strategy_type}`,
        value: strategyType.strategy_type,
        backendValue: strategyType.strategy_type,
        label: strategyOptionLabel(strategyType.strategy_type, 'builtin', status),
      });
    }
  }

  for (const config of strategyConfigs.value) {
    const status = runtimeStatusByName.value.get(config.name);
    const disabled = catalogUnavailable || builtInNames.value.has(config.name);
    options.push({
      id: `config:${config.name}`,
      value: catalogUnavailable ? config.name : (disabled ? `config:${config.name}` : config.name),
      backendValue: config.name,
      label: strategyOptionLabel(config.name, 'config', status, disabled),
      disabled,
    });
  }

  return options;
});

const selectedStrategyOption = computed(() => strategyOptions.value
  .find((option) => option.value === form.strategy));

function buildRequest(): BacktestRequest {
  return {
    strategy: selectedStrategyOption.value?.backendValue ?? form.strategy,
    symbol: form.symbol,
    timeframe: form.timeframe,
    start_time: form.startTime!.getTime(),
    end_time: form.endTime!.getTime(),
    initial_capital: form.initialCapital as number,
  };
}

function validateForm(): boolean {
  if (strategyTypesState.value !== 'loaded') {
    ElMessage.error(t('backtest.strategyCatalogUnavailable'));
    return false;
  }

  if (!selectedStrategyOption.value) {
    ElMessage.error(t('backtest.strategyUnavailable'));
    return false;
  }

  if (selectedStrategyOption.value.disabled) {
    ElMessage.error(strategyConflictMessage.value ?? t('backtest.strategyUnavailable'));
    return false;
  }

  if (validationError.value) {
    ElMessage.error(t(`backtest.validation.${validationError.value}`));
    return false;
  }

  return true;
}

async function loadStrategies(): Promise<void> {
  const requestToken = ++strategyCatalogRequestToken;
  strategiesLoading.value = true;
  try {
    const [strategyTypesResult, strategyConfigsResult, strategiesResult] = await Promise.allSettled([
      listStrategyTypes(),
      listStrategyConfigs(),
      listStrategies(),
    ]);
    if (requestToken !== strategyCatalogRequestToken) {
      return;
    }

    if (strategyTypesResult.status === 'fulfilled') {
      strategyTypes.value = strategyTypesResult.value;
      strategyTypesState.value = 'loaded';
    } else {
      strategyTypes.value = [];
      strategyTypesState.value = 'failed';
    }
    if (strategyConfigsResult.status === 'fulfilled') {
      strategyConfigs.value = strategyConfigsResult.value;
    }
    if (strategiesResult.status === 'fulfilled') {
      strategies.value = strategiesResult.value;
    }
    if (strategyTypesResult.status === 'rejected'
      || strategyConfigsResult.status === 'rejected'
      || strategiesResult.status === 'rejected') {
      ElMessage.error(t('backtest.loadStrategiesError'));
    }
  } finally {
    if (requestToken === strategyCatalogRequestToken) {
      strategiesLoading.value = false;
    }
  }
}

function clearSelectionIfMissing(nextResults: BacktestResult[]): void {
  if (!selectedResultId.value) {
    return;
  }

  const stillExists = nextResults.some((result) => result.id === selectedResultId.value);
  if (stillExists) {
    return;
  }

  selectedResultId.value = null;
  selectedDetail.value = null;
  detailError.value = null;
  detailLoading.value = false;
  detailRequestToken += 1;
}

async function loadResults(): Promise<void> {
  const requestToken = ++historyRequestToken;
  resultsLoading.value = true;
  try {
    const nextResults = await fetchBacktestResults();
    if (requestToken !== historyRequestToken) {
      return;
    }

    results.value = nextResults;
    clearSelectionIfMissing(nextResults);
  } catch {
    if (requestToken === historyRequestToken) {
      ElMessage.error(t('backtest.loadResultsError'));
    }
  } finally {
    if (requestToken === historyRequestToken) {
      resultsLoading.value = false;
    }
  }
}

async function loadResultDetail(id: string): Promise<void> {
  const requestToken = ++detailRequestToken;
  detailLoading.value = true;
  detailError.value = null;
  selectedDetail.value = null;

  try {
    const detail = await fetchBacktestResultDetail(id);
    if (requestToken === detailRequestToken && selectedResultId.value === id) {
      selectedDetail.value = detail;
    }
  } catch (error) {
    if (requestToken === detailRequestToken && selectedResultId.value === id) {
      selectedDetail.value = null;
      detailError.value = getBacktestApiErrorMessage(error) ?? t('backtest.detailLoadError');
    }
  } finally {
    if (requestToken === detailRequestToken && selectedResultId.value === id) {
      detailLoading.value = false;
    }
  }
}

function handleSelectResult(id: string): void {
  selectedResultId.value = id;
  void loadResultDetail(id);
}

async function handleRun(): Promise<void> {
  if (!validateForm()) {
    return;
  }

  running.value = true;
  try {
    latestMetrics.value = await runBacktest(buildRequest());
    ElMessage.success(t('backtest.runSuccess'));
    await loadResults();
  } catch (error) {
    ElMessage.error(getBacktestApiErrorMessage(error) ?? t('backtest.runError'));
  } finally {
    running.value = false;
  }
}

function handleRefreshResults(): void {
  void loadResults();
}

function handleRetryDetail(): void {
  if (!selectedResultId.value) {
    return;
  }

  void loadResultDetail(selectedResultId.value);
}

onMounted(() => {
  void loadStrategies();
  void loadResults();
});
</script>

<template>
  <section class="backtest-page">
    <AppPageHeader
      :title="t('backtest.title')"
      :description="t('backtest.description')"
    />

    <div class="backtest-page__section">
      <BacktestForm
        :form="form"
        :strategy-options="strategyOptions"
        :strategy-conflict-message="strategyConflictMessage"
        :strategy-catalog-unavailable="strategyTypesState === 'failed'"
        :symbol-options="symbolOptions"
        :timeframe-options="timeframeOptions"
        :strategies-loading="strategiesLoading"
        :running="running"
        :validation-error="validationError"
        @run="handleRun"
        @retry-strategies="loadStrategies"
      />
    </div>

    <div class="backtest-page__section">
      <BacktestMetrics
        :metrics="latestMetrics"
        :loading="running"
      />
    </div>

    <div class="backtest-page__section">
      <BacktestResultsTable
        :results="results"
        :selected-result-id="selectedResultId"
        :loading="resultsLoading"
        @select-result="handleSelectResult"
        @refresh="handleRefreshResults"
      />
    </div>

    <div class="backtest-page__section">
      <BacktestResultDetail
        :selected-detail="selectedDetail"
        :selected-result-id="selectedResultId"
        :loading="detailLoading"
        :error="detailError"
        @retry="handleRetryDetail"
      />
    </div>
  </section>
</template>

<style scoped>
.backtest-page {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
  min-width: 0;
}

.backtest-page__section {
  min-width: 0;
}
</style>
