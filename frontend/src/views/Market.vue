<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { useI18n } from 'vue-i18n';

import AppPageHeader from '@/components/ui/AppPageHeader.vue';
import MarketChartPanel from '@/components/market/MarketChartPanel.vue';
import MarketQueryPanel from '@/components/market/MarketQueryPanel.vue';
import { fetchKlines, fetchTickers } from '@/services/market';
import type { Kline, MarketTicker } from '@/types/market';
import { buildMarketKlineQuery, fallbackSymbolsByType, formatMarketDateTime } from '@/utils/market';

const { t, locale } = useI18n();

const marketType = ref('spot');
const symbol = ref('BTC-USDT');
const timeframe = ref('1h');
const limit = ref(100);
const startTime = ref<Date | null>(null);
const endTime = ref<Date | null>(null);
interface ChartQuerySummary {
  marketType: string;
  symbol: string;
  timeframe: string;
  startTime: number | null;
  endTime: number | null;
}

const klines = ref<Kline[]>([]);
const tickers = ref<MarketTicker[]>([]);
const loading = ref(false);
const tickersLoading = ref(false);
const errorMessage = ref('');
const rangeQuery = ref(false);
const chartQuery = ref<ChartQuerySummary | null>(null);
let klineRequestId = 0;
let tickerRequestId = 0;

const displayChartQuery = computed(() => chartQuery.value ?? {
  marketType: marketType.value,
  symbol: symbol.value.trim(),
  timeframe: timeframe.value,
  startTime: startTime.value?.getTime() ?? null,
  endTime: endTime.value?.getTime() ?? null,
});

const displayChartRangeSummary = computed(() => {
  if (displayChartQuery.value.startTime === null || displayChartQuery.value.startTime === undefined || displayChartQuery.value.endTime === null || displayChartQuery.value.endTime === undefined) {
    return t('market.latestCandles');
  }

  return t('market.rangeSummary', {
    start: formatMarketDateTime(displayChartQuery.value.startTime, locale.value),
    end: formatMarketDateTime(displayChartQuery.value.endTime, locale.value),
  });
});
const symbolOptions = computed(() => {
  const fallbackSymbols = fallbackSymbolsByType[marketType.value] ?? fallbackSymbolsByType.spot;
  const tickerSymbols = tickers.value.map((ticker) => ticker.symbol).filter(Boolean);
  return Array.from(new Set([...fallbackSymbols, ...tickerSymbols]));
});

async function loadTickers() {
  const requestId = ++tickerRequestId;
  tickersLoading.value = true;

  try {
    const nextTickers = await fetchTickers(marketType.value);
    if (requestId !== tickerRequestId) {
      return;
    }

    tickers.value = nextTickers;
  } catch {
    if (requestId !== tickerRequestId) {
      return;
    }

    tickers.value = [];
    if (typeof document !== 'undefined') {
      ElMessage.warning(t('market.unableToLoadSymbols'));
    }
  } finally {
    if (requestId === tickerRequestId) {
      tickersLoading.value = false;
    }
  }
}

async function loadKlines() {
  const result = buildMarketKlineQuery({
    symbol: symbol.value,
    timeframe: timeframe.value,
    limit: limit.value,
    startTime: startTime.value,
    endTime: endTime.value,
    marketType: marketType.value,
  });

  if ('error' in result) {
    klineRequestId += 1;
    errorMessage.value = t(`market.${result.error}`);
    loading.value = false;
    if (!chartQuery.value) {
      klines.value = [];
      rangeQuery.value = false;
    }
    if (result.error !== 'symbolRequired' && typeof document !== 'undefined') {
      ElMessage.error(errorMessage.value);
    }
    return;
  }

  const requestId = ++klineRequestId;
  loading.value = true;
  errorMessage.value = '';

  try {
    const nextKlines = await fetchKlines(result.query);
    if (requestId !== klineRequestId) {
      return;
    }

    klines.value = nextKlines;
    rangeQuery.value = result.rangeQuery;
    chartQuery.value = {
      marketType: result.query.market_type ?? marketType.value,
      symbol: result.query.symbol,
      timeframe: result.query.timeframe,
      startTime: result.query.start_time ?? null,
      endTime: result.query.end_time ?? null,
    };
    errorMessage.value = '';
  } catch {
    if (requestId !== klineRequestId) {
      return;
    }

    errorMessage.value = t('market.loadDataError');
    if (typeof document !== 'undefined') {
      ElMessage.error(errorMessage.value);
    }
  } finally {
    if (requestId === klineRequestId) {
      loading.value = false;
    }
  }
}

function handleSubmit() {
  void loadKlines();
}

watch(marketType, (nextMarketType, previousMarketType) => {
  const previousFallbackSymbols = fallbackSymbolsByType[previousMarketType] ?? fallbackSymbolsByType.spot;
  const nextFallbackSymbols = fallbackSymbolsByType[nextMarketType] ?? fallbackSymbolsByType.spot;

  if (!symbol.value.trim() || previousFallbackSymbols.includes(symbol.value)) {
    symbol.value = nextFallbackSymbols[0] ?? '';
  }

  tickers.value = [];
  void loadTickers();
  void loadKlines();
});

onMounted(() => {
  void loadTickers();
  void loadKlines();
});
</script>

<template>
  <section class="market-view">
    <AppPageHeader :title="t('market.title')" :description="t('market.description')" />

    <MarketQueryPanel
      v-model:market-type="marketType"
      v-model:symbol="symbol"
      v-model:timeframe="timeframe"
      v-model:limit="limit"
      v-model:start-time="startTime"
      v-model:end-time="endTime"
      :symbol-options="symbolOptions"
      :loading="loading"
      :tickers-loading="tickersLoading"
      :chart-query="chartQuery"
      @submit="handleSubmit"
    />

    <MarketChartPanel
      :klines="klines"
      :loading="loading"
      :error="errorMessage || null"
      :range-query="rangeQuery"
      :stale="Boolean(errorMessage && chartQuery && klines.length > 0)"
      :symbol="displayChartQuery.symbol"
      :timeframe="displayChartQuery.timeframe"
      @retry="loadKlines"
    />

    <p v-if="chartQuery" class="market-view__status" data-testid="market-query-status" aria-live="polite">
      {{ t(`settings.marketTypes.${chartQuery.marketType}`) }} · {{ chartQuery.symbol }} · {{ chartQuery.timeframe }}
      <span class="market-view__status-range">· {{ displayChartRangeSummary }}</span>
    </p>
  </section>
</template>

<style scoped>
.market-view {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-20);
}

.market-view__status {
  margin: 0;
  color: var(--ui-color-text-secondary);
}
</style>
