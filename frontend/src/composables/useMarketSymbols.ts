import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useI18n } from 'vue-i18n';
import { fetchTickers } from '@/services/market';
import { marketTypeForSymbol, type MarketType } from '@/utils/market';

/** Keep operation instrument choices bound to the latest selected market. */
export function useMarketSymbols(market: Ref<MarketType>) {
  const { t } = useI18n();
  const fetchedSymbols = ref<string[]>([]);
  const symbolsLoading = ref(false);
  let active = false;
  let sequence = 0;

  async function load(): Promise<void> {
    const request = ++sequence;
    const capturedMarket = market.value;
    fetchedSymbols.value = [];
    symbolsLoading.value = true;
    try {
      const tickers = await fetchTickers(capturedMarket);
      if (!active || request !== sequence) return;
      fetchedSymbols.value = [...new Set(tickers.map((ticker) => ticker.symbol)
        .filter((symbol) => marketTypeForSymbol(symbol) === capturedMarket))];
    } catch {
      if (!active || request !== sequence) return;
      ElMessage.warning(t('market.unableToLoadSymbols'));
    } finally {
      if (active && request === sequence) symbolsLoading.value = false;
    }
  }

  watch(market, () => { if (active) void load(); }, { flush: 'sync' });
  onMounted(() => { active = true; void load(); });
  onBeforeUnmount(() => { active = false; sequence += 1; });
  return { fetchedSymbols, symbolsLoading };
}
