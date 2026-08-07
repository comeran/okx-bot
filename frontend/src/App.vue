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

import { useWebSocket } from '@/composables/useWebSocket';
import { useDashboardStore } from '@/stores/dashboard';
import { useStrategiesStore } from '@/stores/strategies';
import type { StrategyWebSocketMessage } from '@/types/strategy';
import { locales, saveLocale, type Locale } from './i18n';

const { locale, t } = useI18n({ useScope: 'global' });
const route = useRoute();
const dashboard = useDashboardStore();
const strategies = useStrategiesStore();
const mobileNavigationOpen = ref(false);
const menuButtonRef = ref<HTMLButtonElement | null>(null);
const websocket = useWebSocket('/ws', {
  onMessage: (message) => {
    dashboard.addWebSocketMessage(message);
    strategies.applyWebSocketMessage(message as StrategyWebSocketMessage);
  },
});

watch(websocket.connected, (connected) => {
  dashboard.setWebSocketConnected(connected);
});

watch(() => route.path, () => {
  mobileNavigationOpen.value = false;
});

async function restoreMenuFocus(): Promise<void> {
  await nextTick();
  menuButtonRef.value?.focus();
}

onMounted(() => {
  websocket.connect();
});

const selectedLocale = computed({
  get: () => locale.value as Locale,
  set: (value: Locale) => {
    locale.value = value;
    saveLocale(value);
  },
});
</script>

<template>
  <el-container class="app-shell">
    <el-aside width="220px" class="sidebar">
      <div class="brand">OKX Quant Bot</div>
      <el-menu router :default-active="route.path" class="sidebar-menu">
        <el-menu-item index="/">{{ t('nav.dashboard') }}</el-menu-item>
        <el-menu-item index="/strategies">{{ t('nav.strategies') }}</el-menu-item>
        <el-menu-item index="/backtest">{{ t('nav.backtest') }}</el-menu-item>
        <el-menu-item index="/market">{{ t('nav.market') }}</el-menu-item>
        <el-menu-item index="/trades">{{ t('nav.trades') }}</el-menu-item>
        <el-menu-item index="/settings">{{ t('nav.settings') }}</el-menu-item>
      </el-menu>
    </el-aside>

    <el-drawer
      v-model="mobileNavigationOpen"
      direction="ltr"
      size="min(82vw, 320px)"
      class="mobile-navigation"
      :title="t('app.mobileMenu')"
      :aria-label="t('app.mobileMenu')"
      @closed="restoreMenuFocus"
    >
      <el-menu router :default-active="route.path" class="sidebar-menu">
        <el-menu-item index="/">{{ t('nav.dashboard') }}</el-menu-item>
        <el-menu-item index="/strategies">{{ t('nav.strategies') }}</el-menu-item>
        <el-menu-item index="/backtest">{{ t('nav.backtest') }}</el-menu-item>
        <el-menu-item index="/market">{{ t('nav.market') }}</el-menu-item>
        <el-menu-item index="/trades">{{ t('nav.trades') }}</el-menu-item>
        <el-menu-item index="/settings">{{ t('nav.settings') }}</el-menu-item>
      </el-menu>
    </el-drawer>

    <el-container class="main-shell">
      <el-header class="header">
        <div class="header__title">
          <button
            ref="menuButtonRef"
            type="button"
            class="mobile-menu-button"
            :aria-label="t('app.mobileMenu')"
            @click="mobileNavigationOpen = true"
          >
            <span class="mobile-menu-button__icon" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </button>
          <h1>{{ t('app.title') }}</h1>
        </div>
        <div class="language-switcher">
          <span>{{ t('app.language') }}</span>
          <el-select v-model="selectedLocale" class="language-switcher__select" size="small">
            <el-option
              v-for="availableLocale in locales"
              :key="availableLocale"
              :label="availableLocale === 'zh-CN' ? '简体中文' : 'English'"
              :value="availableLocale"
            />
          </el-select>
        </div>
      </el-header>
      <el-main class="content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  background: #f5f7fa;
}

.sidebar {
  background: #ffffff;
  border-right: 1px solid #e4e7ed;
}

.brand {
  height: 60px;
  display: flex;
  align-items: center;
  padding: 0 20px;
  font-size: 18px;
  font-weight: 700;
  color: #303133;
}

.sidebar-menu {
  border-right: none;
}

.main-shell {
  min-width: 0;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
}

.header__title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 10px;
}

.header h1 {
  margin: 0;
  font-size: 20px;
  color: #303133;
}

.mobile-menu-button {
  display: none;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  color: #606266;
  cursor: pointer;
  background: #ffffff;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
}

.mobile-menu-button__icon {
  display: grid;
  width: 18px;
  gap: 4px;
}

.mobile-menu-button__icon span {
  display: block;
  height: 2px;
  background: currentColor;
  border-radius: 1px;
}

.language-switcher {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #606266;
  font-size: 14px;
}

.language-switcher__select {
  width: 120px;
}

.content {
  min-width: 0;
  padding: 24px;
  overflow-x: hidden;
}

:deep(.mobile-navigation .el-drawer__body) {
  padding: 0;
}

@media (max-width: 767px) {
  .sidebar {
    display: none;
  }

  .header {
    height: auto;
    min-height: 60px;
    padding: 10px 12px;
    gap: 10px;
  }

  .header h1 {
    overflow: hidden;
    font-size: 17px;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .mobile-menu-button {
    display: inline-flex;
    flex: 0 0 auto;
  }

  .language-switcher > span {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }

  .language-switcher__select {
    width: 104px;
  }

  .content {
    padding: 16px 12px;
  }
}
</style>
