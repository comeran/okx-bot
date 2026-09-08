<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';

import TradeFilters from '@/components/trades/TradeFilters.vue';
import TradeSummary from '@/components/trades/TradeSummary.vue';
import TradesTable from '@/components/trades/TradesTable.vue';
import AppPageHeader from '@/components/ui/AppPageHeader.vue';
import DataState from '@/components/ui/DataState.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import { fetchTrades } from '@/services/trades';
import type { TradeRecord } from '@/types/trades';
import {
  buildTradeFilterOptions,
  createTradeFilters,
  filterTrades,
  summarizeTrades,
  type TradeFilters as TradeFiltersState,
} from '@/utils/trades';

const { t } = useI18n();

const trades = ref<TradeRecord[]>([]);
const filters = ref<TradeFiltersState>(createTradeFilters());
const loading = ref(false);
const loadedOnce = ref(false);
const errorKey = ref<'trades.loadError' | null>(null);
let loadSequence = 0;

const filterOptions = computed(() => buildTradeFilterOptions(trades.value));
const filteredTrades = computed(() => filterTrades(trades.value, filters.value));
const tradeSummary = computed(() => summarizeTrades(filteredTrades.value));
const hasLoadedRecords = computed(() => trades.value.length > 0);
const hasSuccessfulLoad = computed(() => loadedOnce.value);
const blockingLoading = computed(() => loading.value && !hasSuccessfulLoad.value);
const errorMessage = computed(() => (errorKey.value ? t(errorKey.value) : null));
const stale = computed(() => Boolean(errorMessage.value && hasSuccessfulLoad.value));
const empty = computed(() => hasSuccessfulLoad.value && !hasLoadedRecords.value);

async function loadTrades(): Promise<void> {
  const requestSequence = ++loadSequence;
  loading.value = true;

  try {
    const nextTrades = await fetchTrades();
    if (requestSequence !== loadSequence) return;
    trades.value = nextTrades;
    loadedOnce.value = true;
    errorKey.value = null;
  } catch {
    if (requestSequence !== loadSequence) return;
    errorKey.value = 'trades.loadError';
  } finally {
    if (requestSequence !== loadSequence) return;
    loading.value = false;
  }
}

onMounted(() => {
  void loadTrades();
});
</script>

<template>
  <section class="trades-view">
    <AppPageHeader :title="t('trades.title')" :description="t('trades.description')">
      <template #actions>
        <el-button :loading="loading" @click="loadTrades">
          {{ t('common.refresh') }}
        </el-button>
      </template>
    </AppPageHeader>

    <DataState
      :loading="blockingLoading"
      :error="errorMessage"
      :empty="empty"
      :empty-description="t('trades.noTrades')"
      :stale="stale"
      @retry="loadTrades"
    >
      <div class="trades-view__content">
        <SectionCard :title="t('trades.filters.title')" :description="t('trades.filters.description')">
          <TradeFilters
            v-model="filters"
            :strategy-options="filterOptions.strategies"
            :symbol-options="filterOptions.symbols"
            :disabled="loading && !hasSuccessfulLoad"
          />
        </SectionCard>

        <SectionCard :title="t('trades.summary.title')" :description="t('trades.summary.description')">
          <TradeSummary :summary="tradeSummary" />
        </SectionCard>

        <SectionCard :title="t('trades.history')" :description="t('trades.table.description')">
          <TradesTable
            :trades="filteredTrades"
            :loading="loading && hasSuccessfulLoad"
            :empty-description="t('trades.table.noMatches')"
          />
        </SectionCard>
      </div>
    </DataState>
  </section>
</template>

<style scoped>
.trades-view {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
  min-width: 0;
}

.trades-view__content {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-16);
  min-width: 0;
}
</style>
