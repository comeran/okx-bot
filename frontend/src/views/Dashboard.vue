<script setup lang="ts">
import { computed, onMounted, watch } from 'vue';
import { useI18n } from 'vue-i18n';

import { useWebSocket } from '@/composables/useWebSocket';
import { useDashboardStore } from '@/stores/dashboard';
import type { DashboardFieldValue, Order, Position } from '@/types/dashboard';

const { t } = useI18n();
const dashboard = useDashboardStore();
const websocket = useWebSocket('/ws', {
  onMessage: dashboard.addWebSocketMessage,
});

const formatCurrency = (value?: number) => {
  if (value === undefined) {
    return '--';
  }

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value);
};

const formatTickerPrice = (value?: number | string) => {
  if (value === undefined || value === '') {
    return '--';
  }

  const numberValue = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numberValue)) {
    return '--';
  }

  return numberValue.toLocaleString('en-US', { maximumFractionDigits: 4 });
};

const formatRecord = (record: Position | Order) => {
  const entries = Object.entries(record) as [string, DashboardFieldValue][];

  if (entries.length === 0) {
    return '--';
  }

  return entries.map(([key, value]) => `${key}: ${value ?? '--'}`).join(' · ');
};

const latestMessages = computed(() => dashboard.websocketMessages.slice(0, 5));

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
      <h2>{{ t('dashboard.title') }}</h2>
      <el-tag :type="dashboard.websocketConnected ? 'success' : 'danger'">
        WebSocket {{ dashboard.websocketConnected ? t('common.connected') : t('common.disconnected') }}
      </el-tag>
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
        <el-card shadow="never">
          <template #header>{{ t('dashboard.marketTickers') }}</template>
          <el-empty v-if="dashboard.tickers.length === 0" :description="t('dashboard.noMarketTickers')" />
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
        <el-card shadow="never">
          <template #header>{{ t('dashboard.positions') }}</template>
          <el-empty v-if="dashboard.positions.length === 0" :description="t('dashboard.noPositions')" />
          <el-table v-else :data="dashboard.positions" size="small">
            <el-table-column :label="t('dashboard.position')">
              <template #default="{ row }">{{ formatRecord(row) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header>{{ t('dashboard.orders') }}</template>
          <el-empty v-if="dashboard.orders.length === 0" :description="t('dashboard.noOrders')" />
          <el-table v-else :data="dashboard.orders" size="small">
            <el-table-column :label="t('dashboard.order')">
              <template #default="{ row }">{{ formatRecord(row) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="dashboard-section">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
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
          <ul v-else class="message-list">
            <li v-for="(message, index) in latestMessages" :key="index">
              {{ message.type }}
            </li>
          </ul>
        </el-card>
      </el-col>
    </el-row>
  </section>
</template>

<style scoped>
.dashboard-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
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

.message-list {
  margin: 0;
  padding-left: 18px;
  color: #606266;
}
</style>
