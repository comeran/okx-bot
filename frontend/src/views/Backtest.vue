<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useI18n } from 'vue-i18n';

import Candlestick from '@/components/charts/Candlestick.vue';
import {
  runBacktest,
  fetchBacktestResultDetail,
  fetchBacktestResults,
} from '@/services/backtest';
import { listStrategies } from '@/services/strategies';
import type {
  BacktestMetrics,
  BacktestRequest,
  BacktestResult,
  BacktestResultDetail,
} from '@/types/backtest';
import type { StrategyRuntimeSummary } from '@/types/strategy';
import { getBacktestApiErrorMessage, getBacktestValidationError } from '@/utils/backtest';

const { t } = useI18n();

const timeframeOptions = ['1m', '5m', '15m', '1h', '4h', '1d'];
const symbolOptions = ['BTC-USDT', 'ETH-USDT', 'OKB-USDT', 'SOL-USDT'];
const defaultEndTime = new Date();
const defaultStartTime = new Date(defaultEndTime.getTime() - 30 * 24 * 60 * 60 * 1000);

const form = reactive<{
  strategy: string;
  symbol: string;
  timeframe: string;
  startTime: Date | null;
  endTime: Date | null;
  initialCapital: number | null | undefined;
}>({
  strategy: 'ma_cross',
  symbol: 'BTC-USDT',
  timeframe: '1h',
  startTime: defaultStartTime,
  endTime: defaultEndTime,
  initialCapital: 100000,
});

const strategies = ref<StrategyRuntimeSummary[]>([]);
const latestMetrics = ref<BacktestMetrics | null>(null);
const results = ref<BacktestResult[]>([]);
const selectedResultId = ref<string | null>(null);
const selectedDetail = ref<BacktestResultDetail | null>(null);
const strategiesLoading = ref(false);
const running = ref(false);
const resultsLoading = ref(false);
const detailLoading = ref(false);
const detailError = ref(false);
let detailRequestToken = 0;

const strategyOptions = computed(() => {
  const names = strategies.value.map((strategy) => strategy.name);
  return Array.from(new Set(['ma_cross', ...names]));
});

const metricCards = computed(() => {
  if (!latestMetrics.value) {
    return [];
  }

  return [
    {
      label: t('backtest.metrics.totalReturn'),
      value: formatPercent(latestMetrics.value.total_return),
    },
    {
      label: t('backtest.metrics.sharpeRatio'),
      value: formatNumber(latestMetrics.value.sharpe_ratio),
    },
    {
      label: t('backtest.metrics.maxDrawdown'),
      value: formatPercent(latestMetrics.value.max_drawdown),
    },
    {
      label: t('backtest.metrics.winRate'),
      value: formatPercent(latestMetrics.value.win_rate),
    },
    {
      label: t('backtest.metrics.totalTrades'),
      value: String(latestMetrics.value.total_trades),
    },
  ];
});

function formatNumber(value: number): string {
  return value.toFixed(2);
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleString();
}

function buildRequest(): BacktestRequest {
  return {
    strategy: form.strategy,
    symbol: form.symbol,
    timeframe: form.timeframe,
    start_time: form.startTime!.getTime(),
    end_time: form.endTime!.getTime(),
    initial_capital: form.initialCapital as number,
  };
}

function validateForm(): boolean {
  const error = getBacktestValidationError(form.startTime, form.endTime, form.initialCapital);
  if (error) {
    ElMessage.error(t(`backtest.validation.${error}`));
    return false;
  }

  return true;
}

async function loadStrategies(): Promise<void> {
  strategiesLoading.value = true;
  try {
    strategies.value = await listStrategies();
  } catch {
    ElMessage.error(t('backtest.loadStrategiesError'));
  } finally {
    strategiesLoading.value = false;
  }
}

async function loadResults(): Promise<void> {
  resultsLoading.value = true;
  try {
    results.value = await fetchBacktestResults();
  } catch {
    ElMessage.error(t('backtest.loadResultsError'));
  } finally {
    resultsLoading.value = false;
  }
}

async function loadResultDetail(id: string): Promise<void> {
  const requestToken = ++detailRequestToken;
  detailLoading.value = true;
  detailError.value = false;
  selectedDetail.value = null;

  try {
    const detail = await fetchBacktestResultDetail(id);
    if (requestToken === detailRequestToken && selectedResultId.value === id) {
      selectedDetail.value = detail;
    }
  } catch {
    if (requestToken === detailRequestToken && selectedResultId.value === id) {
      selectedDetail.value = null;
      detailError.value = true;
    }
  } finally {
    if (requestToken === detailRequestToken && selectedResultId.value === id) {
      detailLoading.value = false;
    }
  }
}

