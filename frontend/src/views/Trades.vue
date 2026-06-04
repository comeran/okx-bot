<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useI18n } from 'vue-i18n';

import { fetchTrades } from '@/services/trades';
import type { TradeRecord } from '@/types/trades';

const { t } = useI18n();

const trades = ref<TradeRecord[]>([]);
const loading = ref(false);

function formatNumber(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 8 });
}

function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleString();
}

async function loadTrades(): Promise<void> {
  loading.value = true;
  try {
    trades.value = await fetchTrades();
  } catch {
    ElMessage.error(t('trades.loadError'));
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void loadTrades();
});
</script>

<template>
  <section class="trades-page">
    <div class="trades-page__header">
      <div>
        <h2>{{ t('trades.title') }}</h2>
        <p>{{ t('trades.description') }}</p>
      </div>
      <el-button :loading="loading" @click="loadTrades">
        {{ t('common.refresh') }}
      </el-button>
    </div>

    <el-card shadow="hover" class="trades-card">
      <template #header>{{ t('trades.history') }}</template>
      <el-table v-loading="loading" :data="trades" empty-text=" " stripe>
        <el-table-column :label="t('trades.timestamp')" min-width="180">
          <template #default="{ row }">
            {{ formatTime(row.timestamp) }}
          </template>
        </el-table-column>
        <el-table-column prop="strategy" :label="t('trades.strategy')" min-width="120" />
        <el-table-column prop="symbol" :label="t('common.symbol')" min-width="120" />
        <el-table-column prop="side" :label="t('trades.side')" min-width="100" />
        <el-table-column :label="t('trades.amount')" min-width="120">
          <template #default="{ row }">
            {{ formatNumber(row.amount) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('trades.price')" min-width="120">
          <template #default="{ row }">
            {{ formatNumber(row.price) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('trades.fee')" min-width="120">
          <template #default="{ row }">
            {{ formatNumber(row.fee) }}
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && trades.length === 0" :description="t('trades.noTrades')" />
    </el-card>
  </section>
</template>

<style scoped>
.trades-page h2 {
  margin: 0 0 8px;
}

.trades-page p {
  margin: 0;
  color: #606266;
}

.trades-page__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.trades-card {
  margin-bottom: 20px;
}
</style>
