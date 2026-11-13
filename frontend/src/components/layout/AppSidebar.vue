<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute } from 'vue-router';
import { DataAnalysis, Histogram, Monitor, Operation, Setting, Tickets, TrendCharts } from '@element-plus/icons-vue';

const navigationItems = [
  { route: '/', labelKey: 'nav.dashboard', icon: Monitor },
  { route: '/strategies', labelKey: 'nav.strategies', icon: Operation },
  { route: '/backtest', labelKey: 'nav.backtest', icon: Histogram },
  { route: '/market', labelKey: 'nav.market', icon: TrendCharts },
  { route: '/trades', labelKey: 'nav.trades', icon: Tickets },
  { route: '/settings', labelKey: 'nav.settings', icon: Setting },
] as const;

type NavigationItem = (typeof navigationItems)[number];

const emit = defineEmits<{
  (event: 'navigate', route: string): void;
}>();

const route = useRoute();
const { t } = useI18n({ useScope: 'global' });
const activeRoute = computed(() => route.path);

function isActive(item: NavigationItem): boolean {
  return activeRoute.value === item.route;
}

function handleSelect(index: string): void {
  emit('navigate', index);
}
</script>

<template>
  <nav class="app-sidebar" :aria-label="t('app.primaryNavigation')">
    <div class="app-sidebar__brand">
      <span class="app-sidebar__brand-mark" aria-hidden="true"><DataAnalysis /></span>
      <span>OKX Quant Bot</span>
    </div>
    <el-menu
      router
      :default-active="activeRoute"
      class="app-sidebar__menu"
      @select="handleSelect"
    >
      <el-menu-item
        v-for="item in navigationItems"
        :key="item.route"
        :index="item.route"
        :class="{ 'is-active': isActive(item) }"
        :aria-current="isActive(item) ? 'page' : undefined"
      >
        <component :is="item.icon" class="app-sidebar__icon" aria-hidden="true" />
        <span>{{ t(item.labelKey) }}</span>
      </el-menu-item>
    </el-menu>
  </nav>
</template>

<style scoped>
.app-sidebar {
  box-sizing: border-box;
  min-height: 100%;
  padding: 0 var(--ui-space-12) var(--ui-space-20);
  background: var(--ui-color-sidebar);
}

.app-sidebar__brand {
  display: flex;
  align-items: center;
  min-height: 68px;
  gap: var(--ui-space-10);
  padding: 0 var(--ui-space-8);
  color: var(--ui-color-surface);
  font-size: var(--ui-font-size-14);
  font-weight: 700;
  letter-spacing: 0.01em;
}

.app-sidebar__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 32px;
  height: 32px;
  color: var(--ui-color-surface);
  background: var(--ui-color-primary);
  border: var(--ui-border-width-thin) solid color-mix(in srgb, var(--ui-color-surface) 16%, transparent);
  border-radius: var(--ui-radius-8);
}

.app-sidebar__brand-mark svg {
  width: var(--ui-space-20);
  height: var(--ui-space-20);
}

.app-sidebar__menu {
  border-right: none;
  --el-menu-base-level-padding: 14px;
}

.app-sidebar :deep(.el-menu) {
  background: transparent;
  border-right: none;
}

.app-sidebar :deep(.el-menu-item) {
  position: relative;
  height: 46px;
  margin-bottom: var(--ui-space-6);
  gap: var(--ui-space-12);
  color: var(--ui-color-sidebar-text, #bfd0ca);
  border-radius: var(--ui-radius-8);
  font-size: var(--ui-font-size-13);
  line-height: 1.4;
}

.app-sidebar__icon {
  flex: none;
  width: var(--ui-space-20);
  height: var(--ui-space-20);
}

.app-sidebar :deep(.el-menu-item:hover),
.app-sidebar :deep(.el-menu-item:focus),
.app-sidebar :deep(.el-menu-item.is-active) {
  background: var(--ui-color-sidebar-active);
  color: var(--ui-color-surface);
}

.app-sidebar :deep(.el-menu-item.is-active) {
  font-weight: 600;
}

.app-sidebar :deep(.el-menu-item.is-active)::before {
  position: absolute;
  top: var(--ui-space-12);
  bottom: var(--ui-space-12);
  left: 0;
  width: 3px;
  background: var(--ui-color-sidebar-accent, #76d6c4);
  border-radius: var(--ui-radius-4);
  content: '';
}

.app-sidebar :deep(.el-menu-item.is-active) .app-sidebar__icon {
  color: var(--ui-color-sidebar-accent, #76d6c4);
}

.app-sidebar :deep(.el-menu-item:focus-visible) {
  outline: 2px solid var(--ui-color-sidebar-accent, #76d6c4);
  outline-offset: -2px;
}
</style>
