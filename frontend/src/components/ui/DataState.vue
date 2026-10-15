<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

interface Props {
  loading?: boolean;
  error?: string | null;
  empty?: boolean;
  emptyDescription?: string;
  stale?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  error: null,
  empty: false,
  emptyDescription: '',
  stale: false,
});

const emit = defineEmits<{
  retry: [];
}>();

const { t } = useI18n();

const showLoading = computed(() => props.loading);
const showStaleBanner = computed(() => !props.loading && props.stale);
const showErrorState = computed(() => !props.loading && Boolean(props.error) && !props.stale);
const showEmptyState = computed(() => !props.loading && props.empty && (props.stale || !props.error));
const showDefaultContent = computed(() => !props.loading && !props.empty && (!props.error || props.stale));

function retry() {
  emit('retry');
}
</script>

<template>
  <div class="data-state">
    <template v-if="showLoading">
      <slot name="loading">
        <div class="data-state__panel data-state__panel--loading" role="status" aria-live="polite">
          <span class="data-state__spinner" aria-hidden="true" />
          <span class="data-state__text">{{ t('common.loading') }}</span>
        </div>
      </slot>
    </template>

    <template v-else>
      <div v-if="showStaleBanner" class="data-state__stale" role="status" aria-live="polite">
        <div class="data-state__panel data-state__panel--stale">
          <strong class="data-state__title">{{ t('common.stale') }}</strong>
          <p v-if="error" class="data-state__message">{{ error }}</p>
          <button class="data-state__retry" type="button" :aria-label="t('common.retry')" @click="retry">
            {{ t('common.retry') }}
          </button>
        </div>
      </div>

      <template v-if="showErrorState">
        <slot name="error" :error="error" :retry="retry">
          <div class="data-state__panel data-state__panel--error" role="alert">
            <strong class="data-state__title">{{ t('common.error') }}</strong>
            <p v-if="error" class="data-state__message">{{ error }}</p>
            <button class="data-state__retry" type="button" :aria-label="t('common.retry')" @click="retry">
              {{ t('common.retry') }}
            </button>
          </div>
        </slot>
      </template>

      <template v-else-if="showEmptyState">
        <slot name="empty">
          <div class="data-state__panel data-state__panel--empty" role="status" aria-live="polite">
            <strong class="data-state__title">{{ t('common.empty') }}</strong>
            <p v-if="emptyDescription" class="data-state__message">{{ emptyDescription }}</p>
          </div>
        </slot>
      </template>

      <slot v-if="showDefaultContent" />
    </template>
  </div>
</template>

<style scoped>
.data-state {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-12);
}

.data-state__panel {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--ui-space-8);
  padding: var(--ui-space-16);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-surface);
  color: var(--ui-color-text-secondary);
}

.data-state__panel--error {
  border-color: color-mix(in srgb, var(--ui-color-danger) 28%, var(--ui-color-border));
  background: var(--ui-color-danger-soft);
}

.data-state__panel--empty {
  background: var(--ui-color-info-soft);
}

.data-state__panel--stale {
  border-color: color-mix(in srgb, var(--ui-color-warning) 32%, var(--ui-color-border));
  background: var(--ui-color-warning-soft);
}

.data-state__panel--loading {
  flex-direction: row;
  align-items: center;
}

.data-state__title {
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-14);
  line-height: 1.5;
}

.data-state__message {
  margin: 0;
  line-height: 1.6;
}

.data-state__retry {
  appearance: none;
  border: var(--ui-border-width-thin) solid var(--ui-color-primary);
  border-radius: var(--ui-radius-4);
  background: var(--ui-color-primary-soft);
  color: var(--ui-color-primary);
  padding: var(--ui-space-6) var(--ui-space-12);
  cursor: pointer;
}

.data-state__retry:hover {
  background: color-mix(in srgb, var(--ui-color-primary-soft) 88%, white);
}

.data-state__spinner {
  width: var(--ui-space-14);
  height: var(--ui-space-14);
  border-radius: var(--ui-radius-pill);
  border: 2px solid color-mix(in srgb, var(--ui-color-primary) 22%, var(--ui-color-border));
  border-top-color: var(--ui-color-primary);
  animation: data-state-spin 0.85s linear infinite;
}

.data-state__text {
  color: var(--ui-color-text-secondary);
}

@keyframes data-state-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
