<script setup lang="ts">
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';

import type { Locale } from '@/i18n';

interface Props {
  pageTitle: string;
  locale: Locale;
  availableLocales: readonly Locale[];
  menuExpanded?: boolean;
  mobileNavigationId?: string;
}

const props = withDefaults(defineProps<Props>(), {
  menuExpanded: false,
  mobileNavigationId: 'mobile-navigation',
});
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
        :aria-expanded="menuExpanded"
        :aria-controls="mobileNavigationId"
        @click="emit('menu-trigger')"
      >
        <span class="app-header__menu-icon" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      </button>
      <div class="app-header__titles">
        <span class="app-header__workspace">{{ t('app.workspace') }}</span>
        <span class="app-header__separator" aria-hidden="true">/</span>
        <span class="app-header__page-title">{{ pageTitle }}</span>
      </div>
    </div>

    <div class="app-header__actions">
      <div class="app-header__status">
        <slot name="status" />
      </div>
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
  min-height: 68px;
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
  flex: 1 1 auto;
  min-width: 0;
  gap: var(--ui-space-8);
}

.app-header__titles {
  display: flex;
  align-items: center;
  gap: var(--ui-space-10);
  min-width: 0;
  font-size: var(--ui-font-size-13);
}

.app-header__workspace,
.app-header__separator {
  flex: none;
  color: var(--ui-color-text-secondary);
}

.app-header__separator {
  color: var(--ui-color-text-muted);
}

.app-header__page-title {
  overflow: hidden;
  color: var(--ui-color-text);
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.app-header__menu-button {
  display: none;
  align-items: center;
  justify-content: center;
  width: max(44px, var(--ui-shell-mobile-menu-button-size));
  height: max(44px, var(--ui-shell-mobile-menu-button-size));
  padding: 0;
  color: var(--ui-color-text-secondary);
  cursor: pointer;
  background: var(--ui-color-surface);
  border: var(--ui-border-width-thin) solid var(--ui-color-border-subtle);
  border-radius: var(--ui-radius-8);
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
  flex: 0 1 auto;
  justify-content: flex-end;
  gap: var(--ui-space-12);
  min-width: 0;
}

.app-header__status {
  min-width: 0;
}

.app-header__locale-switcher {
  flex: none;
  gap: var(--ui-space-8);
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-shell-language-switcher-font-size);
}

.app-header__locale-select {
  width: var(--ui-shell-language-switcher-select-width-mobile);
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

@media (min-width: 768px) and (max-width: 1023px) {
  .app-header {
    flex-wrap: wrap;
    padding: var(--ui-space-12) var(--ui-space-20);
  }

  .app-header__actions {
    flex: 1 1 100%;
    justify-content: space-between;
  }
}

@media (max-width: 767px) {
  .app-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    min-height: var(--ui-shell-header-height-mobile);
    padding: var(--ui-shell-header-padding-mobile);
    gap: var(--ui-shell-header-gap-mobile);
  }

  .app-header__actions {
    display: contents;
  }

  .app-header__title-group {
    grid-column: 1;
    grid-row: 1;
    gap: var(--ui-space-6);
  }

  .app-header__titles {
    gap: var(--ui-space-6);
  }

  .app-header__workspace,
  .app-header__separator {
    display: none;
  }

  .app-header__status {
    grid-column: 1 / -1;
    grid-row: 2;
  }

  .app-header__locale-switcher {
    grid-column: 2;
    grid-row: 1;
  }

  .app-header__menu-button {
    display: inline-flex;
    flex: 0 0 auto;
  }
}
</style>
