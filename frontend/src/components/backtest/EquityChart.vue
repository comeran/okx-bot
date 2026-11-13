<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ECharts, ComposeOption } from 'echarts/core';
import type { LineSeriesOption } from 'echarts/charts';
import type {
  GridComponentOption,
  LegendComponentOption,
  TooltipComponentOption,
} from 'echarts/components';

import type { BacktestEquityPoint } from '@/types/backtest';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

type ChartOption = ComposeOption<
  | LineSeriesOption
  | GridComponentOption
  | LegendComponentOption
  | TooltipComponentOption
>;

const props = withDefaults(defineProps<{
  points: BacktestEquityPoint[];
  height?: number;
}>(), {
  height: 280,
});

const { t } = useI18n();
const chartRef = ref<HTMLDivElement | null>(null);
let chart: ECharts | null = null;

function renderChart(): void {
  if (!chart || props.points.length === 0) return;
  const option: ChartOption = {
    tooltip: { trigger: 'axis' },
    legend: { data: [t('backtest.equity'), t('backtest.drawdown')] },
    grid: { left: 48, right: 48, top: 32, bottom: 32 },
    xAxis: {
      type: 'category',
      data: props.points.map((point) => String(point.seq)),
    },
    yAxis: [
      { type: 'value', name: t('backtest.equity') },
      {
        type: 'value',
        name: t('backtest.drawdown'),
        axisLabel: { formatter: (value: number) => `${(value * 100).toFixed(1)}%` },
      },
    ],
    series: [
      {
        name: t('backtest.equity'),
        type: 'line',
        showSymbol: false,
        data: props.points.map((point) => point.equity),
      },
      {
        name: t('backtest.drawdown'),
        type: 'line',
        yAxisIndex: 1,
        showSymbol: false,
        data: props.points.map((point) => point.drawdown),
      },
    ],
  };
  chart.setOption(option);
}

onMounted(() => {
  if (!chartRef.value) return;
  chart = echarts.init(chartRef.value);
  renderChart();
});

watch(() => props.points, renderChart, { deep: true });

onBeforeUnmount(() => {
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div
    ref="chartRef"
    class="equity-chart"
    :style="{ height: `${height}px` }"
    role="img"
    :aria-label="t('backtest.equityCurve')"
  />
</template>

<style scoped>
.equity-chart {
  width: 100%;
  min-width: 0;
}
</style>
