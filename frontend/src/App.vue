<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import { locales, saveLocale, type Locale } from './i18n';

const { locale, t } = useI18n({ useScope: 'global' });

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
      <el-menu router :default-active="$route.path" class="sidebar-menu">
        <el-menu-item index="/">{{ t('nav.dashboard') }}</el-menu-item>
        <el-menu-item index="/strategies">{{ t('nav.strategies') }}</el-menu-item>
        <el-menu-item index="/backtest">{{ t('nav.backtest') }}</el-menu-item>
        <el-menu-item index="/market">{{ t('nav.market') }}</el-menu-item>
        <el-menu-item index="/trades">{{ t('nav.trades') }}</el-menu-item>
        <el-menu-item index="/settings">{{ t('nav.settings') }}</el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <h1>{{ t('app.title') }}</h1>
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

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
}

.header h1 {
  margin: 0;
  font-size: 20px;
  color: #303133;
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
  padding: 24px;
}
</style>
