<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import Candlestick from '@/components/charts/Candlestick.vue';
import MetricCard from '@/components/ui/MetricCard.vue';
import DataState from '@/components/ui/DataState.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import type { BacktestResultDetail as BacktestResultDetailData } from '@/types/backtest';
import {
  EMPTY_BACKTEST_VALUE,
  formatBacktestNumber,
  formatBacktestPercent,
  formatBacktestTime,
} from '@/utils/backtest';

interface Props {
  selectedDetail: BacktestResultDetailData | null;
  selectedResultId: string | null;
  loading?: boolean;
  error?: string | null;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  error: null,
});

const emit = defineEmits<{
  retry: [];
}>();

const { t, locale } = useI18n();
const currentLocale = computed(() => locale.value);

const hasSelection = computed(() => Boolean(props.selectedResultId));
const hasVisibleDetail = computed(() => Boolean(
  props.selectedResultId
  && props.selectedDetail
  && props.selectedDetail.result.id === props.selectedResultId,
));

const detail = computed(() => (hasVisibleDetail.value ? props.selectedDetail : null));

const metricCards = computed(() => ([
  {
    key: 'total_return',
    label: t('backtest.metrics.totalReturn'),
    value: formatBacktestPercent(detail.value?.result.total_return),
    tone: 'primary' as const,
  },
  {
    key: 'sharpe_ratio',
    label: t('backtest.metrics.sharpeRatio'),
    value: formatBacktestNumber(detail.value?.result.sharpe_ratio),
    tone: 'success' as const,
  },
  {
    key: 'max_drawdown',
    label: t('backtest.metrics.maxDrawdown'),
    value: formatBacktestPercent(detail.value?.result.max_drawdown),
    tone: 'warning' as const,
  },
  {
    key: 'win_rate',
    label: t('backtest.metrics.winRate'),
    value: formatBacktestPercent(detail.value?.result.win_rate),
    tone: 'danger' as const,
  },
  {
    key: 'total_trades',
    label: t('backtest.metrics.totalTrades'),
    value: detail.value?.result.total_trades === undefined || detail.value?.result.total_trades === null
      ? EMPTY_BACKTEST_VALUE
      : formatBacktestNumber(detail.value.result.total_trades, 0),
    tone: 'neutral' as const,
  },
]));

const summaryFields = computed(() => {
  if (!detail.value) return [];

  return [
    { key: 'strategy', label: t('backtest.resultStrategy'), value: detail.value.result.strategy },
    { key: 'symbol', label: t('common.symbol'), value: detail.value.result.symbol },
    { key: 'timeframe', label: t('common.timeframe'), value: detail.value.result.timeframe },
    { key: 'start', label: t('backtest.startTime'), value: formatBacktestTime(detail.value.result.start_time, currentLocale.value) },
    { key: 'end', label: t('backtest.endTime'), value: formatBacktestTime(detail.value.result.end_time, currentLocale.value) },
    { key: 'capital', label: t('backtest.initialCapital'), value: formatBacktestNumber(detail.value.result.initial_capital, 0) },
    { key: 'created', label: t('common.timestamp'), value: formatBacktestTime(detail.value.result.created_at, currentLocale.value) },
  ];
});

function retry(): void {
  emit('retry');
}
</script>

<template>
  <SectionCard :title="t('backtest.resultDetail')" :description="t('backtest.resultDetailDescription')">
    <DataState
      :loading="props.loading"
      :error="!props.loading ? props.error : null"
      :empty="!hasSelection || !hasVisibleDetail"
      :empty-description="t('backtest.selectHistoryEmpty')"
      @retry="retry"
    >
      <div class="backtest-result-detail" :aria-busy="props.loading || undefined">
        <div class="backtest-result-detail__layout">
          <section class="backtest-result-detail__summary">
            <h3 class="backtest-result-detail__section-title">{{ t('backtest.resultSummary') }}</h3>
            <div class="backtest-result-detail__metrics">
              <MetricCard
                v-for="card in metricCards"
                :key="card.key"
                :label="card.label"
                :value="card.value"
                :tone="card.tone"
              />
            </div>
            <dl class="backtest-result-detail__fields">
              <div v-for="field in summaryFields" :key="field.key" class="backtest-result-detail__field">
                <dt>{{ field.label }}</dt>
                <dd>{{ field.value }}</dd>
              </div>
            </dl>
          </section>

          <section class="backtest-result-detail__chart-section">
            <h3 class="backtest-result-detail__section-title">{{ t('backtest.resultChart') }}</h3>
            <div class="backtest-result-detail__chart">
              <Candlestick
                :klines="detail?.klines ?? []"
                :markers="detail?.markers ?? []"
                :symbol="detail?.result.symbol ?? ''"
                :timeframe="detail?.result.timeframe ?? ''"
                :height="460"
              />
            </div>
          </section>
        </div>
      </div>
    </DataState>
  </SectionCard>
</template>

<style scoped>
.backtest-result-detail {
  min-width: 0;
}

.backtest-result-detail__layout {
  display: grid;
  grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.2fr);
  gap: var(--ui-space-16);
}

.backtest-result-detail__summary,
.backtest-result-detail__chart-section {
  min-width: 0;
  padding: var(--ui-space-16);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-surface);
}

.backtest-result-detail__section-title {
  margin: 0 0 var(--ui-space-12);
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-16);
  line-height: 1.5;
}

.backtest-result-detail__metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ui-space-12);
}

.backtest-result-detail__fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ui-space-12);
  margin: var(--ui-space-16) 0 0;
}

.backtest-result-detail__field {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-4);
  padding: var(--ui-space-12);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-info-soft);
}

.backtest-result-detail__field dt {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-12);
  line-height: 1.4;
}

.backtest-result-detail__field dd {
  margin: 0;
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-14);
  line-height: 1.5;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.backtest-result-detail__chart {
  min-width: 0;
}

@media (max-width: 1024px) {
  .backtest-result-detail__layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 767px) {
  .backtest-result-detail__metrics,
  .backtest-result-detail__fields {
    grid-template-columns: 1fr;
  }
}
</style>
