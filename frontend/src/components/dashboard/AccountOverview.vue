<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import * as echarts from 'echarts/core';
import { PieChart } from 'echarts/charts';
import { LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ECharts, ComposeOption } from 'echarts/core';
import type { LegendComponentOption, TooltipComponentOption } from 'echarts/components';
import type { PieSeriesOption } from 'echarts/charts';

import DataState from '@/components/ui/DataState.vue';
import ResponsiveTable from '@/components/ui/ResponsiveTable.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import type { AssetBalance } from '@/types/dashboard';
import { formatRuntimeCurrency, formatRuntimeNumber } from '@/utils/dashboard';

echarts.use([CanvasRenderer, LegendComponent, PieChart, TooltipComponent]);

type AccountAllocationOption = ComposeOption<LegendComponentOption | PieSeriesOption | TooltipComponentOption>;

type AssetAllocationRow = AssetBalance & {
  allocationShare: number;
};

interface Props {
  assets: AssetBalance[];
  loading?: boolean;
  error?: string | null;
  accountError?: string | null;
  stale?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  error: null,
  accountError: null,
  stale: false,
});

const emit = defineEmits<{
  retry: [];
}>();

const { t } = useI18n();
const allocationChartRef = ref<HTMLDivElement | null>(null);
let allocationChart: ECharts | null = null;

const displayError = computed(() => props.accountError ?? props.error);
const positiveAssets = computed(() => props.assets.filter((asset) => asset.eq_utd > 0));
const positiveAllocationTotal = computed(() => positiveAssets.value.reduce((sum, asset) => sum + asset.eq_utd, 0));
const hasAllocationAssets = computed(() => positiveAllocationTotal.value > 0);
const chartVisible = computed(() => !props.loading && hasAllocationAssets.value && (!displayError.value || props.stale));
const assetRows = computed<AssetAllocationRow[]>(() => props.assets.map((asset) => ({
  ...asset,
  allocationShare: asset.eq_utd > 0 && positiveAllocationTotal.value > 0
    ? (asset.eq_utd / positiveAllocationTotal.value) * 100
    : 0,
})));
const pieData = computed(() => positiveAssets.value.map((asset) => ({
  name: asset.ccy,
  value: asset.eq_utd,
})));

function disposeChart() {
  allocationChart?.dispose();
  allocationChart = null;
}

function resizeChart() {
  allocationChart?.resize();
}

async function syncChart() {
  if (!chartVisible.value) {
    disposeChart();
    return;
  }

  await nextTick();
  if (!chartVisible.value) {
    disposeChart();
    return;
  }

  const chartElement = allocationChartRef.value;
  if (!chartElement) {
    disposeChart();
    return;
  }

  allocationChart = echarts.getInstanceByDom(chartElement) ?? echarts.init(chartElement);
  const option: AccountAllocationOption = {
    tooltip: {
      trigger: 'item',
      valueFormatter: (value) => formatRuntimeCurrency(Number(value)),
    },
    legend: {
      bottom: 0,
      type: 'scroll',
    },
    series: [
      {
        name: t('dashboard.assetAllocation'),
        type: 'pie',
        radius: ['42%', '70%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        label: {
          formatter: '{b}: {d}%',
        },
        data: pieData.value,
      },
    ],
  };

  allocationChart.setOption(option, true);
  allocationChart.resize();
}

watch([chartVisible, pieData], () => {
  void syncChart();
}, { deep: true, immediate: true });

onMounted(() => {
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', resizeChart);
  }
});

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', resizeChart);
  }
  disposeChart();
});

function retry() {
  emit('retry');
}
</script>

<template>
  <SectionCard :title="t('dashboard.accountOverview')">
    <DataState
      :loading="props.loading"
      :error="displayError"
      :stale="props.stale"
      :empty="!props.loading && !displayError && !hasAllocationAssets"
      :empty-description="t('dashboard.noAssets')"
      @retry="retry"
    >
      <div class="account-overview">
        <section class="account-overview__panel account-overview__panel--chart" :aria-label="t('dashboard.assetAllocation')">
          <h4 class="account-overview__panel-title">{{ t('dashboard.assetAllocation') }}</h4>
          <div v-if="chartVisible" ref="allocationChartRef" class="account-overview__chart" />
          <el-empty v-else-if="!props.loading && !displayError" :description="t('dashboard.noAssets')" />
        </section>

        <ResponsiveTable
          class="account-overview__table"
          :data="assetRows"
          :scroll-label="t('dashboard.assetAllocation')"
          :scroll-description="t('dashboard.assetAllocation')"
        >
          <el-table-column prop="ccy" :label="t('dashboard.currency')" min-width="90" />
          <el-table-column :label="t('dashboard.assetCashBalance')" min-width="130">
            <template #default="{ row }">
              {{ formatRuntimeNumber(row.cash_bal) }}
            </template>
          </el-table-column>
          <el-table-column :label="t('dashboard.nativeEquity')" min-width="130">
            <template #default="{ row }">
              {{ formatRuntimeNumber(row.eq) }}
            </template>
          </el-table-column>
          <el-table-column :label="t('dashboard.convertedEquity')" min-width="140">
            <template #default="{ row }">
              {{ formatRuntimeCurrency(row.eq_utd) }}
            </template>
          </el-table-column>
          <el-table-column :label="t('dashboard.allocationShare')" min-width="130">
            <template #default="{ row }">
              {{ `${formatRuntimeNumber(row.allocationShare)}%` }}
            </template>
          </el-table-column>
        </ResponsiveTable>
      </div>
    </DataState>
  </SectionCard>
</template>

<style scoped>
.account-overview {
  display: grid;
  grid-template-columns: minmax(0, 320px) minmax(0, 1fr);
  gap: var(--ui-space-16);
  align-items: start;
  min-width: 0;
}

.account-overview__panel {
  display: flex;
  flex-direction: column;
  gap: var(--ui-space-12);
  min-width: 0;
}

.account-overview__panel-title {
  margin: 0;
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-13);
  line-height: 1.4;
  font-weight: 600;
}

.account-overview__chart {
  width: 100%;
  min-height: 280px;
}

.account-overview__table {
  min-width: 0;
}

@media (max-width: 991px) {
  .account-overview {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
