<script setup lang="ts">
import { useSlots } from 'vue';

interface Props {
  title: string;
  description?: string;
}

defineProps<Props>();

const slots = useSlots();
</script>

<template>
  <header class="app-page-header">
    <div class="app-page-header__copy">
      <h1 class="app-page-header__title">{{ title }}</h1>
      <p v-if="description" class="app-page-header__description">{{ description }}</p>
    </div>
    <div v-if="slots.actions" class="app-page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.app-page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--ui-space-12) var(--ui-space-16);
  min-width: 0;
}

.app-page-header__copy {
  flex: 1 1 240px;
  min-width: 0;
}

.app-page-header__title {
  margin: 0;
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-24);
  line-height: 1.3;
  font-weight: 700;
  letter-spacing: -0.025em;
  overflow-wrap: anywhere;
}

.app-page-header__description {
  max-width: 76ch;
  margin: var(--ui-space-6) 0 0;
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.app-page-header__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: var(--ui-space-8);
  min-width: 0;
  max-width: 100%;
}

@media (max-width: 767px) {
  .app-page-header {
    align-items: stretch;
    flex-direction: column;
  }

  .app-page-header__copy {
    flex-basis: auto;
  }

  .app-page-header__actions {
    justify-content: flex-start;
    width: 100%;
  }
}
</style>
