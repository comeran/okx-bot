<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { CircleCheckFilled, CircleCloseFilled } from '@element-plus/icons-vue';

import AccountOverview from '@/components/dashboard/AccountOverview.vue';
import DashboardActivity from '@/components/dashboard/DashboardActivity.vue';
import StrategyPerformanceTable from '@/components/dashboard/StrategyPerformanceTable.vue';
import AppPageHeader from '@/components/ui/AppPageHeader.vue';
import MetricCard from '@/components/ui/MetricCard.vue';
import StatusBadge from '@/components/ui/StatusBadge.vue';
import { useDashboardStore } from '@/stores/dashboard';
import { useStrategiesStore } from '@/stores/strategies';
import { formatRuntimeCurrency, formatRuntimeTime } from '@/utils/dashboard';
import { enrichStrategyPerformanceRows } from '@/utils/strategyPerformance';

const { t, locale } = useI18n();
const dashboard = useDashboardStore();
const strategies = useStrategiesStore();

const latestMessages = computed(() => dashboard.websocketMessages.slice(0, 5));
const recentOrders = computed(() => dashboard.orders.slice(0, 20));
const strategyPerformanceRows = computed(() => enrichStrategyPerformanceRows(
  strategies.runtimeSummaries,
  dashboard.strategyPerformance,
  dashboard.positions,
  dashboard.orders,
));
const lastUpdatedText = computed(() => formatRuntimeTime(dashboard.lastUpdatedAt ?? undefined, locale.value));
const headerLoading = computed(() => dashboard.loading || strategies.loadingInitial);

const accountOverviewHasVisibleData = computed(() => (dashboard.account?.assets?.length ?? 0) > 0);
const dashboardVisibleData = computed(() => (
  dashboard.account !== null
  || dashboard.positions.length > 0
  || dashboard.orders.length > 0
  || dashboard.tickers.length > 0
));
const accountOverviewError = computed(() => dashboard.accountError ?? dashboard.error);
const accountOverviewStale = computed(() => Boolean(accountOverviewHasVisibleData.value && (dashboard.accountError || dashboard.error)));
const dashboardHasVisibleData = computed(() => (
  dashboardVisibleData.value
  || strategyPerformanceRows.value.length > 0
  || latestMessages.value.length > 0
  || strategies.runtimeSummaries.length > 0
));
const dashboardErrorTone = computed(() => (dashboardHasVisibleData.value ? 'warning' : 'error'));
const activityLoading = computed(() => dashboard.loading || strategies.loadingInitial);
interface DashboardAlert {
  key: string;
  title: string;
  description?: string;
  type: 'error' | 'warning';
}

const dashboardStatusAlerts = computed<DashboardAlert[]>(() => {
  const alerts: DashboardAlert[] = [];

  if (dashboard.error) {
    alerts.push({
      key: 'dashboard-error',
      title: dashboardHasVisibleData.value ? t('common.stale') : dashboard.error,
      description: dashboardHasVisibleData.value ? dashboard.error : undefined,
      type: dashboardErrorTone.value,
    });
  }

  if (dashboard.accountError) {
    alerts.push({
      key: 'account-error',
      title: accountOverviewHasVisibleData.value ? t('common.stale') : dashboard.accountError,
      description: accountOverviewHasVisibleData.value ? dashboard.accountError : undefined,
      type: accountOverviewHasVisibleData.value ? 'warning' : 'error',
    });
  }

  if (dashboard.tickerError) {
    alerts.push({
      key: 'ticker-error',
      title: t('dashboard.tickerLoadError'),
      description: dashboard.tickerError,
      type: 'warning',
    });
  }

  return alerts;
});

const strategyPerformanceHasVisibleData = computed(() => strategyPerformanceRows.value.length > 0);
const strategyPerformanceStale = computed(() => Boolean(
  dashboard.strategyPerformanceError
  && strategyPerformanceHasVisibleData.value
  && !dashboard.strategyPerformanceLoading,
));

function refreshAll() {
  void Promise.all([
    dashboard.loadInitialData(),
    strategies.loadInitialData(),
  ]);
}

