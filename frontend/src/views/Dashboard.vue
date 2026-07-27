<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts/core';
import { PieChart } from 'echarts/charts';
import { LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { PieSeriesOption } from 'echarts/charts';
import type { ComposeOption, ECharts } from 'echarts/core';
import type { LegendComponentOption, TooltipComponentOption } from 'echarts/components';
import { useI18n } from 'vue-i18n';

import { useWebSocket } from '@/composables/useWebSocket';
import { useDashboardStore } from '@/stores/dashboard';
import type { AssetBalance } from '@/types/dashboard';
import {
  formatRuntimeCurrency,
  formatRuntimeNumber,
  formatRuntimePayloadPreview,
  formatRuntimeText,
  formatRuntimeTime,
  formatTickerPrice,
  getDashboardStrategyStatusTagType,
} from '@/utils/dashboard';

echarts.use([CanvasRenderer, LegendComponent, PieChart, TooltipComponent]);

type AccountAllocationOption = ComposeOption<
  LegendComponentOption | PieSeriesOption | TooltipComponentOption
>;

type AssetAllocationRow = AssetBalance & {
  allocationShare: number;
};

const { t } = useI18n();
const dashboard = useDashboardStore();
const websocket = useWebSocket('/ws', {
  onMessage: dashboard.addWebSocketMessage,
});

const latestMessages = computed(() => dashboard.websocketMessages.slice(0, 5));
const lastUpdatedText = computed(() => formatRuntimeTime(dashboard.lastUpdatedAt ?? undefined));
const accountAllocationChartRef = ref<HTMLDivElement | null>(null);
let accountAllocationChart: ECharts | null = null;

const positiveAccountAssets = computed(() => (
  dashboard.account?.assets?.filter((asset) => asset.eq_utd > 0) ?? []
));
const accountAllocationTotal = computed(() => (
  positiveAccountAssets.value.reduce((sum, asset) => sum + asset.eq_utd, 0)
));
const accountAssetRows = computed<AssetAllocationRow[]>(() => (
  dashboard.account?.assets?.map((asset) => ({
    ...asset,
    allocationShare: asset.eq_utd > 0 && accountAllocationTotal.value > 0
      ? (asset.eq_utd / accountAllocationTotal.value) * 100
      : 0,
  })) ?? []
));
const accountAllocationPieData = computed(() => (
  positiveAccountAssets.value.map((asset) => ({
    name: asset.ccy,
    value: asset.eq_utd,
  }))
));
const hasAccountAssets = computed(() => accountAssetRows.value.length > 0);
const hasAccountAllocationAssets = computed(() => accountAllocationPieData.value.length > 0);

function disposeAccountAllocationChart() {
  accountAllocationChart?.dispose();
  accountAllocationChart = null;
}

function resizeAccountAllocationChart() {
  accountAllocationChart?.resize();
}

async function updateAccountAllocationChart() {
  if (!hasAccountAllocationAssets.value) {
    disposeAccountAllocationChart();
    return;
  }

  await nextTick();
  const chartElement = accountAllocationChartRef.value;
  if (!chartElement) {
    return;
  }

  accountAllocationChart = echarts.getInstanceByDom(chartElement) ?? echarts.init(chartElement);

  const option: AccountAllocationOption = {
    tooltip: {
      trigger: 'item',
      valueFormatter: (value) => formatRuntimeCurrency(Number(value)),
    },
    legend: {
      bottom: 0,
      type: 'scroll',
    },
    series: [
      {
        name: t('dashboard.assetAllocation'),
        type: 'pie',
        radius: ['42%', '70%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        data: accountAllocationPieData.value,
        label: {
          formatter: '{b}: {d}%',
        },
      },
    ],
  };

  accountAllocationChart.setOption(option, true);
  accountAllocationChart.resize();
}

watch(websocket.connected, (connected) => {
  dashboard.setWebSocketConnected(connected);
});

watch(
  accountAllocationPieData,
  () => {
    void updateAccountAllocationChart();
  },
  { deep: true },
);

onMounted(() => {
  void dashboard.loadInitialData();
  websocket.connect();
  window.addEventListener('resize', resizeAccountAllocationChart);
  void updateAccountAllocationChart();
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeAccountAllocationChart);
  disposeAccountAllocationChart();
});
</script>

<template>
  <section>
    <div class="dashboard-header">
      <div>
        <h2>{{ t('dashboard.title') }}</h2>
        <p class="dashboard-header__meta">
          {{ t('dashboard.lastUpdated') }}: {{ lastUpdatedText }}
        </p>
      </div>
      <div class="dashboard-header__actions">
        <el-tag :type="dashboard.websocketConnected ? 'success' : 'danger'">
          WebSocket {{ dashboard.websocketConnected ? t('common.connected') : t('common.disconnected') }}
        </el-tag>
        <el-button :loading="dashboard.loading" @click="dashboard.loadInitialData">
          {{ t('common.refresh') }}
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="dashboard.error"
      :title="dashboard.error"
      type="error"
      show-icon
      class="dashboard-alert"
    />

    <el-row :gutter="16">
      <el-col :xs="24" :sm="12" :lg="4">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.totalEquity') }}</template>
          <div class="metric">{{ formatRuntimeCurrency(dashboard.account?.equity) }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.cashBalance') }}</template>
          <div class="metric">{{ formatRuntimeCurrency(dashboard.account?.cash_balance) }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.realizedPnl') }}</template>
          <div class="metric">{{ formatRuntimeCurrency(dashboard.account?.realized_pnl) }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.dailyPnl') }}</template>
          <div class="metric">{{ formatRuntimeCurrency(dashboard.account?.daily_pnl) }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.feesPaid') }}</template>
          <div class="metric">{{ formatRuntimeCurrency(dashboard.account?.fees_paid) }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.activeStrategies') }}</template>
          <div class="metric">{{ dashboard.activeStrategyCount }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="dashboard-section">
      <el-col :span="24">
        <el-card shadow="never" v-loading="dashboard.loading">
          <template #header>
            <div class="account-overview__header">
              <span>{{ t('dashboard.accountOverview') }}</span>
              <span class="account-overview__total">
                {{ t('dashboard.totalEquity') }}: {{ formatRuntimeCurrency(dashboard.account?.equity) }}
              </span>
            </div>
          </template>
          <el-empty v-if="!hasAccountAssets" :description="t('dashboard.noAssets')" />
          <el-row v-else :gutter="16" class="account-overview">
            <el-col :xs="24" :lg="8">
              <div
                v-if="hasAccountAllocationAssets"
                class="account-allocation-chart"
                ref="accountAllocationChartRef"
              />
              <el-empty v-else :description="t('dashboard.noAssets')" />
            </el-col>
            <el-col :xs="24" :lg="16">
              <el-table :data="accountAssetRows" size="small">
                <el-table-column prop="ccy" :label="t('dashboard.currency')" min-width="90" />
                <el-table-column :label="t('dashboard.assetCashBalance')" min-width="130">
                  <template #default="{ row }">{{ formatRuntimeNumber(row.cash_bal) }}</template>
                </el-table-column>
                <el-table-column :label="t('dashboard.nativeEquity')" min-width="130">
                  <template #default="{ row }">{{ formatRuntimeNumber(row.eq) }}</template>
                </el-table-column>
                <el-table-column :label="t('dashboard.convertedEquity')" min-width="140">
                  <template #default="{ row }">{{ formatRuntimeCurrency(row.eq_utd) }}</template>
                </el-table-column>
                <el-table-column :label="t('dashboard.unrealizedPnl')" min-width="130">
                  <template #default="{ row }">{{ formatRuntimeNumber(row.upl) }}</template>
                </el-table-column>
                <el-table-column :label="t('dashboard.allocationShare')" min-width="130">
                  <template #default="{ row }">{{ formatRuntimeNumber(row.allocationShare) }}%</template>
                </el-table-column>
              </el-table>
            </el-col>
          </el-row>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="dashboard-section">
      <el-col :span="24">
        <el-card shadow="never" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.marketTickers') }}</template>
          <el-alert
            v-if="dashboard.tickerError"
            :title="t('dashboard.tickerLoadError')"
            :description="dashboard.tickerError"
            type="warning"
            show-icon
            class="dashboard-alert"
          />
          <el-empty v-else-if="dashboard.tickers.length === 0" :description="t('dashboard.noMarketTickers')" />
          <el-table v-else :data="dashboard.tickers" size="small">
            <el-table-column prop="symbol" :label="t('common.symbol')" />
            <el-table-column :label="t('dashboard.last')">
              <template #default="{ row }">{{ formatTickerPrice(row.last) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.bid')">
              <template #default="{ row }">{{ formatTickerPrice(row.bidPx) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.ask')">
              <template #default="{ row }">{{ formatTickerPrice(row.askPx) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.volume24h')">
              <template #default="{ row }">{{ formatTickerPrice(row.vol24h) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="dashboard-section">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.positions') }}</template>
          <el-empty v-if="dashboard.positions.length === 0" :description="t('dashboard.noPositions')" />
          <el-table v-else :data="dashboard.positions" size="small">
            <el-table-column prop="symbol" :label="t('common.symbol')" min-width="110">
              <template #default="{ row }">{{ formatRuntimeText(row.symbol) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.side')" min-width="80">
              <template #default="{ row }">{{ formatRuntimeText(row.side) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.size')" min-width="100">
              <template #default="{ row }">{{ formatRuntimeNumber(row.amount) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.entryPrice')" min-width="110">
              <template #default="{ row }">{{ formatRuntimeNumber(row.entry_price) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.markPrice')" min-width="110">
              <template #default="{ row }">{{ formatRuntimeNumber(row.mark_price) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.unrealizedPnl')" min-width="130">
              <template #default="{ row }">{{ formatRuntimeCurrency(row.unrealized_pnl) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="12">
        <el-card shadow="never" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.orders') }}</template>
          <el-empty v-if="dashboard.orders.length === 0" :description="t('dashboard.noOrders')" />
          <el-table v-else :data="dashboard.orders" size="small">
            <el-table-column prop="symbol" :label="t('common.symbol')" min-width="110">
              <template #default="{ row }">{{ formatRuntimeText(row.symbol) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.side')" min-width="80">
              <template #default="{ row }">{{ formatRuntimeText(row.side) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.orderType')" min-width="90">
              <template #default="{ row }">{{ formatRuntimeText(row.type) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.price')" min-width="100">
              <template #default="{ row }">{{ formatRuntimeNumber(row.price) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.amount')" min-width="100">
              <template #default="{ row }">{{ formatRuntimeNumber(row.amount) }}</template>
            </el-table-column>
            <el-table-column :label="t('common.status')" min-width="100">
              <template #default="{ row }">{{ formatRuntimeText(row.status) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.timestamp')" min-width="160">
              <template #default="{ row }">{{ formatRuntimeTime(row.timestamp) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="dashboard-section">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.strategies') }}</template>
          <el-empty v-if="dashboard.strategies.length === 0" :description="t('dashboard.noStrategies')" />
          <el-table v-else :data="dashboard.strategies" size="small">
            <el-table-column prop="name" :label="t('common.name')" min-width="140" />
            <el-table-column :label="t('common.status')" min-width="110">
              <template #default="{ row }">
                <el-tag :type="getDashboardStrategyStatusTagType(row.status)" effect="plain">
                  {{ row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.lastError')" min-width="180">
              <template #default="{ row }">
                {{ formatRuntimeText(dashboard.strategyErrors[row.name]) }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header>{{ t('dashboard.websocketMessages') }}</template>
          <el-empty v-if="latestMessages.length === 0" :description="t('dashboard.noMessages')" />
          <el-table v-else :data="latestMessages" size="small">
            <el-table-column :label="t('dashboard.messageType')" min-width="120">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('dashboard.messageReceived')" min-width="160">
              <template #default="{ row }">{{ formatRuntimeTime(row.received_at) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.messagePayload')" min-width="260">
              <template #default="{ row }">
                <code class="dashboard-message-payload">{{ formatRuntimePayloadPreview(row) }}</code>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </section>
</template>

<style scoped>
.dashboard-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.dashboard-header__meta {
  margin: 6px 0 0;
  color: #606266;
}

.dashboard-header__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

h2 {
  margin: 0;
}

.dashboard-alert {
  margin-bottom: 16px;
}

.metric {
  font-size: 28px;
  font-weight: 700;
  color: #409eff;
}

.dashboard-section {
  margin-top: 16px;
}

.account-overview__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.account-overview__total {
  font-size: 14px;
  font-weight: 600;
  color: #409eff;
}

.account-overview {
  align-items: center;
}

.account-allocation-chart {
  width: 100%;
  min-height: 280px;
}

.dashboard-message-payload {
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  color: #606266;
}
</style>
