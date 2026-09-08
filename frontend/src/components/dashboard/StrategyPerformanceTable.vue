<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';

import DataState from '@/components/ui/DataState.vue';
import ResponsiveTable from '@/components/ui/ResponsiveTable.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import StatusBadge from '@/components/ui/StatusBadge.vue';
import type { StrategyPerformanceDisplayRow } from '@/utils/strategyPerformance';
import {
  EMPTY_RUNTIME_VALUE,
  formatRuntimeCurrency,
  formatRuntimeNumber,
  formatRuntimeText,
  formatRuntimeTime,
  getDashboardStrategyStatusTagType,
} from '@/utils/dashboard';

interface Props {
  rows: StrategyPerformanceDisplayRow[];
  loading?: boolean;
  error?: string | null;
  stale?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  error: null,
  stale: false,
});

const emit = defineEmits<{
  retry: [];
}>();

const { t, locale } = useI18n();
const hasRows = computed(() => props.rows.length > 0);
const expandedStrategyName = ref<string | null>(null);
const expandedRowKeys = computed(() => (expandedStrategyName.value ? [expandedStrategyName.value] : []));

function detailsIdFor(name: string): string {
  return `strategy-performance-table-details-${encodeURIComponent(name)}`;
}

function isExpanded(name: string): boolean {
  return expandedStrategyName.value === name;
}

function toggleExpanded(name: string): void {
  expandedStrategyName.value = isExpanded(name) ? null : name;
}

function retry() {
  emit('retry');
}

function formatNullableCurrency(value?: number | null): string {
  return value === null || value === undefined ? EMPTY_RUNTIME_VALUE : formatRuntimeCurrency(value);
}

function formatNullableNumber(value?: number | null): string {
  return value === null || value === undefined ? EMPTY_RUNTIME_VALUE : formatRuntimeNumber(value);
}

function formatNullableTime(value?: number | null): string {
  return value === null || value === undefined ? EMPTY_RUNTIME_VALUE : formatRuntimeTime(value, locale.value);
}

function formatPercent(value?: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_RUNTIME_VALUE;
  }

  return `${formatRuntimeNumber(value * 100)}%`;
}

function formatStrategyStatus(status: string): string {
  if (status === 'running') return t('common.running');
  if (status === 'stopped') return t('common.stopped');
  if (status === 'starting') return t('common.starting');
  if (status === 'error') return t('common.error');
  if (status === 'unknown') return t('common.unknown');
  return formatRuntimeText(status);
}
</script>

