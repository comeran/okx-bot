<script setup lang="ts">
import type { Component } from 'vue';

interface Props {
  status: string;
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info';
  icon?: Component;
  showDot?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  tone: 'neutral',
  showDot: true,
});
</script>

<template>
  <span class="status-badge" :class="`status-badge--${props.tone}`" :data-tone="props.tone" role="status" :aria-label="props.status">
    <span v-if="props.icon" class="status-badge__indicator status-badge__indicator--icon" aria-hidden="true">
      <component :is="props.icon" class="status-badge__icon" />
    </span>
    <span v-else-if="props.showDot" class="status-badge__indicator status-badge__indicator--dot" aria-hidden="true" />
    <span class="status-badge__text">{{ props.status }}</span>
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--ui-space-8);
  padding: var(--ui-space-4) var(--ui-space-10);
  border: var(--ui-border-width-thin) solid transparent;
  border-radius: var(--ui-radius-pill);
  font-size: var(--ui-font-size-12);
  line-height: 1.4;
  font-weight: 600;
  white-space: nowrap;
}

.status-badge__indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
}

.status-badge__indicator--dot {
  width: var(--ui-space-8);
  height: var(--ui-space-8);
  border-radius: var(--ui-radius-pill);
}

.status-badge__icon {
  width: var(--ui-space-12);
  height: var(--ui-space-12);
}

.status-badge--neutral {
  border-color: var(--ui-color-border);
  background: var(--ui-color-info-soft);
  color: var(--ui-color-text-secondary);
}

.status-badge--neutral .status-badge__indicator--dot {
  background: var(--ui-color-info-light-3);
}

.status-badge--primary {
  border-color: color-mix(in srgb, var(--ui-color-primary) 28%, var(--ui-color-border));
  background: var(--ui-color-primary-soft);
  color: var(--ui-color-primary);
}

.status-badge--primary .status-badge__indicator--dot {
  background: var(--ui-color-primary);
}

.status-badge--success {
  border-color: color-mix(in srgb, var(--ui-color-success) 28%, var(--ui-color-border));
  background: var(--ui-color-success-soft);
  color: var(--ui-color-success);
}

.status-badge--success .status-badge__indicator--dot {
  background: var(--ui-color-success);
}

.status-badge--warning {
  border-color: color-mix(in srgb, var(--ui-color-warning) 28%, var(--ui-color-border));
  background: var(--ui-color-warning-soft);
  color: var(--ui-color-warning);
}

.status-badge--warning .status-badge__indicator--dot {
  background: var(--ui-color-warning);
}

.status-badge--danger {
  border-color: color-mix(in srgb, var(--ui-color-danger) 28%, var(--ui-color-border));
  background: var(--ui-color-danger-soft);
  color: var(--ui-color-danger);
}

.status-badge--danger .status-badge__indicator--dot {
  background: var(--ui-color-danger);
}

.status-badge--info {
  border-color: color-mix(in srgb, var(--ui-color-info) 28%, var(--ui-color-border));
  background: var(--ui-color-info-soft);
  color: var(--ui-color-info-dark-2);
}

.status-badge--info .status-badge__indicator--dot {
  background: var(--ui-color-info-dark-2);
}
</style>
