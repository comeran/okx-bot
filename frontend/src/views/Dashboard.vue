<script setup lang="ts">
import { computed, onMounted, watch } from 'vue';
import { useI18n } from 'vue-i18n';

import { useWebSocket } from '@/composables/useWebSocket';
import { useDashboardStore } from '@/stores/dashboard';
import type { DashboardWebSocketMessage } from '@/types/dashboard';

const { t } = useI18n();
const dashboard = useDashboardStore();
const websocket = useWebSocket('/ws', {
  onMessage: dashboard.addWebSocketMessage,
});

const emptyValue = '—';

const formatCurrency = (value?: number) => {
  if (value === undefined || !Number.isFinite(value)) {
    return emptyValue;
  }

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value);
};

const formatTickerPrice = (value?: number | string) => {
  if (value === undefined || value === '') {
    return emptyValue;
  }

  const numberValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numberValue)) {
    return emptyValue;
  }

  return numberValue.toLocaleString('en-US', { maximumFractionDigits: 4 });
};

const formatNumber = (value?: number) => {
  if (value === undefined || !Number.isFinite(value)) {
    return emptyValue;
  }

  return value.toLocaleString('en-US', { maximumFractionDigits: 8 });
};

const formatText = (value?: string) => value || emptyValue;

const formatTime = (timestamp?: number) => {
  if (timestamp === undefined || !Number.isFinite(timestamp)) {
    return emptyValue;
  }

  return new Date(timestamp).toLocaleString();
};

const formatPayloadPreview = (message: DashboardWebSocketMessage) => {
  const { type, received_at, ...payload } = message;

  if (Object.keys(payload).length === 0) {
    return emptyValue;
  }

  const preview = JSON.stringify(payload);
  return preview.length > 120 ? `${preview.slice(0, 120)}…` : preview;
};

const latestMessages = computed(() => dashboard.websocketMessages.slice(0, 5));
const lastUpdatedText = computed(() => formatTime(dashboard.lastUpdatedAt ?? undefined));

watch(websocket.connected, (connected) => {
  dashboard.setWebSocketConnected(connected);
});

onMounted(() => {
  void dashboard.loadInitialData();
  websocket.connect();
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
      <el-col :xs="24" :md="8">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.totalEquity') }}</template>
          <div class="metric">{{ formatCurrency(dashboard.account?.equity) }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.dailyPnl') }}</template>
          <div class="metric">{{ formatCurrency(dashboard.account?.daily_pnl) }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>{{ t('dashboard.activeStrategies') }}</template>
          <div class="metric">{{ dashboard.activeStrategyCount }}</div>
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
              <template #default="{ row }">{{ formatText(row.symbol) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.side')" min-width="80">
              <template #default="{ row }">{{ formatText(row.side) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.size')" min-width="100">
              <template #default="{ row }">{{ formatNumber(row.amount) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.entryPrice')" min-width="110">
              <template #default="{ row }">{{ formatNumber(row.entry_price) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.markPrice')" min-width="110">
              <template #default="{ row }">{{ formatNumber(row.mark_price) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.unrealizedPnl')" min-width="130">
              <template #default="{ row }">{{ formatCurrency(row.unrealized_pnl) }}</template>
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
              <template #default="{ row }">{{ formatText(row.symbol) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.side')" min-width="80">
              <template #default="{ row }">{{ formatText(row.side) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.orderType')" min-width="90">
              <template #default="{ row }">{{ formatText(row.type) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.price')" min-width="100">
              <template #default="{ row }">{{ formatNumber(row.price) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.amount')" min-width="100">
              <template #default="{ row }">{{ formatNumber(row.amount) }}</template>
            </el-table-column>
            <el-table-column :label="t('common.status')" min-width="100">
              <template #default="{ row }">{{ formatText(row.status) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.timestamp')" min-width="160">
              <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
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
            <el-table-column prop="name" :label="t('common.name')" />
            <el-table-column prop="status" :label="t('common.status')" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header>{{ t('dashboard.websocketMessages') }}</template>
          <el-empty v-if="latestMessages.length === 0" :description="t('dashboard.noMessages')" />
          <el-table v-else :data="latestMessages" size="small">
            <el-table-column :label="t('dashboard.messageType')" min-width="100">
              <template #default="{ row }">{{ row.type }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.messageReceived')" min-width="160">
              <template #default="{ row }">{{ formatTime(row.received_at) }}</template>
            </el-table-column>
            <el-table-column :label="t('dashboard.messagePayload')" min-width="220">
              <template #default="{ row }">{{ formatPayloadPreview(row) }}</template>
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
</style>
