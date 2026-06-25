<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { useI18n } from 'vue-i18n';

import Candlestick from '@/components/charts/Candlestick.vue';
import { fetchKlines, fetchTickers } from '@/services/market';
import type { Kline, KlineQuery, MarketTicker } from '@/types/market';

const { t } = useI18n();

const timeframeOptions = ['1m', '5m', '15m', '1h', '4h', '1d'];
const limitOptions = [50, 100, 200, 500];
const marketType = ref('spot');
const fallbackSymbolsByType: Record<string, string[]> = {
  spot: ['BTC-USDT', 'ETH-USDT', 'OKB-USDT', 'SOL-USDT'],
  swap: ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP'],
  future: ['BTC-USDT-260626', 'ETH-USDT-260626'],
  option: [],
};

const form = reactive<{
  symbol: string;
  timeframe: string;
  limit: number;
  startTime: Date | null;
  endTime: Date | null;
}>({
  symbol: 'BTC-USDT',
  timeframe: '1h',
  limit: 100,
  startTime: null,
  endTime: null,
});

const klines = ref<Kline[]>([]);
const chartQuery = ref({ symbol: form.symbol, timeframe: form.timeframe });
const tickers = ref<MarketTicker[]>([]);
const loading = ref(false);
const tickersLoading = ref(false);
const errorMessage = ref('');
const lastQueryUsedRange = ref(false);
let klineRequestId = 0;

const symbolOptions = computed(() => {
  const fallbackSymbols = fallbackSymbolsByType[marketType.value] ?? fallbackSymbolsByType.spot;
  const symbols = tickers.value
    .map((ticker) => ticker.symbol)
    .filter((symbol): symbol is string => Boolean(symbol));

  return Array.from(new Set([...fallbackSymbols, ...symbols]));
});

const hasKlines = computed(() => klines.value.length > 0);
const emptyDescription = computed(() => (
  errorMessage.value || (lastQueryUsedRange.value ? t('market.noCachedKlineData') : t('market.noKlineData'))
));

const buildKlineQuery = (): KlineQuery | null => {
  const symbol = form.symbol.trim();
  if (!symbol) {
    errorMessage.value = t('market.symbolRequired');
    return null;
  }

  if ((form.startTime === null) !== (form.endTime === null)) {
    ElMessage.error(t('market.incompleteRange'));
    return null;
  }

  if (form.startTime && form.endTime) {
    const startTime = form.startTime.getTime();
    const endTime = form.endTime.getTime();

    if (endTime <= startTime) {
      ElMessage.error(t('market.invalidRange'));
      return null;
    }

    return {
      symbol,
      timeframe: form.timeframe,
      limit: form.limit,
      start_time: startTime,
      end_time: endTime,
      market_type: marketType.value,
    };
  }

  return {
    symbol,
    timeframe: form.timeframe,
    limit: form.limit,
    market_type: marketType.value,
  };
};

const loadTickers = async () => {
  tickersLoading.value = true;

  try {
    tickers.value = await fetchTickers(marketType.value);
  } catch {
    tickers.value = [];
    ElMessage.warning(t('market.unableToLoadSymbols'));
  } finally {
    tickersLoading.value = false;
  }
};

const loadKlines = async () => {
  const query = buildKlineQuery();
  if (query === null) {
    klineRequestId += 1;
    klines.value = [];
    lastQueryUsedRange.value = false;
    loading.value = false;
    return;
  }

  const requestId = ++klineRequestId;
  const isRangeQuery = query.start_time !== undefined && query.end_time !== undefined;

  loading.value = true;
  errorMessage.value = '';

  try {
    const nextKlines = await fetchKlines(query);
    if (requestId !== klineRequestId) {
      return;
    }

    klines.value = nextKlines;
    lastQueryUsedRange.value = isRangeQuery;
    errorMessage.value = nextKlines.length === 0 && isRangeQuery ? t('market.noCachedKlineData') : '';
    chartQuery.value = { symbol: query.symbol, timeframe: query.timeframe };
  } catch (error) {
    if (requestId !== klineRequestId) {
      return;
    }

    klines.value = [];
    lastQueryUsedRange.value = isRangeQuery;
    errorMessage.value = t('market.loadDataError');
    ElMessage.error(errorMessage.value);
  } finally {
    if (requestId === klineRequestId) {
      loading.value = false;
    }
  }
};

const handleSubmit = () => {
  void loadKlines();
};

watch(marketType, () => {
  const fallbackSymbols = fallbackSymbolsByType[marketType.value] ?? fallbackSymbolsByType.spot;
  form.symbol = fallbackSymbols[0] ?? '';
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
    <div class="market-header">
      <div>
        <h2>{{ t('market.title') }}</h2>
        <p>{{ t('market.description') }}</p>
      </div>
    </div>

    <el-card shadow="hover" class="controls-card">
      <el-form :model="form" inline label-position="top" @submit.prevent="handleSubmit">
        <el-form-item :label="t('market.marketType')">
          <el-select v-model="marketType" class="control-width">
            <el-option :label="t('settings.marketTypes.spot')" value="spot" />
            <el-option :label="t('settings.marketTypes.swap')" value="swap" />
            <el-option :label="t('settings.marketTypes.future')" value="future" />
            <el-option :label="t('settings.marketTypes.option')" value="option" />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('common.symbol')">
          <el-select
            v-model="form.symbol"
            filterable
            allow-create
            default-first-option
            :loading="tickersLoading"
            :placeholder="t('market.selectSymbol')"
            class="control-width"
          >
            <el-option
              v-for="symbol in symbolOptions"
              :key="symbol"
              :label="symbol"
              :value="symbol"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('common.timeframe')">
          <el-select v-model="form.timeframe" class="control-width">
            <el-option
              v-for="timeframe in timeframeOptions"
              :key="timeframe"
              :label="timeframe"
              :value="timeframe"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('market.startTime')">
          <el-date-picker
            v-model="form.startTime"
            type="datetime"
            :placeholder="t('market.selectStartTime')"
            class="time-control-width"
          />
        </el-form-item>

        <el-form-item :label="t('market.endTime')">
          <el-date-picker
            v-model="form.endTime"
            type="datetime"
            :placeholder="t('market.selectEndTime')"
            class="time-control-width"
          />
        </el-form-item>

        <el-form-item :label="t('common.limit')">
          <el-select v-model="form.limit" class="control-width">
            <el-option
              v-for="limit in limitOptions"
              :key="limit"
              :label="String(limit)"
              :value="limit"
            />
          </el-select>
        </el-form-item>

        <el-form-item label=" ">
          <el-button type="primary" :loading="loading" native-type="submit">
            {{ t('market.loadChart') }}
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="hover" class="chart-card">
      <div v-loading="loading" class="chart-content">
        <Candlestick
          v-if="hasKlines"
          :klines="klines"
          :symbol="chartQuery.symbol"
          :timeframe="chartQuery.timeframe"
        />

        <el-empty
          v-else
          :description="emptyDescription"
        >
          <el-button type="primary" :loading="loading" @click="loadKlines">
            {{ t('common.refresh') }}
          </el-button>
        </el-empty>
      </div>
    </el-card>
  </section>
</template>

<style scoped>
.market-view h2 {
  margin: 0 0 8px;
}

.market-view p {
  margin: 0;
  color: #606266;
}

.market-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.controls-card {
  margin-bottom: 20px;
}

.control-width {
  width: 180px;
}

.time-control-width {
  width: 220px;
}

.chart-card :deep(.el-card__body) {
  padding: 16px;
}

.chart-content {
  min-height: 420px;
}
</style>
