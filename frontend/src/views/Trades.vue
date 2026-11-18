<script setup lang="ts">
import { ElMessage } from 'element-plus';
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';

import { useMarketSymbols } from '@/composables/useMarketSymbols';
import { fallbackSymbolsByType, marketTypeForSymbol, marketTypeOptions, type MarketType } from '@/utils/market';
import TradeFilters from '@/components/trades/TradeFilters.vue';
import TradeSummary from '@/components/trades/TradeSummary.vue';
import TradesTable from '@/components/trades/TradesTable.vue';
import AppPageHeader from '@/components/ui/AppPageHeader.vue';
import DataState from '@/components/ui/DataState.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import { useAccountContextStore } from '@/stores/accountContext';
import AccountContextNotice from '@/components/dashboard/AccountContextNotice.vue';
import { fetchTrades } from '@/services/trades';
import {
  amendTradingOrder,
  cancelTradingOrder,
  fetchTradingOrders,
  submitManualOrder,
  type RestingOrder,
} from '@/services/trading';
import type { TradeRecord } from '@/types/trades';
import {
  buildTradeFilterOptions,
  createTradeFilters,
  filterTrades,
  summarizeTrades,
  type TradeFilters as TradeFiltersState,
} from '@/utils/trades';

const { t } = useI18n();
const authority = useAccountContextStore();

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
const blockingLoading = computed(() => (loading.value || authority.loading) && !hasSuccessfulLoad.value);
const errorMessage = computed(() => (errorKey.value ? t(errorKey.value) : null));
const stale = computed(() => Boolean(errorMessage.value && hasSuccessfulLoad.value));
const ticketMarket = ref<MarketType>('spot');
const { fetchedSymbols, symbolsLoading } = useMarketSymbols(ticketMarket);
const ticket = reactive({
  symbol: 'BTC-USDT',
  side: 'buy' as 'buy' | 'sell',
  orderType: 'limit' as 'limit' | 'market',
  amount: '0.0001',
  price: '',
});
const ticketMarketValue = computed({
  get: () => ticketMarket.value,
  set: (market: MarketType) => {
    if (marketTypeForSymbol(ticket.symbol) !== market) ticket.symbol = '';
    ticketMarket.value = market;
  },
});
const ticketSymbols = computed(() => [...new Set([
  ...fallbackSymbolsByType[ticketMarket.value], ticket.symbol, ...fetchedSymbols.value,
])].filter((symbol) => marketTypeForSymbol(symbol) === ticketMarket.value));
const restingOrders = ref<RestingOrder[]>([]);
const amendPrices = reactive<Record<string, string>>({});
const orderBusy = ref(false);
const terminalStatuses = new Set(['filled', 'cancelled', 'canceled', 'rejected']);

function orderKey(order: RestingOrder): string {
  return order.order_id || order.id || '';
}

async function loadOrders(): Promise<void> {
  try {
    const rows = await fetchTradingOrders();
    restingOrders.value = rows.filter((order) => !terminalStatuses.has(String(order.status ?? 'open')));
  } catch {
    restingOrders.value = [];
  }
}

async function submitOrder(): Promise<void> {
  if (orderBusy.value) return;
  const amount = Number(ticket.amount);
  const price = ticket.price.trim() ? Number(ticket.price) : null;
  if (marketTypeForSymbol(ticket.symbol) !== ticketMarket.value) {
    ElMessage.error(t('market.instrumentMismatch'));
    return;
  }
  if (!ticket.symbol.trim() || !Number.isFinite(amount) || amount <= 0) {
    ElMessage.error(t('trades.orderFailed'));
    return;
  }
  if (ticket.orderType === 'limit' && (price === null || !Number.isFinite(price) || price <= 0)) {
    ElMessage.error(t('trades.orderFailed'));
    return;
  }
  orderBusy.value = true;
  try {
    await submitManualOrder({
      symbol: ticket.symbol.trim(),
      market_type: ticketMarket.value,
      side: ticket.side,
      order_type: ticket.orderType,
      amount,
      price,
    });
    ElMessage.success(t('trades.orderSent'));
    await Promise.all([loadOrders(), loadTrades()]);
  } catch {
    ElMessage.error(t('trades.orderFailed'));
  } finally {
    orderBusy.value = false;
  }
}

