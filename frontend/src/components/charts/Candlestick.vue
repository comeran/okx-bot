<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts/core';
import { CandlestickChart, BarChart, ScatterChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ECharts, ComposeOption } from 'echarts/core';
import type { CandlestickSeriesOption, BarSeriesOption, ScatterSeriesOption } from 'echarts/charts';
import type {
  DataZoomComponentOption,
  GridComponentOption,
  LegendComponentOption,
  TitleComponentOption,
  TooltipComponentOption,
} from 'echarts/components';

import type { BacktestMarker } from '@/types/backtest';
import type { Kline } from '@/types/market';

echarts.use([
  BarChart,
  CandlestickChart,
  CanvasRenderer,
  ScatterChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
]);

type ChartOption = ComposeOption<
  | BarSeriesOption
  | CandlestickSeriesOption
  | ScatterSeriesOption
  | DataZoomComponentOption
  | GridComponentOption
  | LegendComponentOption
  | TitleComponentOption
  | TooltipComponentOption
>;

const props = withDefaults(
  defineProps<{
    klines: Kline[];
    symbol?: string;
    timeframe?: string;
    height?: number;
    markers?: BacktestMarker[];
  }>(),
  {
    symbol: '',
    timeframe: '',
    height: 420,
    markers: () => [],
  },
);

const chartRef = ref<HTMLDivElement | null>(null);
let chart: ECharts | null = null;
let resizeObserver: ResizeObserver | null = null;

const sortedKlines = computed(() =>
  [...props.klines].sort((a, b) => a.timestamp - b.timestamp),
);

const normalizeTimestamp = (timestamp: number): number => (
  timestamp < 10_000_000_000 ? timestamp * 1000 : timestamp
);

const formatTime = (timestamp: number): string => {
  const value = normalizeTimestamp(timestamp);
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
};

const buildMarkerData = (side: BacktestMarker['side'], timestampIndexes: Map<number, number>) => (
  props.markers
    .filter((marker) => marker.side === side && (!props.symbol || marker.symbol === props.symbol))
    .map((marker) => {
      const index = timestampIndexes.get(normalizeTimestamp(marker.timestamp));
      return index === undefined ? null : [index, marker.price];
    })
    .filter((item): item is [number, number] => item !== null)
);

const chartOption = computed<ChartOption>(() => {
  const times = sortedKlines.value.map((item) => formatTime(item.timestamp));
  const candles = sortedKlines.value.map((item) => [
    item.open,
    item.close,
    item.low,
    item.high,
  ]);
  const volumes = sortedKlines.value.map((item) => item.volume);
  const timestampIndexes = new Map(
    sortedKlines.value.map((item, index) => [normalizeTimestamp(item.timestamp), index]),
  );
  const buyMarkers = buildMarkerData('buy', timestampIndexes);
  const sellMarkers = buildMarkerData('sell', timestampIndexes);
  const hasMarkers = buyMarkers.length > 0 || sellMarkers.length > 0;
  const zoomStart = hasMarkers ? 0 : 70;
  const legendData = ['Price', 'Volume'];
  const series: ChartOption['series'] = [
    {
      name: 'Price',
      type: 'candlestick',
      data: candles,
      itemStyle: {
        color: '#67c23a',
        color0: '#f56c6c',
        borderColor: '#67c23a',
        borderColor0: '#f56c6c',
      },
    },
    {
      name: 'Volume',
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: volumes,
      itemStyle: {
        color: '#909399',
      },
    },
  ];

  if (buyMarkers.length > 0) {
    legendData.push('Buy');
    series.push({
      name: 'Buy',
      type: 'scatter',
      data: buyMarkers,
      symbol: 'triangle',
      symbolSize: 14,
      itemStyle: {
        color: '#67c23a',
      },
    });
  }

  if (sellMarkers.length > 0) {
    legendData.push('Sell');
    series.push({
      name: 'Sell',
      type: 'scatter',
      data: sellMarkers,
      symbol: 'triangle',
      symbolRotate: 180,
      symbolSize: 14,
      itemStyle: {
        color: '#f56c6c',
      },
    });
  }

  return {
    title: {
      text: props.symbol ? `${props.symbol} ${props.timeframe}`.trim() : 'Candlestick',
      left: 8,
      top: 0,
      textStyle: {
        fontSize: 14,
        fontWeight: 600,
      },
    },
    legend: {
      top: 0,
      right: 8,
      data: legendData,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
      },
    },
    grid: [
      {
        left: 56,
        right: 24,
        top: 48,
        height: '58%',
      },
      {
        left: 56,
        right: 24,
        top: '76%',
        height: '14%',
      },
    ],
    xAxis: [
      {
        type: 'category',
        data: times,
        boundaryGap: true,
        axisLine: { onZero: false },
        min: 'dataMin',
        max: 'dataMax',
      },
      {
        type: 'category',
        gridIndex: 1,
        data: times,
        boundaryGap: true,
        axisLabel: { show: false },
        axisTick: { show: false },
        axisLine: { show: false },
        min: 'dataMin',
        max: 'dataMax',
      },
    ],
    yAxis: [
      {
        scale: true,
        splitArea: { show: true },
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: zoomStart,
        end: 100,
      },
      {
        show: true,
        type: 'slider',
        xAxisIndex: [0, 1],
        top: '92%',
        start: zoomStart,
        end: 100,
      },
    ],
    series,
  };
});

const renderChart = () => {
  if (!chartRef.value) {
    return;
  }

  if (!chart) {
    chart = echarts.init(chartRef.value);
  }

  chart.setOption(chartOption.value, true);
};

onMounted(() => {
  renderChart();

  if (chartRef.value) {
    resizeObserver = new ResizeObserver(() => chart?.resize());
    resizeObserver.observe(chartRef.value);
  }
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  chart?.dispose();
  chart = null;
});

watch(chartOption, renderChart, { deep: true });
</script>

<template>
  <div ref="chartRef" class="candlestick-chart" :style="{ height: `${height}px` }" />
</template>

<style scoped>
.candlestick-chart {
  width: 100%;
  min-height: 320px;
}
</style>
