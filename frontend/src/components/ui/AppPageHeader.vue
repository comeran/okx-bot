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
      <h2 class="app-page-header__title">{{ title }}</h2>
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
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--ui-space-16);
  margin-bottom: var(--ui-space-20);
}

.app-page-header__copy {
  min-width: 0;
}

.app-page-header__title {
  margin: 0;
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-24);
  line-height: 1.2;
  font-weight: 700;
}

.app-page-header__description {
  margin: var(--ui-space-8) 0 0;
  color: var(--ui-color-text-secondary);
  line-height: 1.6;
}

.app-page-header__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--ui-space-12);
}

@media (max-width: 767px) {
  .app-page-header {
    flex-direction: column;
  }

  .app-page-header__actions {
    justify-content: flex-start;
  }
}
</style>
