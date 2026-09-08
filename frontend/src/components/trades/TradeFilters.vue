<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import type { TradeFilters } from '@/utils/trades';
import { createTradeFilters } from '@/utils/trades';

interface Props {
  modelValue: TradeFilters;
  strategyOptions: string[];
  symbolOptions: string[];
  disabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  disabled: false,
});

const emit = defineEmits<{
  'update:modelValue': [filters: TradeFilters];
  clear: [];
}>();

const { t } = useI18n();

const clearDisabled = computed(() => (
  props.disabled
  || (!props.modelValue.strategy
    && !props.modelValue.symbol
    && !props.modelValue.side
    && !props.modelValue.search)
));

function updateFilter<Key extends keyof TradeFilters>(key: Key, value: TradeFilters[Key]): void {
  emit('update:modelValue', {
    ...props.modelValue,
    [key]: value,
  });
}

function clearFilters(): void {
  if (clearDisabled.value) return;
  emit('update:modelValue', createTradeFilters());
  emit('clear');
}
</script>

<template>
  <div class="trade-filters">
    <div class="trade-filters__grid">
      <div class="trade-filters__field">
        <label class="trade-filters__label" for="trade-filter-strategy">{{ t('trades.filters.strategy') }}</label>
        <el-select
          id="trade-filter-strategy"
          :model-value="props.modelValue.strategy"
          :placeholder="t('trades.filters.allStrategies')"
          :disabled="props.disabled"
          clearable
          filterable
          :aria-label="t('trades.filters.strategy')"
          @update:model-value="updateFilter('strategy', $event ?? '')"
        >
          <el-option :label="t('trades.filters.allStrategies')" value="" />
          <el-option v-for="option in props.strategyOptions" :key="option" :label="option" :value="option" />
        </el-select>
      </div>

      <div class="trade-filters__field">
        <label class="trade-filters__label" for="trade-filter-symbol">{{ t('trades.filters.symbol') }}</label>
        <el-select
          id="trade-filter-symbol"
          :model-value="props.modelValue.symbol"
          :placeholder="t('trades.filters.allSymbols')"
          :disabled="props.disabled"
          clearable
          filterable
          :aria-label="t('trades.filters.symbol')"
          @update:model-value="updateFilter('symbol', $event ?? '')"
        >
          <el-option :label="t('trades.filters.allSymbols')" value="" />
          <el-option v-for="option in props.symbolOptions" :key="option" :label="option" :value="option" />
        </el-select>
      </div>

      <div class="trade-filters__field">
        <label class="trade-filters__label" for="trade-filter-side">{{ t('trades.filters.side') }}</label>
        <el-select
          id="trade-filter-side"
          :model-value="props.modelValue.side"
          :placeholder="t('trades.filters.allSides')"
          :disabled="props.disabled"
          clearable
          :aria-label="t('trades.filters.side')"
          @update:model-value="updateFilter('side', $event ?? '')"
        >
          <el-option :label="t('trades.filters.allSides')" value="" />
          <el-option :label="t('trades.filters.buy')" value="buy" />
          <el-option :label="t('trades.filters.sell')" value="sell" />
        </el-select>
      </div>

      <div class="trade-filters__field trade-filters__field--search">
        <label class="trade-filters__label" for="trade-filter-search">{{ t('trades.filters.search') }}</label>
        <el-input
          id="trade-filter-search"
          :model-value="props.modelValue.search"
          :placeholder="t('trades.filters.searchPlaceholder')"
          :disabled="props.disabled"
          clearable
          :aria-label="t('trades.filters.search')"
          @update:model-value="updateFilter('search', String($event ?? ''))"
        />
      </div>
    </div>

    <div class="trade-filters__actions">
      <el-button :disabled="clearDisabled" @click="clearFilters">
        {{ t('trades.filters.clear') }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.trade-filters {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
}

.trade-filters__grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--ui-space-12);
}

.trade-filters__field {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-6);
  min-width: 0;
}

.trade-filters__field--search {
  grid-column: span 2;
}

.trade-filters__label {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.4;
  font-weight: 600;
}

.trade-filters__actions {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 1023px) {
  .trade-filters__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .trade-filters__field--search {
    grid-column: span 2;
  }
}

@media (max-width: 767px) {
  .trade-filters__grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .trade-filters__field--search {
    grid-column: span 1;
  }

  .trade-filters__actions {
    justify-content: stretch;
  }

  .trade-filters__actions :deep(.el-button) {
    width: 100%;
  }
}
</style>
