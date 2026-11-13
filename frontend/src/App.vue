<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute } from 'vue-router';

import AppHeader from '@/components/layout/AppHeader.vue';
import AppSidebar from '@/components/layout/AppSidebar.vue';
import ExchangeAuthBadge from '@/components/dashboard/ExchangeAuthBadge.vue';
import ExchangeAuthNotice from '@/components/dashboard/ExchangeAuthNotice.vue';
import StatusBadge from '@/components/ui/StatusBadge.vue';
import { useWebSocket } from '@/composables/useWebSocket';
import { useAccountContextStore } from '@/stores/accountContext';
import type { DashboardWebSocketMessage } from '@/types/dashboard';
import { useDashboardStore } from '@/stores/dashboard';
import { useStrategiesStore } from '@/stores/strategies';
import type { StrategyWebSocketMessage } from '@/types/strategy';
import { locales, saveLocale, type Locale } from './i18n';

const pageTitleKeys = {
  dashboard: 'nav.dashboard',
  strategies: 'nav.strategies',
  backtest: 'nav.backtest',
  market: 'nav.market',
  trades: 'nav.trades',
  settings: 'nav.settings',
} as const;

type PageRouteName = keyof typeof pageTitleKeys;

function isPageRouteName(name: unknown): name is PageRouteName {
  return typeof name === 'string' && name in pageTitleKeys;
}

const { locale, t } = useI18n({ useScope: 'global' });
const route = useRoute();
const dashboard = useDashboardStore();
const accountContext = useAccountContextStore();
const strategies = useStrategiesStore();
const mobileNavigationOpen = ref(false);
const appHeaderRef = ref<InstanceType<typeof AppHeader> | null>(null);
const mainContentRef = ref<HTMLElement | null>(null);
let reconnectTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
let disposed = false;
function cancelReconnect(): void {
  if (reconnectTimer !== null) globalThis.clearTimeout(reconnectTimer);
  reconnectTimer = null;
}

async function reloadAccount(): Promise<void> {
  cancelReconnect();
  const request = accountContext.reload();
  const epoch = accountContext.epoch;
  const context = await request;
  if (!disposed && context && accountContext.epoch === epoch) await dashboard.loadInitialData();
}

function scheduleAccountReconnect(): void {
  accountContext.invalidate();
  const epoch = accountContext.epoch;
  reconnectTimer = globalThis.setTimeout(() => {
    reconnectTimer = null;
    if (!disposed && accountContext.epoch === epoch) void reloadAccount();
  }, 3000);
}

function handleControl(message: DashboardWebSocketMessage): boolean {
  if (message.type !== 'context_changed') return false;
  void reloadAccount();
  return true;
}

const exchangeWebSocket = useWebSocket(() => {
  const context = accountContext.context;
  return context?.available && context.account_scope_id
    ? `/ws?scope=exchange&expected_account_scope_id=${encodeURIComponent(context.account_scope_id)}`
    : null;
}, {
  reconnect: false,
  onClose: scheduleAccountReconnect,
  onMessage: (message) => {
    if (!handleControl(message)) dashboard.addWebSocketMessage(message);
  },
});
const strategyWebSocket = useWebSocket('/ws?scope=strategy&strategy=*', {
  onMessage: (message) => {
    if (handleControl(message)) return;
    dashboard.addWebSocketMessage(message);
    strategies.applyWebSocketMessage(message as StrategyWebSocketMessage);
  },
});
watch(() => accountContext.epoch, () => {
  cancelReconnect();
  exchangeWebSocket.disconnect();
}, { flush: 'sync' });
watch(() => accountContext.context, (context) => {
  if (context?.available && context.account_scope_id) {
    exchangeWebSocket.connect();
    void accountContext.checkAuthentication();
  }
}, { immediate: true });
watch(
  () => [
    accountContext.authentication?.status,
    accountContext.authentication?.account_scope_id,
    accountContext.authentication?.checked_at,
  ] as const,
  ([status, scopeId]) => {
    const context = accountContext.context;
    if (status !== 'authenticated' || !scopeId || !context?.available
      || context.account_scope_id !== scopeId || context.environment !== 'sandbox'
      || accountContext.loading || accountContext.authenticationChecking) {
      return;
    }
    void dashboard.autoSyncExchangeAccount();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  disposed = true;
  cancelReconnect();
});

const selectedLocale = computed({
  get: () => locale.value as Locale,
  set: (value: Locale) => {
    locale.value = value;
    saveLocale(value);
  },
});

const pageTitle = computed(() => (
  isPageRouteName(route.name) ? t(pageTitleKeys[route.name]) : t('app.title')
));

const websocketStatusLabel = computed(() => (
  strategyWebSocket.connected.value ? t('exchangeAuth.serviceConnected') : t('exchangeAuth.serviceDisconnected')
));

