<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import type { TradeSummary } from '@/utils/trades';
import { formatTradeNumber } from '@/utils/trades';

interface Props {
  summary: TradeSummary;
}

const props = defineProps<Props>();
const { t, locale } = useI18n();
const currentLocale = computed(() => locale.value);
</script>

<template>
  <dl class="trade-summary">
    <div class="trade-summary__item">
      <dt class="trade-summary__label">{{ t('trades.summary.totalTrades') }}</dt>
      <dd class="trade-summary__value">{{ props.summary.totalTrades }}</dd>
    </div>

    <div class="trade-summary__item">
      <dt class="trade-summary__label">{{ t('trades.summary.totalNotional') }}</dt>
      <dd class="trade-summary__value">{{ formatTradeNumber(props.summary.totalNotional, currentLocale) }}</dd>
    </div>

    <div class="trade-summary__item">
      <dt class="trade-summary__label">{{ t('trades.summary.totalFees') }}</dt>
      <dd class="trade-summary__value">{{ formatTradeNumber(props.summary.totalFees, currentLocale) }}</dd>
    </div>

    <div class="trade-summary__item trade-summary__item--positive">
      <dt class="trade-summary__label">{{ t('trades.summary.positivePnl') }}</dt>
      <dd class="trade-summary__value">{{ formatTradeNumber(props.summary.positivePnlCount, currentLocale, 0) }}</dd>
    </div>

    <div class="trade-summary__item trade-summary__item--negative">
      <dt class="trade-summary__label">{{ t('trades.summary.negativePnl') }}</dt>
      <dd class="trade-summary__value">{{ formatTradeNumber(props.summary.negativePnlCount, currentLocale, 0) }}</dd>
    </div>
  </dl>
</template>

<style scoped>
.trade-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--ui-space-12);
  margin: 0;
}

.trade-summary__item {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-4);
  padding: var(--ui-space-12) var(--ui-space-12);
  border: var(--ui-border-width-thin) solid var(--ui-color-border);
  border-radius: var(--ui-radius-8);
  background: var(--ui-color-surface-soft, var(--ui-color-info-soft));
  min-width: 0;
}

.trade-summary__label {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-12);
  line-height: 1.4;
  font-weight: 600;
}

.trade-summary__value {
  margin: 0;
  color: var(--ui-color-text);
  font-size: var(--ui-font-size-16);
  line-height: 1.4;
  font-weight: 700;
  text-align: right;
}

.trade-summary__item--positive {
  border-color: color-mix(in srgb, var(--ui-color-success) 28%, var(--ui-color-border));
}

.trade-summary__item--negative {
  border-color: color-mix(in srgb, var(--ui-color-danger) 28%, var(--ui-color-border));
}

@media (max-width: 1023px) {
  .trade-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .trade-summary {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