function retryAccountOverview() {
  void dashboard.refreshAccountOverview();
}

function retryStrategyPerformance() {
  void dashboard.refreshStrategyPerformance();
}

onMounted(() => {
  refreshAll();
});
</script>

<template>
  <section class="dashboard-view">
    <AppPageHeader
      :title="t('dashboard.title')"
      :description="`${t('dashboard.lastUpdated')}: ${lastUpdatedText}`"
    >
      <template #actions>
        <StatusBadge
          :status="dashboard.websocketConnected ? t('common.connected') : t('common.disconnected')"
          :tone="dashboard.websocketConnected ? 'success' : 'danger'"
          :icon="dashboard.websocketConnected ? CircleCheckFilled : CircleCloseFilled"
        />
        <el-button :loading="headerLoading" @click="refreshAll">
          {{ t('common.refresh') }}
        </el-button>
      </template>
    </AppPageHeader>

    <div v-if="dashboardStatusAlerts.length" class="dashboard-view__alerts">
      <el-alert
        v-for="alert in dashboardStatusAlerts"
        :key="alert.key"
        :title="alert.title"
        :description="alert.description"
        :type="alert.type"
        show-icon
        class="dashboard-view__alert"
      />
    </div>

    <el-row :gutter="16" class="dashboard-view__metrics">
      <el-col :xs="24" :sm="12" :lg="4">
        <MetricCard
          :label="t('dashboard.equity')"
          :value="formatRuntimeCurrency(dashboard.account?.equity)"
          tone="primary"
          :loading="dashboard.loading"
        />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <MetricCard
          :label="t('dashboard.cashBalance')"
          :value="formatRuntimeCurrency(dashboard.account?.cash_balance)"
          tone="neutral"
          :loading="dashboard.loading"
        />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <MetricCard
          :label="t('dashboard.realizedPnl')"
          :value="formatRuntimeCurrency(dashboard.account?.realized_pnl)"
          tone="success"
          :loading="dashboard.loading"
        />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <MetricCard
          :label="t('dashboard.dailyPnl')"
          :value="formatRuntimeCurrency(dashboard.account?.daily_pnl)"
          tone="warning"
          :loading="dashboard.loading"
        />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <MetricCard
          :label="t('dashboard.feesPaid')"
          :value="formatRuntimeCurrency(dashboard.account?.fees_paid)"
          tone="danger"
          :loading="dashboard.loading"
        />
      </el-col>
      <el-col :xs="24" :sm="12" :lg="4">
        <MetricCard
          :label="t('dashboard.activeStrategies')"
          :value="String(strategies.activeStrategyCount)"
          tone="primary"
          :loading="strategies.loadingInitial && strategies.runtimeSummaries.length === 0"
        />
      </el-col>
    </el-row>

    <div class="dashboard-view__section">
      <AccountOverview
        :assets="dashboard.account?.assets ?? []"
        :loading="dashboard.loading || dashboard.accountLoading"
        :error="dashboard.error"
        :account-error="dashboard.accountError"
        :stale="accountOverviewStale"
        @retry="retryAccountOverview"
      />
    </div>

    <div class="dashboard-view__section">
      <StrategyPerformanceTable
        :rows="strategyPerformanceRows"
        :loading="dashboard.strategyPerformanceLoading"
        :error="dashboard.strategyPerformanceError"
        :stale="strategyPerformanceStale"
        @retry="retryStrategyPerformance"
      />
    </div>

    <div class="dashboard-view__section">
      <DashboardActivity
        :recent-orders="recentOrders"
        :positions="dashboard.positions"
        :runtime-summaries="strategies.runtimeSummaries"
        :runtime-errors="strategies.errors"
        :websocket-messages="latestMessages"
        :loading="activityLoading"
      />
    </div>
  </section>
</template>

<style scoped>
.dashboard-view {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
  min-width: 0;
}

.dashboard-view__alerts {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-12);
}

.dashboard-view__alert {
  min-width: 0;
}

.dashboard-view__metrics {
  margin: 0;
}

.dashboard-view__section {
  min-width: 0;
}
</style>
