<script setup lang="ts">
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';

import type { Locale } from '@/i18n';

interface Props {
  pageTitle: string;
  locale: Locale;
  availableLocales: readonly Locale[];
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (event: 'menu-trigger'): void;
  (event: 'update:locale', value: Locale): void;
}>();

const { t } = useI18n({ useScope: 'global' });
const menuButtonRef = ref<HTMLButtonElement | null>(null);

function localeLabel(locale: Locale): string {
  return locale === 'zh-CN' ? '简体中文' : 'English';
}

function updateLocale(value: string): void {
  if ((props.availableLocales as readonly string[]).includes(value)) {
    emit('update:locale', value as Locale);
  }
}

function focusMenuButton(): void {
  menuButtonRef.value?.focus();
}

defineExpose({ focusMenuButton });
</script>

<template>
  <header class="app-header">
    <div class="app-header__title-group">
      <button
        ref="menuButtonRef"
        type="button"
        class="app-header__menu-button"
        :aria-label="t('app.mobileMenu')"
        @click="emit('menu-trigger')"
      >
        <span class="app-header__menu-icon" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      </button>
      <div class="app-header__titles">
        <span class="app-header__app-title">{{ t('app.title') }}</span>
        <h1 class="app-header__page-title">{{ pageTitle }}</h1>
      </div>
    </div>

    <div class="app-header__actions">
      <slot name="status" />
      <div class="app-header__locale-switcher">
        <span>{{ t('app.language') }}</span>
        <el-select
          :model-value="locale"
          class="app-header__locale-select"
          size="small"
          :aria-label="t('app.language')"
          @update:model-value="updateLocale"
        >
          <el-option
            v-for="availableLocale in availableLocales"
            :key="availableLocale"
            :label="localeLabel(availableLocale)"
            :value="availableLocale"
          />
        </el-select>
      </div>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 64px;
  gap: var(--ui-space-12);
  padding: 0 var(--ui-space-24);
  background: var(--ui-color-surface);
  border-bottom: var(--ui-border-width-thin) solid var(--ui-color-border);
}

.app-header__title-group,
.app-header__actions,
.app-header__locale-switcher {
  display: flex;
  align-items: center;
}

.app-header__title-group {
  min-width: 0;
  gap: var(--ui-space-8);
}

.app-header__titles {
  min-width: 0;
}

.app-header__app-title {
  display: block;
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-12);
  line-height: 1.2;
}

.app-header__page-title {
  margin: 0;
  color: var(--ui-color-text);
  font-size: var(--ui-shell-title-font-size);
  line-height: 1.25;
}

.app-header__menu-button {
  display: none;
  align-items: center;
  justify-content: center;
  width: var(--ui-shell-mobile-menu-button-size);
  height: var(--ui-shell-mobile-menu-button-size);
  padding: 0;
  color: var(--ui-color-text-secondary);
  cursor: pointer;
  background: var(--ui-color-surface);
  border: var(--ui-border-width-thin) solid var(--ui-color-border-subtle);
  border-radius: var(--ui-radius-4);
}

.app-header__menu-icon {
  display: grid;
  width: var(--ui-shell-mobile-menu-icon-size);
  gap: var(--ui-space-4);
}

.app-header__menu-icon span {
  display: block;
  height: var(--ui-shell-mobile-menu-icon-bar-height);
  background: currentColor;
  border-radius: var(--ui-radius-4);
}

.app-header__actions {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--ui-space-12);
}

.app-header__locale-switcher {
  gap: var(--ui-space-8);
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-shell-language-switcher-font-size);
}

.app-header__locale-select {
  width: var(--ui-shell-language-switcher-select-width);
}

@media (max-width: 767px) {
  .app-header {
    min-height: var(--ui-shell-header-height-mobile);
    padding: var(--ui-shell-header-padding-mobile);
    gap: var(--ui-shell-header-gap-mobile);
  }

  .app-header__page-title {
    overflow: hidden;
    font-size: var(--ui-shell-title-font-size-mobile);
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .app-header__menu-button {
    display: inline-flex;
    flex: 0 0 auto;
  }

  .app-header__locale-switcher > span {
    position: absolute;
    width: var(--ui-a11y-hidden-size);
    height: var(--ui-a11y-hidden-size);
    margin: var(--ui-a11y-hidden-offset);
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }

  .app-header__locale-select {
    width: var(--ui-shell-language-switcher-select-width-mobile);
  }
}
</style>
