<script setup lang="ts">
import {
  computed,
  nextTick,
  onMounted,
  ref,
  watch,
} from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute } from 'vue-router';

import AppHeader from '@/components/layout/AppHeader.vue';
import AppSidebar from '@/components/layout/AppSidebar.vue';
import StatusBadge from '@/components/ui/StatusBadge.vue';
import { useWebSocket } from '@/composables/useWebSocket';
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
const strategies = useStrategiesStore();
const mobileNavigationOpen = ref(false);
const appHeaderRef = ref<InstanceType<typeof AppHeader> | null>(null);
const websocket = useWebSocket('/ws', {
  onMessage: (message) => {
    dashboard.addWebSocketMessage(message);
    strategies.applyWebSocketMessage(message as StrategyWebSocketMessage);
  },
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
  dashboard.websocketConnected ? t('common.connected') : t('common.disconnected')
));

const websocketStatusTone = computed(() => (
  dashboard.websocketConnected ? 'success' : 'neutral'
));

watch(websocket.connected, (connected) => {
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

async function restoreMenuFocus(): Promise<void> {
  await nextTick();
  appHeaderRef.value?.focusMenuButton();
}

onMounted(() => {
  websocket.connect();
});
</script>

<template>
  <el-container class="app-shell">
    <el-aside width="220px" class="app-shell__sidebar">
      <AppSidebar />
    </el-aside>

    <el-drawer
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
        @menu-trigger="openMobileNavigation"
      >
        <template #status>
          <StatusBadge :status="websocketStatusLabel" :tone="websocketStatusTone" />
        </template>
      </AppHeader>
      <el-main class="app-shell__content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  background: var(--ui-color-canvas);
}

.app-shell__sidebar {
  background: var(--ui-color-sidebar);
  border-right: var(--ui-border-width-thin) solid var(--ui-color-sidebar-active);
}

.app-shell__main {
  min-width: 0;
}

.app-shell__content {
  min-width: 0;
  width: 100%;
  max-width: var(--ui-content-max-width);
  margin: 0 auto;
  padding: var(--ui-space-24);
  overflow-x: hidden;
}

:deep(.app-shell__mobile-navigation .el-drawer__body) {
  padding: 0;
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
}
</style>