const websocketStatusTone = computed(() => (
  strategyWebSocket.connected.value ? 'success' : 'neutral'
));

watch(exchangeWebSocket.connected, (connected) => {
  dashboard.setWebSocketConnected(connected);
});

watch(() => route.fullPath, () => {
  mobileNavigationOpen.value = false;
});

function openMobileNavigation(): void {
  mobileNavigationOpen.value = true;
}

function closeMobileNavigation(): void {
  mobileNavigationOpen.value = false;
}

function focusMainContent(): void {
  mainContentRef.value?.focus();
}

async function restoreMenuFocus(): Promise<void> {
  await nextTick();
  appHeaderRef.value?.focusMenuButton();
}

onMounted(() => {
  void accountContext.ensure();
  strategyWebSocket.connect();
});
</script>

<template>
  <a class="app-shell__skip-link" href="#main-content" @click.prevent="focusMainContent">
    {{ t('app.skipToContent') }}
  </a>
  <el-container class="app-shell">
    <el-aside width="220px" class="app-shell__sidebar">
      <AppSidebar />
    </el-aside>

    <el-drawer
      id="mobile-navigation"
      v-model="mobileNavigationOpen"
      direction="ltr"
      size="min(82vw, 320px)"
      class="app-shell__mobile-navigation"
      :title="t('app.mobileMenu')"
      :aria-label="t('app.mobileMenu')"
      @closed="restoreMenuFocus"
    >
      <AppSidebar @navigate="closeMobileNavigation" />
    </el-drawer>

    <el-container
      class="app-shell__main"
      direction="vertical"
      style="flex-direction: column;"
    >
      <AppHeader
        ref="appHeaderRef"
        v-model:locale="selectedLocale"
        :available-locales="locales"
        :page-title="pageTitle"
        :menu-expanded="mobileNavigationOpen"
        mobile-navigation-id="mobile-navigation"
        @menu-trigger="openMobileNavigation"
      >
        <template #status>
          <div class="app-shell__connection-status">
            <StatusBadge :status="websocketStatusLabel" :tone="websocketStatusTone" />
            <ExchangeAuthBadge :context="accountContext.context" :authentication="accountContext.authentication" :checking="accountContext.authenticationChecking" />
          </div>
        </template>
      </AppHeader>
      <main id="main-content" ref="mainContentRef" class="app-shell__content" :tabindex="-1">
        <ExchangeAuthNotice :authentication="accountContext.authentication" />
        <router-view />
      </main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--ui-color-canvas);
}

.app-shell__skip-link {
  position: fixed;
  top: var(--ui-space-12);
  left: var(--ui-space-12);
  z-index: 3000;
  padding: var(--ui-space-12) var(--ui-space-16);
  color: var(--ui-color-surface);
  background: var(--ui-color-primary);
  border-radius: var(--ui-radius-8);
  transform: translateY(calc(-100% - var(--ui-space-24)));
}

.app-shell__skip-link:focus {
  transform: translateY(0);
}

.app-shell__sidebar {
  position: sticky;
  top: 0;
  flex: 0 0 220px;
  height: 100vh;
  height: 100dvh;
  background: var(--ui-color-sidebar);
  border-right: var(--ui-border-width-thin) solid var(--ui-color-sidebar-active);
  overflow: auto;
}

.app-shell__main {
  min-width: 0;
}

.app-shell__content {
  box-sizing: border-box;
  flex: 1;
  min-width: 0;
  width: 100%;
  max-width: var(--ui-content-max-width);
  margin: 0 auto;
  padding: var(--ui-space-24);
  outline-offset: calc(-1 * var(--ui-space-4));
}

.app-shell__connection-status {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: var(--ui-space-6);
  min-width: 0;
}

:deep(.app-shell__mobile-navigation .el-drawer__body) {
  padding: 0;
}

:deep(.app-shell__mobile-navigation) {
  background: var(--ui-color-sidebar);
}

:deep(.app-shell__mobile-navigation .el-drawer__header) {
  margin: 0;
  padding: var(--ui-space-16) var(--ui-space-20);
  color: var(--ui-color-surface);
  border-bottom: var(--ui-border-width-thin) solid var(--ui-color-sidebar-active);
}

:deep(.app-shell__mobile-navigation .el-drawer__close-btn) {
  min-width: 44px;
  min-height: 44px;
  color: var(--ui-color-surface);
}

@media (max-width: 1024px) {
  .app-shell__content {
    padding: var(--ui-space-16);
  }
}

@media (max-width: 767px) {
  .app-shell__sidebar {
    display: none;
  }

  .app-shell__content {
    padding: var(--ui-space-12);
  }

  .app-shell__connection-status {
    justify-content: flex-start;
  }
}
</style>
