<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useI18n } from 'vue-i18n';

import Candlestick from '@/components/charts/Candlestick.vue';
import { fetchKlines, fetchTickers } from '@/services/market';
import type { Kline, MarketTicker } from '@/types/market';

const { t } = useI18n();

const timeframeOptions = ['1m', '5m', '15m', '1h', '4h', '1d'];
const limitOptions = [50, 100, 200, 500];
const fallbackSymbols = ['BTC-USDT', 'ETH-USDT', 'OKB-USDT', 'SOL-USDT'];

const form = reactive({
  symbol: 'BTC-USDT',
  timeframe: '1h',
  limit: 100,
});

const klines = ref<Kline[]>([]);
const chartQuery = ref({ symbol: form.symbol, timeframe: form.timeframe });
const tickers = ref<MarketTicker[]>([]);
const loading = ref(false);
const tickersLoading = ref(false);
const errorMessage = ref('');
let klineRequestId = 0;

const symbolOptions = computed(() => {
  const symbols = tickers.value
    .map((ticker) => ticker.symbol)
    .filter((symbol): symbol is string => Boolean(symbol));

  return Array.from(new Set([...fallbackSymbols, ...symbols]));
});

const hasKlines = computed(() => klines.value.length > 0);

const loadTickers = async () => {
  tickersLoading.value = true;

  try {
    tickers.value = await fetchTickers();
  } catch {
    tickers.value = [];
    ElMessage.warning(t('market.unableToLoadSymbols'));
  } finally {
    tickersLoading.value = false;
  }
};

const loadKlines = async () => {
  const requestId = ++klineRequestId;
  const query = { ...form };

  loading.value = true;
  errorMessage.value = '';

  try {
    const nextKlines = await fetchKlines(query);
    if (requestId !== klineRequestId) {
      return;
    }

    klines.value = nextKlines;
    chartQuery.value = { symbol: query.symbol, timeframe: query.timeframe };
  } catch (error) {
    if (requestId !== klineRequestId) {
      return;
    }

    klines.value = [];
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
          :description="errorMessage || t('market.noKlineData')"
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

.chart-card :deep(.el-card__body) {
  padding: 16px;
}

.chart-content {
  min-height: 420px;
}
</style>
