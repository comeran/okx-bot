<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import MetricCard from '@/components/ui/MetricCard.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import type { BacktestMetrics as BacktestMetricsData } from '@/types/backtest';
import {
  EMPTY_BACKTEST_VALUE,
  formatBacktestNumber,
  formatBacktestPercent,
} from '@/utils/backtest';

interface Props {
  metrics: BacktestMetricsData | null;
  loading?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
});

const { t } = useI18n();

const cards = computed(() => ([
  {
    key: 'total_return',
    label: t('backtest.metrics.totalReturn'),
    value: formatBacktestPercent(props.metrics?.total_return),
    tone: 'primary' as const,
  },
  {
    key: 'sharpe_ratio',
    label: t('backtest.metrics.sharpeRatio'),
    value: formatBacktestNumber(props.metrics?.sharpe_ratio),
    tone: 'success' as const,
  },
  {
    key: 'max_drawdown',
    label: t('backtest.metrics.maxDrawdown'),
    value: formatBacktestPercent(props.metrics?.max_drawdown),
    tone: 'warning' as const,
  },
  {
    key: 'win_rate',
    label: t('backtest.metrics.winRate'),
    value: formatBacktestPercent(props.metrics?.win_rate),
    tone: 'danger' as const,
  },
  {
    key: 'total_trades',
    label: t('backtest.metrics.totalTrades'),
    value: props.metrics?.total_trades === undefined || props.metrics?.total_trades === null
      ? EMPTY_BACKTEST_VALUE
      : formatBacktestNumber(props.metrics.total_trades, 0),
    tone: 'neutral' as const,
  },
]));

const hasMetrics = computed(() => Boolean(props.metrics));
</script>

<template>
  <SectionCard :title="t('backtest.latestMetrics')" :description="t('backtest.metrics.description')">
    <div class="backtest-metrics" :aria-busy="props.loading || undefined">
      <div class="backtest-metrics__grid">
        <MetricCard
          v-for="card in cards"
          :key="card.key"
          :label="card.label"
          :value="card.value"
          :tone="card.tone"
          :loading="props.loading"
        />
      </div>
      <p v-if="!hasMetrics && !props.loading" class="backtest-metrics__empty">
        {{ t('backtest.noLatestMetrics') }}
      </p>
    </div>
  </SectionCard>
</template>

<style scoped>
.backtest-metrics {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-12);
}

.backtest-metrics__grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--ui-space-12);
}

.backtest-metrics__empty {
  margin: 0;
  color: var(--ui-color-text-secondary);
}

@media (max-width: 1024px) {
  .backtest-metrics__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .backtest-metrics__grid {
    grid-template-columns: 1fr;
  }
}
</style>