async function cancelOrder(order: RestingOrder): Promise<void> {
  const id = orderKey(order);
  if (!id || !order.symbol || orderBusy.value) return;
  orderBusy.value = true;
  try {
    await cancelTradingOrder(id, order.symbol);
    ElMessage.success(t('trades.orderSent'));
    await loadOrders();
  } catch {
    ElMessage.error(t('trades.orderFailed'));
  } finally {
    orderBusy.value = false;
  }
}

async function amendOrder(order: RestingOrder): Promise<void> {
  const id = orderKey(order);
  const price = Number(amendPrices[id]);
  if (!id || !order.symbol || !Number.isFinite(price) || price <= 0 || orderBusy.value) {
    ElMessage.error(t('trades.orderFailed'));
    return;
  }
  orderBusy.value = true;
  try {
    await amendTradingOrder(id, order.symbol, price);
    ElMessage.success(t('trades.orderSent'));
    await loadOrders();
  } catch {
    ElMessage.error(t('trades.orderFailed'));
  } finally {
    orderBusy.value = false;
  }
}
const empty = computed(() => hasSuccessfulLoad.value && !hasLoadedRecords.value);

async function loadTrades(): Promise<void> {
  const contextRequest = authority.context ? null : authority.ensure();
  const epoch = authority.epoch;
  const requestSequence = ++loadSequence;
  loading.value = true;

  try {
    const context = authority.context ?? await contextRequest;
    if (epoch !== authority.epoch || requestSequence !== loadSequence) return;
    if (!context) throw new Error('Account context unavailable');
    const nextTrades = await fetchTrades(undefined, context.available ? context.account_scope_id : null);
    if (requestSequence !== loadSequence || epoch !== authority.epoch) return;
    trades.value = nextTrades;
    loadedOnce.value = true;
    errorKey.value = null;
  } catch {
    if (requestSequence !== loadSequence || epoch !== authority.epoch) return;
    errorKey.value = 'trades.loadError';
  } finally {
    if (requestSequence !== loadSequence || epoch !== authority.epoch) return;
    loading.value = false;
  }
}

