<script setup lang="ts">
import { computed, onMounted, watch } from 'vue';

import { useWebSocket } from '@/composables/useWebSocket';
import { useDashboardStore } from '@/stores/dashboard';
import type { DashboardFieldValue, Order, Position } from '@/types/dashboard';

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
      <h2>Dashboard</h2>
      <el-tag :type="dashboard.websocketConnected ? 'success' : 'danger'">
        WebSocket {{ dashboard.websocketConnected ? 'Connected' : 'Disconnected' }}
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
          <template #header>Total Equity</template>
          <div class="metric">{{ formatCurrency(dashboard.account?.equity) }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>Daily PnL</template>
          <div class="metric">{{ formatCurrency(dashboard.account?.daily_pnl) }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="hover" v-loading="dashboard.loading">
          <template #header>Active Strategies</template>
          <div class="metric">{{ dashboard.activeStrategyCount }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="dashboard-section">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header>Positions</template>
          <el-empty v-if="dashboard.positions.length === 0" description="No positions" />
          <el-table v-else :data="dashboard.positions" size="small">
            <el-table-column label="Position">
              <template #default="{ row }">{{ formatRecord(row) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header>Orders</template>
          <el-empty v-if="dashboard.orders.length === 0" description="No orders" />
          <el-table v-else :data="dashboard.orders" size="small">
            <el-table-column label="Order">
              <template #default="{ row }">{{ formatRecord(row) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="dashboard-section">
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header>Strategies</template>
          <el-empty v-if="dashboard.strategies.length === 0" description="No strategies" />
          <el-table v-else :data="dashboard.strategies" size="small">
            <el-table-column prop="name" label="Name" />
            <el-table-column prop="status" label="Status" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header>WebSocket Messages</template>
          <el-empty v-if="latestMessages.length === 0" description="No messages" />
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
