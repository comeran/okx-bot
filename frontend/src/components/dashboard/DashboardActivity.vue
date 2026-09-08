<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import DataState from '@/components/ui/DataState.vue';
import ResponsiveTable from '@/components/ui/ResponsiveTable.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import StatusBadge from '@/components/ui/StatusBadge.vue';
import type {
  DashboardWebSocketMessage,
  Order,
  Position,
} from '@/types/dashboard';
import { EMPTY_RUNTIME_VALUE, formatRuntimeNumber, formatRuntimeText, formatRuntimeTime, formatRuntimePayloadPreview, getDashboardStrategyStatusTagType } from '@/utils/dashboard';
import type { StrategyPerformanceRuntimeSummary } from '@/utils/strategyPerformance';

interface Props {
  recentOrders: Order[];
  positions: Position[];
  runtimeSummaries: StrategyPerformanceRuntimeSummary[];
  runtimeErrors?: Record<string, string>;
  websocketMessages: DashboardWebSocketMessage[];
  loading?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  runtimeErrors: () => ({}),
  loading: false,
});

const { t, locale } = useI18n();

const messageRows = computed(() => props.websocketMessages);

function formatNullableNumber(value?: number | null): string {
  return value === null || value === undefined ? EMPTY_RUNTIME_VALUE : formatRuntimeNumber(value);
}

function formatNullableTime(value?: number | null): string {
  return value === null || value === undefined ? EMPTY_RUNTIME_VALUE : formatRuntimeTime(value, locale.value);
}

function statusLabel(status: string): string {
  if (status === 'running') return t('common.running');
  if (status === 'stopped') return t('common.stopped');
  if (status === 'starting') return t('common.starting');
  if (status === 'error') return t('common.error');
  if (status === 'unknown') return t('common.unknown');
  return formatRuntimeText(status);
}

function fullPayloadText(message: DashboardWebSocketMessage): string {
  if (message.type === 'raw') {
    return typeof message.data === 'string' ? message.data : EMPTY_RUNTIME_VALUE;
  }

  const { type, received_at, ...payload } = message;
  const entries = Object.entries(payload).filter(([, value]) => value !== undefined);
  if (entries.length === 0) {
    return EMPTY_RUNTIME_VALUE;
  }

  return JSON.stringify(Object.fromEntries(entries));
}
</script>

<template>
  <div class="dashboard-activity">
    <div class="dashboard-activity__column">
      <SectionCard :title="t('dashboard.recentOrders')">
        <DataState
          :loading="props.loading"
          :empty="!props.loading && props.recentOrders.length === 0"
          :empty-description="t('dashboard.noRecentOrders')"
        >
          <ResponsiveTable
            :data="props.recentOrders"
            :scroll-label="t('dashboard.recentOrders')"
            :scroll-description="t('dashboard.recentOrders')"
          >
            <el-table-column prop="symbol" :label="t('common.symbol')" min-width="110">
              <template #default="{ row }">
                {{ formatRuntimeText(row.symbol) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.side')" min-width="80">
              <template #default="{ row }">
                {{ formatRuntimeText(row.side) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.orderType')" min-width="90">
              <template #default="{ row }">
                {{ formatRuntimeText(row.type) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.price')" min-width="100">
              <template #default="{ row }">
                {{ formatNullableNumber(row.price) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.amount')" min-width="100">
              <template #default="{ row }">
                {{ formatNullableNumber(row.amount) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('common.status')" min-width="100">
              <template #default="{ row }">
                {{ formatRuntimeText(row.status) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.timestamp')" min-width="160">
              <template #default="{ row }">
                {{ formatNullableTime(row.timestamp) }}
              </template>
            </el-table-column>
          </ResponsiveTable>
        </DataState>
      </SectionCard>

      <SectionCard :title="t('dashboard.positions')">
        <DataState
          :loading="props.loading"
          :empty="!props.loading && props.positions.length === 0"
          :empty-description="t('dashboard.noPositions')"
        >
          <ResponsiveTable
            :data="props.positions"
            :scroll-label="t('dashboard.positions')"
            :scroll-description="t('dashboard.positions')"
          >
            <el-table-column prop="symbol" :label="t('common.symbol')" min-width="110">
              <template #default="{ row }">
                {{ formatRuntimeText(row.symbol) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.side')" min-width="80">
              <template #default="{ row }">
                {{ formatRuntimeText(row.side) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.size')" min-width="100">
              <template #default="{ row }">
                {{ formatNullableNumber(row.amount) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.entryPrice')" min-width="110">
              <template #default="{ row }">
                {{ formatNullableNumber(row.entry_price) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.markPrice')" min-width="110">
              <template #default="{ row }">
                {{ formatNullableNumber(row.mark_price) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.unrealizedPnl')" min-width="130">
              <template #default="{ row }">
                {{ formatNullableNumber(row.unrealized_pnl) }}
              </template>
            </el-table-column>
          </ResponsiveTable>
        </DataState>
      </SectionCard>
    </div>

    <div class="dashboard-activity__column">
      <SectionCard :title="t('dashboard.strategies')">
        <DataState
          :loading="props.loading"
          :empty="!props.loading && props.runtimeSummaries.length === 0"
          :empty-description="t('dashboard.noStrategies')"
        >
          <ResponsiveTable
            :data="props.runtimeSummaries"
            :scroll-label="t('dashboard.strategies')"
            :scroll-description="t('dashboard.strategies')"
          >
            <el-table-column :label="t('common.name')" min-width="140">
              <template #default="{ row }">
                {{ formatRuntimeText(row.name) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('common.status')" min-width="140">
              <template #default="{ row }">
                <StatusBadge
                  :status="statusLabel(row.status)"
                  :tone="getDashboardStrategyStatusTagType(row.status)"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.lastError')" min-width="200">
              <template #default="{ row }">
                {{ formatRuntimeText(props.runtimeErrors[row.name]) }}
              </template>
            </el-table-column>
          </ResponsiveTable>
        </DataState>
      </SectionCard>

      <SectionCard :title="t('dashboard.websocketMessages')">
        <DataState
          :loading="props.loading"
          :empty="!props.loading && messageRows.length === 0"
          :empty-description="t('dashboard.noMessages')"
        >
          <ResponsiveTable
            :data="messageRows"
            :scroll-label="t('dashboard.websocketMessages')"
            :scroll-description="t('dashboard.websocketMessages')"
          >
            <el-table-column :label="t('dashboard.messageType')" min-width="120">
              <template #default="{ row }">
                <StatusBadge :status="formatRuntimeText(row.type)" tone="info" />
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.messageReceived')" min-width="160">
              <template #default="{ row }">
                {{ formatNullableTime(row.received_at) }}
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.messagePayload')" min-width="300">
              <template #default="{ row }">
                <el-tooltip :content="fullPayloadText(row)" placement="top" effect="light">
                  <code
                    class="dashboard-activity__payload"
                    :aria-label="fullPayloadText(row)"
                    :title="fullPayloadText(row)"
                  >
                    {{ formatRuntimePayloadPreview(row) }}
                  </code>
                </el-tooltip>
              </template>
            </el-table-column>
          </ResponsiveTable>
        </DataState>
      </SectionCard>
    </div>
  </div>
</template>

<style scoped>
.dashboard-activity {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ui-space-16);
  min-width: 0;
}

.dashboard-activity__column {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
  min-width: 0;
}

.dashboard-activity__payload {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  white-space: normal;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  color: var(--ui-color-text-secondary);
}

@media (max-width: 991px) {
  .dashboard-activity {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