<template>
  <SectionCard :title="t('dashboard.strategyPerformance')">
    <DataState
      :loading="false"
      :error="props.loading ? null : props.error"
      :stale="props.loading ? false : props.stale"
      :empty="!hasRows && !props.loading"
      :empty-description="t('dashboard.noStrategyPerformance')"
      @retry="retry"
    >
      <ResponsiveTable
        class="strategy-performance-table"
        :data="rows"
        :loading="props.loading"
        row-key="name"
        :expand-row-keys="expandedRowKeys"
        :scroll-label="t('dashboard.strategyPerformance')"
        :scroll-description="t('dashboard.strategyPerformance')"
      >
        <el-table-column type="expand" width="1" class-name="strategy-performance-table__expand-column">
          <template #default="{ row }">
            <div :id="detailsIdFor(row.name)" class="strategy-performance-table__details">
              <div class="strategy-performance-table__summary-grid">
                <div class="strategy-performance-table__summary-item">
                  <span>{{ t('dashboard.feesPaid') }}</span>
                  <strong>{{ formatNullableCurrency(row.fees_paid) }}</strong>
                </div>
                <div class="strategy-performance-table__summary-item">
                  <span>{{ t('dashboard.totalOrderCount') }}</span>
                  <strong>{{ formatNullableNumber(row.order_count) }}</strong>
                </div>
                <div class="strategy-performance-table__summary-item">
                  <span>{{ t('dashboard.filledOrderCount') }}</span>
                  <strong>{{ formatNullableNumber(row.filled_order_count) }}</strong>
                </div>
                <div class="strategy-performance-table__summary-item">
                  <span>{{ t('dashboard.lastOrder') }}</span>
                  <strong>{{ formatNullableTime(row.last_order_at) }}</strong>
                </div>
              </div>

              <div class="strategy-performance-table__nested-grid">
                <section class="strategy-performance-table__nested-section">
                  <h4>{{ t('dashboard.positions') }}</h4>
                  <el-empty v-if="row.positions.length === 0" :description="t('dashboard.noOpenPositions')" />
                  <ResponsiveTable
                    v-else
                    :data="row.positions"
                    :scroll-label="t('dashboard.positions')"
                    :scroll-description="t('dashboard.positions')"
                  >
                    <el-table-column prop="symbol" :label="t('common.symbol')" min-width="120">
                      <template #default="{ row: position }">
                        {{ formatRuntimeText(position.symbol) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.side')" min-width="90">
                      <template #default="{ row: position }">
                        {{ formatRuntimeText(position.side) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.size')" min-width="100">
                      <template #default="{ row: position }">
                        {{ formatNullableNumber(position.amount) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.entryPrice')" min-width="120">
                      <template #default="{ row: position }">
                        {{ formatNullableNumber(position.entry_price) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.markPrice')" min-width="120">
                      <template #default="{ row: position }">
                        {{ formatNullableNumber(position.mark_price) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.unrealizedPnl')" min-width="120">
                      <template #default="{ row: position }">
                        {{ formatNullableCurrency(position.unrealized_pnl) }}
                      </template>
                    </el-table-column>
                  </ResponsiveTable>
                </section>

                <section class="strategy-performance-table__nested-section">
                  <h4>{{ t('dashboard.recentOrders') }}</h4>
                  <el-empty v-if="row.recent_orders.length === 0" :description="t('dashboard.noRecentOrders')" />
                  <ResponsiveTable
                    v-else
                    :data="row.recent_orders"
                    :scroll-label="t('dashboard.recentOrders')"
                    :scroll-description="t('dashboard.recentOrders')"
                  >
                    <el-table-column prop="symbol" :label="t('common.symbol')" min-width="120">
                      <template #default="{ row: order }">
                        {{ formatRuntimeText(order.symbol) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.side')" min-width="90">
                      <template #default="{ row: order }">
                        {{ formatRuntimeText(order.side) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.orderType')" min-width="100">
                      <template #default="{ row: order }">
                        {{ formatRuntimeText(order.type) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.price')" min-width="110">
                      <template #default="{ row: order }">
                        {{ formatNullableNumber(order.price) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.amount')" min-width="100">
                      <template #default="{ row: order }">
                        {{ formatNullableNumber(order.amount) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('common.status')" min-width="100">
                      <template #default="{ row: order }">
                        {{ formatRuntimeText(order.status) }}
                      </template>
                    </el-table-column>
                    <el-table-column :label="t('dashboard.timestamp')" min-width="160">
                      <template #default="{ row: order }">
                        {{ formatNullableTime(order.timestamp) }}
                      </template>
                    </el-table-column>
                  </ResponsiveTable>
                </section>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column :label="t('dashboard.strategyStatus')" min-width="240">
          <template #default="{ row }">
            <div class="strategy-performance-table__strategy-cell">
              <el-button
                class="strategy-performance-table__expand-button"
                text
                :aria-label="`${isExpanded(row.name) ? t('common.collapse') : t('common.expand')} ${formatRuntimeText(row.name)}`"
                :aria-expanded="String(isExpanded(row.name))"
                :aria-controls="detailsIdFor(row.name)"
                @click="toggleExpanded(row.name)"
              >
                <span aria-hidden="true">{{ isExpanded(row.name) ? '−' : '+' }}</span>
              </el-button>
              <div class="strategy-performance-table__strategy-name">{{ formatRuntimeText(row.name) }}</div>
              <StatusBadge
                :status="formatStrategyStatus(row.status)"
                :tone="getDashboardStrategyStatusTagType(row.status)"
                :show-dot="true"
              />
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('dashboard.equity')" min-width="130">
          <template #default="{ row }">
            {{ formatNullableCurrency(row.equity) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('dashboard.returnPct')" min-width="130">
          <template #default="{ row }">
            {{ formatPercent(row.return_pct) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('dashboard.realizedPnl')" min-width="140">
          <template #default="{ row }">
            {{ formatNullableCurrency(row.realized_pnl) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('dashboard.unrealizedPnl')" min-width="140">
          <template #default="{ row }">
            {{ formatNullableCurrency(row.unrealized_pnl) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('dashboard.positionExposure')" min-width="140">
          <template #default="{ row }">
            {{ formatNullableCurrency(row.position_notional) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('dashboard.closedTradeCount')" min-width="140">
          <template #default="{ row }">
            {{ formatNullableNumber(row.closed_trade_count) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('dashboard.winRate')" min-width="120">
          <template #default="{ row }">
            {{ formatPercent(row.win_rate) }}
          </template>
        </el-table-column>
      </ResponsiveTable>
    </DataState>
  </SectionCard>
</template>

<style scoped>
.strategy-performance-table {
  min-width: 0;
}

.strategy-performance-table :deep(.el-table__expand-icon) {
  display: none;
}

.strategy-performance-table__details {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
  padding: var(--ui-space-16);
}

.strategy-performance-table__summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--ui-space-12);
}

.strategy-performance-table__summary-item {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-4);
  padding: var(--ui-space-12) var(--ui-space-12);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-info-soft);
}

.strategy-performance-table__summary-item span {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-12);
  line-height: 1.4;
}

.strategy-performance-table__summary-item strong {
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-14);
  line-height: 1.5;
}

.strategy-performance-table__nested-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--ui-space-16);
}

.strategy-performance-table__nested-section {
  min-width: 0;
}

.strategy-performance-table__nested-section h4 {
  margin: 0 0 var(--ui-space-12);
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-14);
  line-height: 1.5;
}

.strategy-performance-table__strategy-cell {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: var(--ui-space-8);
  min-width: 0;
  flex-wrap: wrap;
}

.strategy-performance-table__expand-button {
  flex: 0 0 auto;
  min-width: 0;
  padding: 0;
}

.strategy-performance-table__strategy-name {
  flex: 1 1 auto;
  min-width: 0;
  font-weight: 600;
  color: var(--ui-color-text);
}
</style>
