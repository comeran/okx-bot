<script setup lang="ts">
import { computed, getCurrentInstance, useSlots } from 'vue';
import { useI18n } from 'vue-i18n';

interface Props {
  scrollLabel?: string;
  scrollDescription?: string;
  loading?: boolean;
}

defineOptions({ inheritAttrs: false });

const props = withDefaults(defineProps<Props>(), {
  scrollLabel: '',
  scrollDescription: '',
  loading: false,
});

const { t } = useI18n();
const slots = useSlots();
const instance = getCurrentInstance();
const descriptionId = `responsive-table-description-${instance?.uid ?? 'table'}`;

const scrollLabel = computed(() => props.scrollLabel || t('common.scrollableTable'));
const scrollDescription = computed(() => props.scrollDescription || scrollLabel.value);
const tableSlotNames = () => Object.keys(slots).filter((name) => name !== 'default' && name !== 'empty' && name !== 'loading');
const viewportLoadingAttrs = computed(() => (props.loading
  ? {
      inert: true,
      'aria-hidden': true,
      style: {
        pointerEvents: 'none' as const,
      },
    }
  : {}));
</script>

<template>
  <div class="responsive-table">
    <div
      class="responsive-table__scroll-region"
      role="region"
      tabindex="0"
      :aria-label="scrollLabel"
      :aria-describedby="descriptionId"
      :aria-busy="props.loading || undefined"
      :style="{ 'overflow-x': 'auto' }"
    >
      <span :id="descriptionId" class="responsive-table__sr-only">{{ scrollDescription }}</span>
      <div
        class="responsive-table__viewport"
        :class="{ 'responsive-table__viewport--loading': props.loading }"
        v-bind="viewportLoadingAttrs"
      >
        <el-table v-bind="$attrs" class="responsive-table__table">
          <slot />
          <template v-for="name in tableSlotNames()" #[name]="slotProps">
            <slot :name="name" v-bind="slotProps ?? {}" />
          </template>
          <template #empty>
            <slot name="empty">
              <div class="responsive-table__state responsive-table__state--empty">{{ t('common.empty') }}</div>
            </slot>
          </template>
        </el-table>
      </div>
      <div
        v-if="props.loading"
        class="responsive-table__loading-overlay"
        role="status"
        aria-live="polite"
        :style="{ pointerEvents: 'auto', zIndex: 1 }"
      >
        <slot name="loading">
          <div class="responsive-table__state responsive-table__state--loading">{{ t('common.loading') }}</div>
        </slot>
      </div>
    </div>
  </div>
</template>

<style scoped>
.responsive-table {
  min-width: 0;
}

.responsive-table__scroll-region {
  position: relative;
  overflow-x: auto;
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-surface);
}

.responsive-table__viewport {
  min-width: 100%;
}


.responsive-table__table {
  min-width: 100%;
}

.responsive-table__loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--ui-space-16);
  background: color-mix(in srgb, var(--ui-color-surface) 84%, transparent);
  backdrop-filter: blur(var(--ui-blur-sm));
}

.responsive-table__state {
  padding: var(--ui-space-16);
  color: var(--ui-color-text-secondary);
}

.responsive-table__state--loading {
  border-radius: var(--ui-radius-8);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  background: var(--ui-color-surface);
  box-shadow: var(--ui-shadow-sm, 0 1px 2px rgb(0 0 0 / 0.08));
}

.responsive-table__sr-only {
  position: absolute;
  width: var(--ui-a11y-hidden-size);
  height: var(--ui-a11y-hidden-size);
  padding: 0;
  margin: var(--ui-a11y-hidden-offset);
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