function handleResultRowClick(row: BacktestResult): void {
  selectedResultId.value = row.id;
  void loadResultDetail(row.id);
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

onMounted(() => {
  void loadStrategies();
  void loadResults();
});
</script>

<template>
  <section class="backtest-page">
    <div class="backtest-page__header">
      <div>
        <h2>{{ t('backtest.title') }}</h2>
        <p>{{ t('backtest.description') }}</p>
      </div>
      <el-button :loading="resultsLoading" @click="loadResults">
        {{ t('common.refresh') }}
      </el-button>
    </div>

    <el-card shadow="hover" class="backtest-card">
      <template #header>{{ t('backtest.runBacktest') }}</template>
      <el-form :model="form" label-position="top" @submit.prevent="handleRun">
        <el-row :gutter="16">
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('backtest.strategy')">
              <el-select v-model="form.strategy" :loading="strategiesLoading" class="full-width">
                <el-option
                  v-for="strategy in strategyOptions"
                  :key="strategy"
                  :label="strategy"
                  :value="strategy"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('common.symbol')">
              <el-select v-model="form.symbol" filterable class="full-width">
                <el-option
                  v-for="symbol in symbolOptions"
                  :key="symbol"
                  :label="symbol"
                  :value="symbol"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('common.timeframe')">
              <el-select v-model="form.timeframe" class="full-width">
                <el-option
                  v-for="timeframe in timeframeOptions"
                  :key="timeframe"
                  :label="timeframe"
                  :value="timeframe"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('backtest.startTime')">
              <el-date-picker v-model="form.startTime" type="datetime" :placeholder="t('backtest.selectStartTime')" class="full-width" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('backtest.endTime')">
              <el-date-picker v-model="form.endTime" type="datetime" :placeholder="t('backtest.selectEndTime')" class="full-width" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :md="8">
            <el-form-item :label="t('backtest.initialCapital')">
              <el-input-number v-model="form.initialCapital" :min="0" :step="1000" class="full-width" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-button type="primary" :loading="running" native-type="submit">
          {{ t('backtest.run') }}
        </el-button>
      </el-form>
    </el-card>

    <el-card shadow="hover" class="backtest-card">
      <template #header>{{ t('backtest.latestMetrics') }}</template>
      <el-row v-if="latestMetrics" :gutter="16">
        <el-col v-for="metric in metricCards" :key="metric.label" :xs="24" :sm="12" :md="4">
          <div class="metric-card">
            <div class="metric-card__label">{{ metric.label }}</div>
            <div class="metric-card__value">{{ metric.value }}</div>
          </div>
        </el-col>
      </el-row>
      <el-empty v-else :description="t('backtest.noLatestMetrics')" />
    </el-card>

    <el-card shadow="hover" class="backtest-card">
      <template #header>{{ t('backtest.history') }}</template>
      <el-table
        v-loading="resultsLoading"
        :data="results"
        empty-text=" "
        highlight-current-row
        row-key="id"
        stripe
        @row-click="handleResultRowClick"
      >
        <el-table-column prop="strategy" :label="t('backtest.strategy')" min-width="120" />
        <el-table-column prop="symbol" :label="t('common.symbol')" min-width="120" />
        <el-table-column prop="timeframe" :label="t('common.timeframe')" min-width="100" />
        <el-table-column :label="t('backtest.startTime')" min-width="180">
          <template #default="{ row }">
            {{ formatTime(row.start_time) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.endTime')" min-width="180">
          <template #default="{ row }">
            {{ formatTime(row.end_time) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.metrics.totalReturn')" min-width="130">
          <template #default="{ row }">
            {{ formatPercent(row.total_return) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.metrics.sharpeRatio')" min-width="130">
          <template #default="{ row }">
            {{ formatNumber(row.sharpe_ratio) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.metrics.maxDrawdown')" min-width="140">
          <template #default="{ row }">
            {{ formatPercent(row.max_drawdown) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.metrics.winRate')" min-width="120">
          <template #default="{ row }">
            {{ formatPercent(row.win_rate) }}
          </template>
        </el-table-column>
        <el-table-column prop="total_trades" :label="t('backtest.metrics.totalTrades')" min-width="120" />
      </el-table>
      <el-empty v-if="!resultsLoading && results.length === 0" :description="t('backtest.noResults')" />
    </el-card>

    <el-card shadow="hover" class="backtest-card">
      <template #header>{{ t('backtest.historyChart') }}</template>
      <div v-loading="detailLoading" class="history-chart">
        <el-alert
          v-if="detailError"
          :title="t('backtest.detailLoadError')"
          type="error"
          show-icon
          :closable="false"
          class="history-chart__error"
        />
        <el-empty
          v-if="!selectedResultId && !detailError"
          :description="t('backtest.selectHistoryEmpty')"
        />
        <Candlestick
          v-else-if="selectedDetail && selectedDetail.result.id === selectedResultId"
          :klines="selectedDetail.klines"
          :markers="selectedDetail.markers"
          :symbol="selectedDetail.result.symbol"
          :timeframe="selectedDetail.result.timeframe"
          :height="460"
        />
      </div>
    </el-card>
  </section>
</template>

<style scoped>
.backtest-page h2 {
  margin: 0 0 8px;
}

.backtest-page p {
  margin: 0;
  color: #606266;
}

.backtest-page__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.backtest-card {
  margin-bottom: 20px;
}

.full-width,
.backtest-card :deep(.el-select) {
  width: 100%;
}

.metric-card {
  padding: 16px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #fafafa;
}

.metric-card__label {
  margin-bottom: 8px;
  color: #606266;
  font-size: 13px;
}

.metric-card__value {
  color: #303133;
  font-size: 24px;
  font-weight: 600;
}

.history-chart {
  min-height: 320px;
}

.history-chart__error {
  margin-bottom: 16px;
}

.backtest-card :deep(.el-table__row) {
  cursor: pointer;
}
</style>
