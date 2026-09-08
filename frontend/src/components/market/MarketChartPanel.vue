<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import Candlestick from '@/components/charts/Candlestick.vue';
import DataState from '@/components/ui/DataState.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import type { Kline } from '@/types/market';

interface Props {
  klines: Kline[];
  loading?: boolean;
  error?: string | null;
  rangeQuery?: boolean;
  stale?: boolean;
  symbol: string;
  timeframe: string;
  height?: number;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  error: null,
  rangeQuery: false,
  stale: false,
  height: 420,
});

const emit = defineEmits<{
  retry: [];
}>();

const { t } = useI18n();

const hasKlines = computed(() => props.klines.length > 0);
const emptyDescription = computed(() => (props.rangeQuery ? t('market.noCachedKlineData') : t('market.noKlineData')));

function retry() {
  emit('retry');
}
</script>

<template>
  <SectionCard :title="t('market.chartTitle')" :description="t('market.chartDescription')">
    <template #body>
      <DataState
        :loading="loading"
        :error="error"
        :empty="!hasKlines"
        :empty-description="emptyDescription"
        :stale="props.stale"
        @retry="retry"
      >
        <template #loading>
          <div
            class="market-chart-panel__frame market-chart-panel__frame--loading"
            data-testid="market-chart-frame"
            :style="{ minHeight: `${height}px` }"
          >
            <div class="market-chart-panel__placeholder" aria-live="polite">
              <span class="market-chart-panel__spinner" aria-hidden="true" />
              <span>{{ t('common.loading') }}</span>
            </div>
          </div>
        </template>

        <template #empty>
          <div
            class="market-chart-panel__frame market-chart-panel__frame--empty"
            data-testid="market-chart-frame"
            :style="{ minHeight: `${height}px` }"
          >
            <p class="market-chart-panel__message">{{ emptyDescription }}</p>
            <el-button type="primary" :aria-label="t('common.retry')" @click="retry">
              {{ t('common.retry') }}
            </el-button>
          </div>
        </template>

        <template #error="slotProps">
          <div
            class="market-chart-panel__frame market-chart-panel__frame--error"
            data-testid="market-chart-frame"
            :style="{ minHeight: `${height}px` }"
          >
            <p class="market-chart-panel__message">{{ slotProps.error }}</p>
            <el-button type="primary" :aria-label="t('common.retry')" @click="slotProps.retry">
              {{ t('common.retry') }}
            </el-button>
          </div>
        </template>

        <div
          v-if="hasKlines"
          class="market-chart-panel__frame"
          data-testid="market-chart-frame"
          :style="{ minHeight: `${height}px` }"
        >
          <Candlestick
            class="market-chart-panel__chart"
            data-testid="market-chart"
            :klines="klines"
            :symbol="symbol"
            :timeframe="timeframe"
            :height="height"
          />
        </div>
      </DataState>
    </template>
  </SectionCard>
</template>

<style scoped>
.market-chart-panel__frame {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--ui-space-12);
}

.market-chart-panel__frame--loading,
.market-chart-panel__frame--empty,
.market-chart-panel__frame--error {
  padding: var(--ui-space-16);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-surface);
}

.market-chart-panel__placeholder {
  display: inline-flex;
  align-items: center;
  gap: var(--ui-space-8);
  color: var(--ui-color-text-secondary);
}

.market-chart-panel__spinner {
  width: var(--ui-space-14);
  height: var(--ui-space-14);
  border-radius: var(--ui-radius-pill);
  border: 2px solid color-mix(in srgb, var(--ui-color-primary) 22%, var(--ui-color-border));
  border-top-color: var(--ui-color-primary);
  animation: market-chart-spin 0.85s linear infinite;
}

.market-chart-panel__message {
  margin: 0;
  color: var(--ui-color-text-secondary);
  line-height: 1.6;
}

.market-chart-panel__chart {
  width: 100%;
}

@keyframes market-chart-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
