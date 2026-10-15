<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import SectionCard from '@/components/ui/SectionCard.vue';
import { fallbackSymbolsByType, formatMarketDateTime, limitOptions, marketTypeOptions, timeframeOptions } from '@/utils/market';

interface Props {
  marketType: string;
  symbol: string;
  timeframe: string;
  limit: number;
  startTime: Date | null;
  endTime: Date | null;
  marketTypeOptions?: readonly string[];
  symbolOptions: string[];
  timeframeOptions?: readonly string[];
  limitOptions?: readonly number[];
  loading?: boolean;
  tickersLoading?: boolean;
  chartQuery?: {
    marketType: string;
    symbol: string;
    timeframe: string;
    startTime: number | null;
    endTime: number | null;
  } | null;
}

const props = withDefaults(defineProps<Props>(), {
  marketTypeOptions: () => marketTypeOptions,
  timeframeOptions: () => timeframeOptions,
  limitOptions: () => limitOptions,
  loading: false,
  tickersLoading: false,
});

const emit = defineEmits<{
  submit: [payload: {
    marketType: string;
    symbol: string;
    timeframe: string;
    limit: number;
    startTime: Date | null;
    endTime: Date | null;
  }];
  'update:marketType': [value: string];
  'update:symbol': [value: string];
  'update:timeframe': [value: string];
  'update:limit': [value: number];
  'update:startTime': [value: Date | null];
  'update:endTime': [value: Date | null];
}>();

const { t, locale } = useI18n();

const marketTypeModel = computed({
  get: () => props.marketType,
  set: (value: string) => emit('update:marketType', value),
});

const symbolModel = computed({
  get: () => props.symbol,
  set: (value: string) => emit('update:symbol', value),
});

const timeframeModel = computed({
  get: () => props.timeframe,
  set: (value: string) => emit('update:timeframe', value),
});

const limitModel = computed({
  get: () => props.limit,
  set: (value: number) => emit('update:limit', value),
});

const startTimeModel = computed({
  get: () => props.startTime,
  set: (value: Date | null) => emit('update:startTime', value),
});

const endTimeModel = computed({
  get: () => props.endTime,
  set: (value: Date | null) => emit('update:endTime', value),
});

function handleSubmit() {
  emit('submit', {
    marketType: props.marketType,
    symbol: props.symbol.trim(),
    timeframe: props.timeframe,
    limit: props.limit,
    startTime: props.startTime,
    endTime: props.endTime,
  });
}

const activeQuery = computed(() => props.chartQuery);
const activeQueryRangeSummary = computed(() => {
  const chartQuery = props.chartQuery;
  if (!chartQuery) {
    return '';
  }

  if (chartQuery.startTime === null || chartQuery.startTime === undefined || chartQuery.endTime === null || chartQuery.endTime === undefined) {
    return t('market.latestCandles');
  }

  return t('market.rangeSummary', {
    start: formatMarketDateTime(chartQuery.startTime, locale.value),
    end: formatMarketDateTime(chartQuery.endTime, locale.value),
  });
});

const currentFallbackSymbols = computed(() => fallbackSymbolsByType[props.marketType] ?? fallbackSymbolsByType.spot);
</script>

