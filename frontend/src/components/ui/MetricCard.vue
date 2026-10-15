<script setup lang="ts">
interface Props {
  label: string;
  value: string;
  delta?: string;
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger';
  loading?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  tone: 'neutral',
  loading: false,
});

const loadingPlaceholder = '—';
</script>

<template>
  <article class="metric-card" :class="`metric-card--${props.tone}`" :data-tone="props.tone" :aria-busy="props.loading || undefined">
    <div class="metric-card__label">{{ label }}</div>
    <div class="metric-card__value-row">
      <span class="metric-card__value" :class="{ 'metric-card__value--loading': props.loading }">
        {{ props.loading ? loadingPlaceholder : value }}
      </span>
      <span v-if="delta && !props.loading" class="metric-card__delta">{{ delta }}</span>
    </div>
  </article>
</template>

<style scoped>
.metric-card {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-8);
  padding: var(--ui-space-16);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-surface);
}

.metric-card__label {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.4;
}

.metric-card__value-row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--ui-space-8);
  min-height: 1.75rem;
}

.metric-card__value {
  display: inline-flex;
  align-items: baseline;
  min-width: 4ch;
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-28);
  line-height: 1.2;
  font-weight: 700;
}

.metric-card__value--loading {
  color: transparent;
  background: linear-gradient(90deg, var(--ui-color-border-subtle), var(--ui-color-info-soft), var(--ui-color-border-subtle));
  background-size: 200% 100%;
  border-radius: var(--ui-radius-4);
  animation: metric-card-pulse 1.2s ease-in-out infinite;
  min-width: 6ch;
}

.metric-card__delta {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.5;
  white-space: nowrap;
}

.metric-card--primary {
  border-color: color-mix(in srgb, var(--ui-color-primary) 28%, var(--ui-color-border));
}

.metric-card--success {
  border-color: color-mix(in srgb, var(--ui-color-success) 28%, var(--ui-color-border));
}

.metric-card--warning {
  border-color: color-mix(in srgb, var(--ui-color-warning) 28%, var(--ui-color-border));
}

.metric-card--danger {
  border-color: color-mix(in srgb, var(--ui-color-danger) 28%, var(--ui-color-border));
}

@keyframes metric-card-pulse {
  0%, 100% {
    background-position: 0% 50%;
  }

  50% {
    background-position: 100% 50%;
  }
}
</style>