watch(() => authority.epoch, () => {
  loadSequence += 1;
  trades.value = [];
  filters.value = createTradeFilters();
  loadedOnce.value = false;
  loading.value = false;
  errorKey.value = null;
}, { flush: 'sync' });
watch(() => authority.context, (context) => {
  if (context) void loadTrades();
});
onMounted(() => {
  void loadOrders();
  if (authority.context) void loadTrades();
  else void authority.ensure();
});
onBeforeUnmount(() => {
  loadSequence += 1;
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

    <AccountContextNotice :context="authority.context" :loading="authority.loading" :error="authority.error" />

    <SectionCard :title="t('trades.ticketTitle')" :description="t('trades.ticketDescription')">
      <p class="trades-view__market-hint">{{ t('market.sharedCredentials') }}</p>
      <form class="trades-view__ticket" @submit.prevent="submitOrder">
        <label>
          {{ t('market.marketType') }}
          <el-select v-model="ticketMarketValue" data-testid="manual-market-type" :aria-label="t('market.marketType')" :disabled="orderBusy">
            <el-option v-for="market in marketTypeOptions" :key="market" :value="market" :label="t(`settings.marketTypes.${market}`)" />
          </el-select>
        </label>
        <label>
          {{ t('trades.table.symbol') }}
          <el-select v-model="ticket.symbol" data-testid="manual-symbol" filterable allow-create default-first-option
            :loading="symbolsLoading" :disabled="orderBusy" :aria-label="t('trades.table.symbol')">
            <el-option v-for="symbol in ticketSymbols" :key="symbol" :value="symbol" :label="symbol" />
          </el-select>
        </label>
        <label class="trades-view__choice">
          {{ t('trades.table.side') }}
          <span class="sr-only" data-testid="manual-side">{{ ticket.side }}</span>
          <button type="button" data-testid="manual-buy" :aria-pressed="ticket.side === 'buy'" @click="ticket.side = 'buy'">{{ t('trades.table.buy') }}</button>
          <button type="button" data-testid="manual-sell" :aria-pressed="ticket.side === 'sell'" @click="ticket.side = 'sell'">{{ t('trades.table.sell') }}</button>
        </label>
        <label class="trades-view__choice">
          {{ t('trades.orderType') }}
          <span class="sr-only" data-testid="manual-type">{{ ticket.orderType }}</span>
          <button type="button" data-testid="manual-limit" :aria-pressed="ticket.orderType === 'limit'" @click="ticket.orderType = 'limit'">{{ t('trades.limit') }}</button>
          <button type="button" data-testid="manual-market" :aria-pressed="ticket.orderType === 'market'" @click="ticket.orderType = 'market'">{{ t('trades.marketOrder') }}</button>
        </label>
        <label>
          {{ t('trades.table.amount') }}
          <input v-model="ticket.amount" inputmode="decimal" data-testid="manual-amount">
        </label>
        <label>
          {{ t('trades.table.price') }}
          <input v-model="ticket.price" inputmode="decimal" data-testid="manual-price">
        </label>
        <button type="submit" data-testid="manual-submit" :disabled="orderBusy">{{ t('trades.submitOrder') }}</button>
      </form>
      <h3>{{ t('trades.restingOrders') }}</h3>
      <p v-if="!restingOrders.length">{{ t('trades.noRestingOrders') }}</p>
      <div v-for="order in restingOrders" :key="orderKey(order)" class="trades-view__order">
        <span>{{ order.symbol }} {{ order.side }} {{ order.amount }} @ {{ order.price }} {{ order.status }}</span>
        <input v-model="amendPrices[orderKey(order)]" :placeholder="t('trades.newPrice')" :data-testid="`amend-price-${orderKey(order)}`">
        <button type="button" :disabled="orderBusy" @click="amendOrder(order)">{{ t('trades.amendOrder') }}</button>
        <button type="button" :disabled="orderBusy" @click="cancelOrder(order)">{{ t('trades.cancelOrder') }}</button>
      </div>
    </SectionCard>

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

.trades-view__order {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ui-space-8);
  align-items: end;
}

.trades-view__ticket {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ui-space-16);
  align-items: end;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.trades-view__ticket label {
  display: grid;
  gap: var(--ui-space-4);
  min-width: 0;
  color: var(--ui-color-text-secondary);
  font-size: 13px;
}

.trades-view__ticket :deep(.el-select) {
  width: 100%;
  min-width: 0;
}

.trades-view__ticket input,
.trades-view__ticket button {
  box-sizing: border-box;
  min-width: 0;
  min-height: 36px;
  padding: 7px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: var(--ui-radius-4);
  font: inherit;
  color: var(--ui-color-text);
  background: var(--ui-color-surface);
}

.trades-view__ticket button {
  cursor: pointer;
}

.trades-view__ticket label.trades-view__choice {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.trades-view__choice button {
  grid-row: 2;
}

.trades-view__choice button[aria-pressed="true"] {
  border-color: var(--ui-color-primary);
  background: var(--ui-color-primary-soft);
  color: var(--ui-color-primary);
}

.trades-view__ticket > button {
  grid-column: 1 / -1;
  justify-self: end;
  min-width: 160px;
  border-color: var(--ui-color-primary);
  background: var(--ui-color-primary);
  color: white;
  font-weight: 600;
}

.trades-view__ticket button:disabled {
  cursor: wait;
  opacity: 0.6;
}

.trades-view__ticket input:focus-visible,
.trades-view__ticket button:focus-visible {
  outline: 2px solid var(--ui-color-primary);
  outline-offset: 2px;
}

@media (max-width: 600px) {
  .trades-view__ticket {
    grid-template-columns: minmax(0, 1fr);
  }

  .trades-view__ticket > button {
    justify-self: stretch;
  }
}
</style>