<template>
  <SectionCard :title="t('market.queryTitle')" :description="t('market.queryDescription')">
    <template #body>
      <form
        class="market-query-panel"
        data-testid="market-query-form"
        @submit.prevent="handleSubmit"
      >
        <div class="market-query-panel__grid" data-testid="market-query-grid">
          <label class="market-query-panel__field">
            <span class="market-query-panel__label">{{ t('market.marketType') }}</span>
            <el-select
              v-model="marketTypeModel"
              :aria-label="t('market.marketType')"
              data-testid="market-market-type-select"
              :loading="tickersLoading"
              class="market-query-panel__control"
            >
              <el-option
                v-for="option in marketTypeOptions"
                :key="option"
                :label="t(`settings.marketTypes.${option}`)"
                :value="option"
              />
            </el-select>
          </label>

          <label class="market-query-panel__field">
            <span class="market-query-panel__label">{{ t('common.symbol') }}</span>
            <el-select
              v-model="symbolModel"
              filterable
              allow-create
              default-first-option
              :aria-label="t('common.symbol')"
              data-testid="market-symbol-select"
              :loading="tickersLoading"
              :placeholder="t('market.selectSymbol')"
              class="market-query-panel__control"
            >
              <el-option
                v-for="symbol in symbolOptions.length > 0 ? symbolOptions : currentFallbackSymbols"
                :key="symbol"
                :label="symbol"
                :value="symbol"
              />
            </el-select>
          </label>

          <label class="market-query-panel__field">
            <span class="market-query-panel__label">{{ t('common.timeframe') }}</span>
            <el-select
              v-model="timeframeModel"
              :aria-label="t('common.timeframe')"
              data-testid="market-timeframe-select"
              class="market-query-panel__control"
            >
              <el-option
                v-for="option in timeframeOptions"
                :key="option"
                :label="option"
                :value="option"
              />
            </el-select>
          </label>

          <label class="market-query-panel__field">
            <span class="market-query-panel__label">{{ t('market.startTime') }}</span>
            <el-date-picker
              v-model="startTimeModel"
              type="datetime"
              :aria-label="t('market.startTime')"
              data-testid="market-start-time-picker"
              :placeholder="t('market.selectStartTime')"
              class="market-query-panel__control"
            />
          </label>

          <label class="market-query-panel__field">
            <span class="market-query-panel__label">{{ t('market.endTime') }}</span>
            <el-date-picker
              v-model="endTimeModel"
              type="datetime"
              :aria-label="t('market.endTime')"
              data-testid="market-end-time-picker"
              :placeholder="t('market.selectEndTime')"
              class="market-query-panel__control"
            />
          </label>

          <label class="market-query-panel__field">
            <span class="market-query-panel__label">{{ t('common.limit') }}</span>
            <el-select
              v-model="limitModel"
              :aria-label="t('common.limit')"
              data-testid="market-limit-select"
              class="market-query-panel__control"
            >
              <el-option
                v-for="option in limitOptions"
                :key="option"
                :label="String(option)"
                :value="option"
              />
            </el-select>
          </label>

          <div class="market-query-panel__actions">
            <el-button
              type="primary"
              native-type="submit"
              :loading="loading"
              class="market-query-panel__submit"
              data-testid="market-query-submit"
            >
              {{ t('market.loadChart') }}
            </el-button>
          </div>
        </div>

        <section v-if="activeQuery" class="market-query-panel__summary" data-testid="market-active-query" aria-live="polite">
          <h3 class="market-query-panel__summary-title">{{ t('market.activeQuery') }}</h3>
          <dl class="market-query-panel__summary-grid">
            <div>
              <dt>{{ t('market.marketType') }}</dt>
              <dd>{{ t(`settings.marketTypes.${activeQuery.marketType}`) }}</dd>
            </div>
            <div>
              <dt>{{ t('common.symbol') }}</dt>
              <dd>{{ activeQuery.symbol }}</dd>
            </div>
            <div>
              <dt>{{ t('common.timeframe') }}</dt>
              <dd>{{ activeQuery.timeframe }}</dd>
            </div>
            <div>
              <dt>{{ t('market.range') }}</dt>
              <dd>{{ activeQueryRangeSummary }}</dd>
            </div>
          </dl>
        </section>
      </form>
    </template>
  </SectionCard>
</template>

<style scoped>
.market-query-panel {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
}

.market-query-panel__grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(220px, 1fr));
  gap: var(--ui-space-16);
  align-items: end;
}

.market-query-panel__field {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-8);
  min-width: 0;
}

.market-query-panel__label,
.market-query-panel__summary-title {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-14);
  line-height: 1.4;
  font-weight: 600;
}

.market-query-panel__control {
  width: 100%;
}

.market-query-panel__actions {
  display: flex;
  align-items: end;
}

.market-query-panel__submit {
  width: 100%;
  min-height: 40px;
}

.market-query-panel__summary {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-12);
  padding: var(--ui-space-16);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-info-soft);
}

.market-query-panel__summary-title {
  margin: 0;
  color: var(--ui-color-text);
}

.market-query-panel__summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--ui-space-12);
  margin: 0;
}

.market-query-panel__summary-grid dt {
  color: var(--ui-color-text-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.market-query-panel__summary-grid dd {
  margin: var(--ui-space-4) 0 0;
  color: var(--ui-color-text);
  font-weight: 600;
  word-break: break-word;
}

@media (max-width: 1024px) {
  .market-query-panel__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .market-query-panel__summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .market-query-panel__grid,
  .market-query-panel__summary-grid {
    grid-template-columns: 1fr;
  }

  .market-query-panel__actions,
  .market-query-panel__submit {
    width: 100%;
  }
}
</style>
