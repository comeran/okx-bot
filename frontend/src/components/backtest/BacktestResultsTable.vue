<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import DataState from '@/components/ui/DataState.vue';
import ResponsiveTable from '@/components/ui/ResponsiveTable.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import type { BacktestResult } from '@/types/backtest';
import {
  EMPTY_BACKTEST_VALUE,
  formatBacktestNumber,
  formatBacktestPercent,
  formatBacktestTime,
} from '@/utils/backtest';

interface Props {
  results: BacktestResult[];
  selectedResultId: string | null;
  loading?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
});

const emit = defineEmits<{
  'select-result': [resultId: string];
  refresh: [];
}>();

const { t, locale } = useI18n();
const currentLocale = computed(() => locale.value);

const hasResults = computed(() => props.results.length > 0);

function selectResult(resultId: string): void {
  emit('select-result', resultId);
}

function handleRowClick(row: BacktestResult): void {
  selectResult(row.id);
}

function refresh(): void {
  emit('refresh');
}

function rowClassName({ row }: { row: BacktestResult }): string {
  return row.id === props.selectedResultId ? 'backtest-results-table__row--selected' : '';
}
</script>

<template>
  <SectionCard :title="t('backtest.history')" :description="t('backtest.historyDescription')">
    <template #actions>
      <el-button :loading="props.loading" @click="refresh">
        {{ t('common.refresh') }}
      </el-button>
    </template>

    <DataState
      :loading="props.loading"
      :empty="!hasResults"
      :empty-description="t('backtest.noResults')"
      @retry="refresh"
    >
      <ResponsiveTable
        class="backtest-results-table"
        :data="props.results"
        :current-row-key="props.selectedResultId ?? undefined"
        :row-class-name="rowClassName"
        highlight-current-row
        row-key="id"
        stripe
        @row-click="handleRowClick"
        :scroll-label="t('backtest.history')"
        :scroll-description="t('backtest.historyDescription')"
      >
        <el-table-column prop="strategy" :label="t('backtest.strategy')" min-width="140" />
        <el-table-column prop="symbol" :label="t('common.symbol')" min-width="120" />
        <el-table-column prop="timeframe" :label="t('common.timeframe')" min-width="100" />
        <el-table-column :label="t('backtest.startTime')" min-width="180">
          <template #default="{ row }">
            {{ formatBacktestTime(row.start_time, currentLocale) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.endTime')" min-width="180">
          <template #default="{ row }">
            {{ formatBacktestTime(row.end_time, currentLocale) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('common.timestamp')" min-width="180">
          <template #default="{ row }">
            {{ formatBacktestTime(row.created_at, currentLocale) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.metrics.totalReturn')" min-width="130">
          <template #default="{ row }">
            {{ formatBacktestPercent(row.total_return) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.metrics.sharpeRatio')" min-width="130">
          <template #default="{ row }">
            {{ formatBacktestNumber(row.sharpe_ratio) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.metrics.maxDrawdown')" min-width="140">
          <template #default="{ row }">
            {{ formatBacktestPercent(row.max_drawdown) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.metrics.winRate')" min-width="120">
          <template #default="{ row }">
            {{ formatBacktestPercent(row.win_rate) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.metrics.totalTrades')" min-width="120">
          <template #default="{ row }">
            {{ row.total_trades === null || row.total_trades === undefined ? EMPTY_BACKTEST_VALUE : formatBacktestNumber(row.total_trades, 0) }}
          </template>
        </el-table-column>
        <template #empty>
          <div class="backtest-results-table__empty">{{ t('backtest.noResults') }}</div>
        </template>
      </ResponsiveTable>
    </DataState>
  </SectionCard>
</template>

<style scoped>
.backtest-results-table {
  min-width: 0;
}

.backtest-results-table__empty {
  padding: var(--ui-space-16);
  color: var(--ui-color-text-secondary);
}

:deep(.backtest-results-table__row--selected) {
  background: color-mix(in srgb, var(--ui-color-primary-soft) 84%, white);
}
</style>
