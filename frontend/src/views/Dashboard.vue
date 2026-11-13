<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { ElMessage, ElMessageBox } from 'element-plus';

import AccountOverview from '@/components/dashboard/AccountOverview.vue';
import AccountContextNotice from '@/components/dashboard/AccountContextNotice.vue';
import { useAccountContextStore } from '@/stores/accountContext';
import DashboardActivity from '@/components/dashboard/DashboardActivity.vue';
import StrategyPerformanceTable from '@/components/dashboard/StrategyPerformanceTable.vue';
import AppPageHeader from '@/components/ui/AppPageHeader.vue';
import MetricCard from '@/components/ui/MetricCard.vue';
import {
  cancelOpenOrders,
  fetchKillSwitch,
  fetchOpeningPause,
  flattenPositions,
  updateKillSwitch,
  updateOpeningPause,
} from '@/services/ops';
import { useDashboardStore } from '@/stores/dashboard';
import { useStrategiesStore } from '@/stores/strategies';
import { formatRuntimeCurrency, formatRuntimeTime } from '@/utils/dashboard';
import { enrichStrategyPerformanceRows } from '@/utils/strategyPerformance';

const { t, locale } = useI18n();
const dashboard = useDashboardStore();
const accountContext = useAccountContextStore();
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
const canSyncAccount = computed(() => {
  const context = accountContext.context;
  const auth = accountContext.authentication;
  return Boolean(context?.available && context.account_scope_id && context.environment === 'sandbox'
    && auth?.status === 'authenticated' && auth.account_scope_id === context.account_scope_id
    && auth.environment === context.environment && !accountContext.loading
    && !accountContext.authenticationChecking && !dashboard.loading && !dashboard.privateSyncLoading);
});
const privateSyncErrorText = computed(() => {
  const code = dashboard.privateSyncError;
  return code ? t(`dashboard.accountSync.errors.${code}`) : null;
});
const privateSyncSummary = computed(() => {
  const result = dashboard.privateSyncResult;
  return result ? t('dashboard.accountSync.completed', {
    accounts: result.account_upserted, orders: result.orders_upserted, trades: result.trades_upserted,
  }) : null;
});
const showSyncRetry = computed(() => Boolean(
  dashboard.privateSyncError || (dashboard.privateSyncResult && dashboard.error),
));

