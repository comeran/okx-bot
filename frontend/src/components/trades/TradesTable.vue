<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { CaretBottom, CaretTop } from '@element-plus/icons-vue';

import ResponsiveTable from '@/components/ui/ResponsiveTable.vue';
import StatusBadge from '@/components/ui/StatusBadge.vue';
import type { TradeRecord } from '@/types/trades';
import { formatTradeNumber, formatTradeTimestamp } from '@/utils/trades';

interface Props {
  trades: TradeRecord[];
  loading?: boolean;
  emptyDescription?: string;
}

type TradeViewRow = TradeRecord & {
  rowKey: string | number;
};

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  emptyDescription: '',
});

const { t, locale } = useI18n();
const currentLocale = computed(() => locale.value);
const emptyText = computed(() => props.emptyDescription || t('trades.table.noMatches'));

function escapeTradeFingerprintPart(value: string): string {
  return value
    .replace(/\\/g, '\\\\')
    .replace(/\|/g, '\\|')
    .replace(/=/g, '\\=')
    .replace(/#/g, '\\#');
}

function buildTradeFingerprint(trade: TradeRecord): string {
  return [
    ['strategy', trade.strategy],
    ['symbol', trade.symbol],
    ['side', trade.side],
    ['amount', String(trade.amount)],
    ['price', String(trade.price)],
    ['fee', String(trade.fee)],
    ['timestamp', String(trade.timestamp)],
  ]
    .map(([field, value]) => `${field}=${escapeTradeFingerprintPart(value)}`)
    .join('|');
}

const tradeRows = computed<TradeViewRow[]>(() => {
  const seenFingerprints = new Map<string, number>();

  return props.trades.map((trade) => {
    if (trade.id != null) {
      return {
        ...trade,
        rowKey: trade.id,
      };
    }

    const fingerprint = buildTradeFingerprint(trade);
    const occurrence = seenFingerprints.get(fingerprint) ?? 0;
    seenFingerprints.set(fingerprint, occurrence + 1);

    return {
      ...trade,
      rowKey: occurrence === 0 ? fingerprint : `${fingerprint}#${occurrence}`,
    };
  });
});

function sideLabel(side: string): string {
  const normalized = side.trim().toLowerCase();
  if (normalized === 'buy') return t('trades.table.buy');
  if (normalized === 'sell') return t('trades.table.sell');
  return side || t('common.unknown');
}

function sideTone(side: string): 'neutral' | 'success' | 'danger' {
  const normalized = side.trim().toLowerCase();
  if (normalized === 'buy') return 'success';
  if (normalized === 'sell') return 'danger';
  return 'neutral';
}

function sideIcon(side: string) {
  const normalized = side.trim().toLowerCase();
  if (normalized === 'buy') return CaretTop;
  if (normalized === 'sell') return CaretBottom;
  return undefined;
}
</script>

<template>
  <ResponsiveTable
    class="trades-table"
    :data="tradeRows"
    :loading="props.loading"
    row-key="rowKey"
    stripe
    :scroll-label="t('trades.table.scrollLabel')"
    :scroll-description="t('trades.table.scrollDescription')"
  >
    <el-table-column prop="timestamp" :label="t('trades.table.timestamp')" min-width="190">
      <template #default="{ row }">
        {{ formatTradeTimestamp(row.timestamp, currentLocale) }}
      </template>
    </el-table-column>

    <el-table-column prop="strategy" :label="t('trades.table.strategy')" min-width="150" />
    <el-table-column prop="symbol" :label="t('trades.table.symbol')" min-width="130" />

    <el-table-column prop="side" :label="t('trades.table.side')" min-width="110">
      <template #default="{ row }">
        <StatusBadge
          :status="sideLabel(row.side)"
          :tone="sideTone(row.side)"
          :icon="sideIcon(row.side)"
        />
      </template>
    </el-table-column>

    <el-table-column prop="amount" :label="t('trades.table.amount')" min-width="140" align="right" header-align="right">
      <template #default="{ row }">
        {{ formatTradeNumber(row.amount, currentLocale) }}
      </template>
    </el-table-column>

    <el-table-column prop="price" :label="t('trades.table.price')" min-width="140" align="right" header-align="right">
      <template #default="{ row }">
        {{ formatTradeNumber(row.price, currentLocale) }}
      </template>
    </el-table-column>

    <el-table-column prop="fee" :label="t('trades.table.fee')" min-width="140" align="right" header-align="right">
      <template #default="{ row }">
        {{ formatTradeNumber(row.fee, currentLocale) }}
      </template>
    </el-table-column>

    <template #empty>
      <div class="trades-table__empty" role="status" aria-live="polite">
        {{ emptyText }}
      </div>
    </template>
  </ResponsiveTable>
</template>

<style scoped>
.trades-table {
  min-width: 0;
}

.trades-table__empty {
  padding: var(--ui-space-16);
  color: var(--ui-color-text-secondary);
}
</style>
