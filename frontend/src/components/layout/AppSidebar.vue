<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute } from 'vue-router';

const navigationItems = [
  { route: '/', labelKey: 'nav.dashboard' },
  { route: '/strategies', labelKey: 'nav.strategies' },
  { route: '/backtest', labelKey: 'nav.backtest' },
  { route: '/market', labelKey: 'nav.market' },
  { route: '/trades', labelKey: 'nav.trades' },
  { route: '/settings', labelKey: 'nav.settings' },
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
    <div class="app-sidebar__brand">OKX Quant Bot</div>
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
        {{ t(item.labelKey) }}
      </el-menu-item>
    </el-menu>
  </nav>
</template>

<style scoped>
.app-sidebar {
  min-height: 100%;
  background: var(--ui-color-sidebar);
}

.app-sidebar__brand {
  display: flex;
  align-items: center;
  height: var(--ui-shell-brand-height);
  padding: 0 var(--ui-space-20);
  color: var(--ui-color-surface);
  font-size: var(--ui-shell-title-font-size);
  font-weight: 700;
}

.app-sidebar__menu {
  border-right: none;
}

.app-sidebar :deep(.el-menu) {
  background: transparent;
  border-right: none;
}

.app-sidebar :deep(.el-menu-item) {
  color: var(--ui-color-text-muted);
}

.app-sidebar :deep(.el-menu-item:hover),
.app-sidebar :deep(.el-menu-item:focus),
.app-sidebar :deep(.el-menu-item.is-active) {
  background: var(--ui-color-sidebar-active);
  color: var(--ui-color-surface);
}
</style>