function syncAccount() {
  if (canSyncAccount.value) void dashboard.syncExchangeAccount();
}

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

  if (strategies.error) {
    const hasVisibleStrategies = strategies.runtimeSummaries.length > 0;
    alerts.push({
      key: 'strategies-error',
      title: hasVisibleStrategies ? t('common.stale') : t('dashboard.strategyLoadError'),
      description: hasVisibleStrategies ? strategies.error : undefined,
      type: hasVisibleStrategies ? 'warning' : 'error',
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
  if (dashboard.error) {
    void dashboard.loadInitialData();
    return;
  }
  void dashboard.refreshAccountOverview();
}

function retryStrategyPerformance() {
  void dashboard.refreshStrategyPerformance();
}

const killSwitchEngaged = ref(false);
const killSwitchSaving = ref(false);
const killSwitchStatus = ref('');

async function loadKillSwitch(): Promise<void> {
  try {
    const state = await fetchKillSwitch();
    killSwitchEngaged.value = state.engaged;
    killSwitchStatus.value = state.engaged ? t('dashboard.killSwitchEngaged') : '';
  } catch {
    killSwitchStatus.value = t('dashboard.killSwitchError');
  }
}

async function toggleKillSwitch(): Promise<void> {
  const nextEngaged = !killSwitchEngaged.value;
  try {
    await ElMessageBox.confirm(
      nextEngaged ? t('dashboard.engageKillSwitchConfirm') : t('dashboard.releaseKillSwitchConfirm'),
      t('dashboard.killSwitch'),
      {
        type: 'warning',
        confirmButtonText: t('common.confirm'),
        cancelButtonText: t('common.cancel'),
      },
    );
  } catch {
    return;
  }

  killSwitchSaving.value = true;
  try {
    const state = await updateKillSwitch(nextEngaged, t('dashboard.killSwitchReason'));
    killSwitchEngaged.value = state.engaged;
    killSwitchStatus.value = state.engaged ? t('dashboard.killSwitchEngaged') : '';
  } catch {
    ElMessage.error(t('dashboard.killSwitchError'));
  } finally {
    killSwitchSaving.value = false;
  }
}

const openingPaused = ref(false);
const openingPauseSaving = ref(false);
const openingPauseStatus = ref('');

async function loadOpeningPause(): Promise<void> {
  try {
    const state = await fetchOpeningPause();
    openingPaused.value = state.paused;
    openingPauseStatus.value = state.paused ? t('dashboard.openingPaused') : '';
  } catch {
    openingPauseStatus.value = t('dashboard.pauseOpeningError');
  }
}

async function toggleOpeningPause(): Promise<void> {
  const nextPaused = !openingPaused.value;
  try {
    await ElMessageBox.confirm(
      nextPaused ? t('dashboard.pauseOpeningConfirm') : t('dashboard.resumeOpeningConfirm'),
      t('dashboard.pauseOpening'),
      {
        type: 'warning',
        confirmButtonText: t('common.confirm'),
        cancelButtonText: t('common.cancel'),
      },
    );
  } catch {
    return;
  }

  openingPauseSaving.value = true;
  try {
    const state = await updateOpeningPause(nextPaused, t('dashboard.pauseOpeningReason'));
    openingPaused.value = state.paused;
    openingPauseStatus.value = state.paused ? t('dashboard.openingPaused') : '';
  } catch {
    ElMessage.error(t('dashboard.pauseOpeningError'));
  } finally {
    openingPauseSaving.value = false;
  }
}

const emergencySaving = ref(false);

function applyEmergencyState(result: { kill_switch: { engaged: boolean }; opening_pause: { paused: boolean } }): void {
  killSwitchEngaged.value = result.kill_switch.engaged;
  killSwitchStatus.value = result.kill_switch.engaged ? t('dashboard.killSwitchEngaged') : '';
  openingPaused.value = result.opening_pause.paused;
  openingPauseStatus.value = result.opening_pause.paused ? t('dashboard.openingPaused') : '';
}

async function confirmEmergency(messageKey: string, titleKey: string): Promise<boolean> {
  try {
    await ElMessageBox.confirm(t(messageKey), t(titleKey), {
      type: 'warning',
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
    });
    return true;
  } catch {
    return false;
  }
}

async function cancelOrders(): Promise<void> {
  if (!await confirmEmergency('dashboard.cancelOrdersConfirm', 'dashboard.cancelOrders')) {
    return;
  }
  emergencySaving.value = true;
  try {
    const result = await cancelOpenOrders();
    applyEmergencyState(result);
    ElMessage.success(t('dashboard.cancelOrdersDone', { count: result.cancelled.length }));
    if (result.failed.length > 0) {
      ElMessage.warning(t('dashboard.cancelOrdersPartial', { count: result.failed.length }));
    }
  } catch {
    ElMessage.error(t('dashboard.cancelOrdersError'));
  } finally {
    emergencySaving.value = false;
  }
}

async function flatten(): Promise<void> {
  if (!await confirmEmergency('dashboard.flattenPositionsConfirm', 'dashboard.flattenPositions')) {
    return;
  }
  emergencySaving.value = true;
  try {
    const result = await flattenPositions();
    applyEmergencyState(result);
    ElMessage.success(t('dashboard.flattenPositionsDone', {
      count: result.cancelled.length,
      closed: result.closed?.length ?? 0,
    }));
    const failed = result.failed.length + (result.close_failed?.length ?? 0);
    if (failed > 0) {
      ElMessage.warning(t('dashboard.flattenPositionsPartial', { count: failed }));
    }
  } catch {
    ElMessage.error(t('dashboard.flattenPositionsError'));
  } finally {
    emergencySaving.value = false;
  }
}

onMounted(() => {
  refreshAll();
  void loadKillSwitch();
  void loadOpeningPause();
});
</script>

<template>
  <section class="dashboard-view">
    <AppPageHeader
      :title="t('dashboard.title')"
      :description="`${t('dashboard.lastUpdated')}: ${lastUpdatedText}`"
    >
      <template #actions>
        <el-button :loading="headerLoading" @click="refreshAll">
          {{ t('common.refresh') }}
        </el-button>
        <el-button
          class="dashboard-view__kill-switch"
          :type="killSwitchEngaged ? 'danger' : 'warning'"
          :loading="killSwitchSaving"
          @click="toggleKillSwitch"
        >
          {{ killSwitchEngaged ? t('dashboard.releaseKillSwitch') : t('dashboard.engageKillSwitch') }}
        </el-button>
        <span v-if="killSwitchStatus" class="dashboard-view__kill-switch-status">{{ killSwitchStatus }}</span>
        <el-button
          class="dashboard-view__opening-pause"
          :type="openingPaused ? 'danger' : 'warning'"
          :loading="openingPauseSaving"
          @click="toggleOpeningPause"
        >
          {{ openingPaused ? t('dashboard.resumeOpening') : t('dashboard.pauseOpening') }}
        </el-button>
        <span v-if="openingPauseStatus" class="dashboard-view__opening-pause-status">{{ openingPauseStatus }}</span>
        <el-button
          class="dashboard-view__cancel-orders"
          type="danger"
          plain
          :loading="emergencySaving"
          @click="cancelOrders"
        >
          {{ t('dashboard.cancelOrders') }}
        </el-button>
        <el-button
          class="dashboard-view__flatten"
          type="danger"
          :loading="emergencySaving"
          @click="flatten"
        >
          {{ t('dashboard.flattenPositions') }}
        </el-button>
        <el-button v-if="showSyncRetry" type="primary" :loading="dashboard.privateSyncLoading" :disabled="!canSyncAccount" @click="syncAccount">
          {{ t('dashboard.accountSync.retry') }}
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

    <AccountContextNotice
      :context="accountContext.context"
      :loading="accountContext.loading"
      :error="accountContext.error"
      :missing-snapshot="dashboard.account === null
        && !dashboard.loading && !dashboard.accountLoading
        && !dashboard.error && !dashboard.accountError
        && !accountContext.loading && !accountContext.error"
    />

    <div class="dashboard-view__sync" role="status" aria-live="polite">
      <p>{{ t('dashboard.accountSync.description') }}</p>
      <p v-if="accountContext.context?.environment === 'live'">{{ t('dashboard.accountSync.demoOnly') }}</p>
      <p v-else-if="!canSyncAccount && !dashboard.privateSyncLoading && !dashboard.loading">{{ t('dashboard.accountSync.authRequired') }}</p>
      <p v-if="privateSyncErrorText" class="dashboard-view__sync-error">{{ privateSyncErrorText }}</p>
      <template v-if="dashboard.privateSyncResult">
        <p>{{ privateSyncSummary }}</p>
        <p v-if="dashboard.privateSyncResult.account_upserted === 0" class="dashboard-view__sync-warning">{{ t('dashboard.accountSync.accountUnchanged') }}</p>
        <p v-if="dashboard.privateSyncResult.trades_status === 'unavailable'" class="dashboard-view__sync-warning">{{ t('dashboard.accountSync.partial') }}</p>
        <p v-if="dashboard.privateSyncResult.kill_switch_engaged" class="dashboard-view__sync-warning">{{ t('dashboard.accountSync.riskEngaged') }}</p>
        <p v-else-if="dashboard.privateSyncResult.risk_events_saved > 0 || dashboard.privateSyncResult.divergences.length > 0" class="dashboard-view__sync-warning">{{ t('dashboard.accountSync.riskDetected') }}</p>
        <p v-if="dashboard.error">{{ t('dashboard.accountSync.refreshFailed') }}</p>
      </template>
    </div>

    <div class="dashboard-view__metrics">
      <MetricCard
        :label="t('dashboard.equity')"
        :value="formatRuntimeCurrency(dashboard.account?.equity)"
        tone="primary"
        :loading="dashboard.loading"
      />
      <MetricCard
        :label="t('dashboard.cashBalance')"
        :value="formatRuntimeCurrency(dashboard.account?.cash_balance)"
        tone="neutral"
        :loading="dashboard.loading"
      />
      <MetricCard
        :label="t('dashboard.realizedPnl')"
        :value="formatRuntimeCurrency(dashboard.account?.realized_pnl)"
        tone="success"
        :loading="dashboard.loading"
      />
      <MetricCard
        :label="t('dashboard.dailyPnl')"
        :value="formatRuntimeCurrency(dashboard.account?.daily_pnl)"
        tone="warning"
        :loading="dashboard.loading"
      />
      <MetricCard
        :label="t('dashboard.feesPaid')"
        :value="formatRuntimeCurrency(dashboard.account?.fees_paid)"
        tone="danger"
        :loading="dashboard.loading"
      />
      <MetricCard
        :label="t('dashboard.activeStrategies')"
        :value="strategies.error && strategies.runtimeSummaries.length === 0 ? '—' : String(strategies.activeStrategyCount)"
        tone="primary"
        :loading="strategies.loadingInitial && strategies.runtimeSummaries.length === 0"
      />
    </div>

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
.dashboard-view__sync {
  display: grid;
  gap: var(--ui-space-4);
  min-width: 0;
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.dashboard-view__sync p { margin: 0; }
.dashboard-view__sync-error { color: var(--ui-color-danger); }
.dashboard-view__sync-warning { color: var(--ui-color-warning); }

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
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: var(--ui-space-12);
}

@media (max-width: 1279px) {
  .dashboard-view__metrics {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .dashboard-view__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 359px) {
  .dashboard-view__metrics {
    grid-template-columns: minmax(0, 1fr);
  }
}

.dashboard-view__section {
  min-width: 0;
}
</style>
