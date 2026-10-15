<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import * as echarts from 'echarts/core';
import { BarChart, CandlestickChart, ScatterChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ECharts, ComposeOption } from 'echarts/core';
import type { BarSeriesOption, CandlestickSeriesOption, ScatterSeriesOption } from 'echarts/charts';
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

const { t, locale } = useI18n();
const chartRef = ref<HTMLDivElement | null>(null);
let chart: ECharts | null = null;
let resizeObserver: ResizeObserver | null = null;

const sortedKlines = computed(() => [...props.klines].sort((a, b) => a.timestamp - b.timestamp));
const hasKlines = computed(() => sortedKlines.value.length > 0);

const normalizeTimestamp = (timestamp: number): number => (
  timestamp < 10_000_000_000 ? timestamp * 1000 : timestamp
);

const resolveTokenColor = (tokenName: string, fallback: string): string => {
  if (typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(tokenName).trim();
  return value || fallback;
};

const formatTime = (timestamp: number, currentLocale = locale.value): string => {
  const value = normalizeTimestamp(timestamp);
  return new Intl.DateTimeFormat(currentLocale, {
    month: 'long',
    weekday: 'long',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
};

const buildAxisLabelFormatter = () => (value: string | number): string => formatTime(Number(value));

type ChartLegendSelection = Record<string, boolean>;

type ChartSnapshot = {
  legend?: Array<{ selected?: ChartLegendSelection }>;
  series?: Array<{ name?: string }>;
};

interface ChartLabels {
  title: string;
  price: string;
  volume: string;
  buy: string;
  sell: string;
  legendData: string[];
}

const buildMarkerData = (side: BacktestMarker['side'], timestampIndexes: Map<number, number>) => (
  props.markers
    .filter((marker) => marker.side === side && (!props.symbol || marker.symbol === props.symbol))
    .map((marker) => {
      const index = timestampIndexes.get(normalizeTimestamp(marker.timestamp));
      return index === undefined ? null : [index, marker.price];
    })
    .filter((item): item is [number, number] => item !== null)
);

const chartData = computed(() => {
  const timestamps = sortedKlines.value.map((item) => normalizeTimestamp(item.timestamp));
  const candles = sortedKlines.value.map((item) => [item.open, item.close, item.low, item.high] as [number, number, number, number]);
  const volumes = sortedKlines.value.map((item) => item.volume);
  const timestampIndexes = new Map(sortedKlines.value.map((item, index) => [normalizeTimestamp(item.timestamp), index]));
  const buyMarkers = buildMarkerData('buy', timestampIndexes);
  const sellMarkers = buildMarkerData('sell', timestampIndexes);
  const hasMarkers = buyMarkers.length > 0 || sellMarkers.length > 0;
  const zoomStart = hasMarkers ? 0 : 70;
  const colorSuccess = resolveTokenColor('--ui-color-success', '#67c23a');
  const colorDanger = resolveTokenColor('--ui-color-danger', '#f56c6c');
  const colorTextSecondary = resolveTokenColor('--ui-color-text-secondary', '#909399');
  const colorBorder = resolveTokenColor('--ui-color-border', '#dcdfe6');
  const colorSurface = resolveTokenColor('--ui-color-surface', '#ffffff');
  const colorText = resolveTokenColor('--ui-color-text', '#1f2937');
  const colorTextMuted = resolveTokenColor('--ui-color-text-secondary', '#606266');

  return {
    timestamps,
    candles,
    volumes,
    buyMarkers,
    sellMarkers,
    zoomStart,
    colorSuccess,
    colorDanger,
    colorTextSecondary,
    colorBorder,
    colorSurface,
    colorText,
    colorTextMuted,
  };
});

const chartLabels = computed<ChartLabels>(() => {
  const price = t('market.chart.price');
  const volume = t('market.chart.volume');
  const buy = t('market.chart.buy');
  const sell = t('market.chart.sell');
  const legendData = [price, volume];

  if (chartData.value.buyMarkers.length > 0) legendData.push(buy);
  if (chartData.value.sellMarkers.length > 0) legendData.push(sell);

  return {
    title: props.symbol ? `${props.symbol} ${props.timeframe}`.trim() : t('market.chart.candlestick'),
    price,
    volume,
    buy,
    sell,
    legendData,
  };
});

const buildSeriesNames = (): string[] => {
  const labels = chartLabels.value;
  const names = [labels.price, labels.volume];

  if (chartData.value.buyMarkers.length > 0) names.push(labels.buy);
  if (chartData.value.sellMarkers.length > 0) names.push(labels.sell);

  return names;
};

const buildFullChartOption = (): ChartOption => {
  const data = chartData.value;
  const labels = chartLabels.value;

  return {
    title: {
      text: labels.title,
      left: 8,
      top: 0,
      textStyle: {
        fontSize: 14,
        fontWeight: 600,
        color: data.colorText,
      },
    },
    legend: {
      top: 0,
      right: 8,
      data: labels.legendData,
      textStyle: {
        color: data.colorTextMuted,
      },
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
        backgroundColor: data.colorSurface,
        borderColor: data.colorBorder,
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
        data: data.timestamps,
        boundaryGap: true,
        axisLine: { onZero: false, lineStyle: { color: data.colorBorder } },
        axisLabel: {
          color: data.colorTextMuted,
          formatter: buildAxisLabelFormatter(),
        },
        axisTick: { lineStyle: { color: data.colorBorder } },
        min: 'dataMin',
        max: 'dataMax',
      },
      {
        type: 'category',
        gridIndex: 1,
        data: data.timestamps,
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
        axisLine: { lineStyle: { color: data.colorBorder } },
        axisLabel: { color: data.colorTextMuted },
        splitLine: { lineStyle: { color: data.colorBorder } },
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
        start: data.zoomStart,
        end: 100,
      },
      {
        show: true,
        type: 'slider',
        xAxisIndex: [0, 1],
        top: '92%',
        start: data.zoomStart,
        end: 100,
      },
    ],
    series: [
      {
        id: 'price',
        name: labels.price,
        type: 'candlestick',
        data: data.candles,
        itemStyle: {
          color: data.colorSuccess,
          color0: data.colorDanger,
          borderColor: data.colorSuccess,
          borderColor0: data.colorDanger,
        },
      },
      {
        id: 'volume',
        name: labels.volume,
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: data.volumes,
        itemStyle: {
          color: data.colorTextSecondary,
        },
      },
      ...(data.buyMarkers.length > 0
        ? [{
            id: 'buy',
            name: labels.buy,
            type: 'scatter' as const,
            data: data.buyMarkers,
            symbol: 'triangle',
            symbolSize: 14,
            itemStyle: {
              color: data.colorSuccess,
            },
          }]
        : []),
      ...(data.sellMarkers.length > 0
        ? [{
            id: 'sell',
            name: labels.sell,
            type: 'scatter' as const,
            data: data.sellMarkers,
            symbol: 'triangle',
            symbolRotate: 180,
            symbolSize: 14,
            itemStyle: {
              color: data.colorDanger,
            },
          }]
        : []),
    ],
  };
};

const buildLocalePatch = (): ChartOption => {
  const labels = chartLabels.value;
  const data = chartData.value;
  const currentOption = chart?.getOption() as ChartSnapshot | undefined;
  const currentSeriesNames = currentOption?.series?.map((series) => series.name).filter((name): name is string => Boolean(name)) ?? [];
  const selected = currentOption?.legend?.[0]?.selected ?? {};
  const nextSeriesNames = buildSeriesNames();
  const preservedSelection = Object.keys(selected).length > 0
    ? nextSeriesNames.reduce<ChartLegendSelection>((accumulator, nextName, index) => {
        const previousName = currentSeriesNames[index];
        if (previousName) accumulator[nextName] = selected[previousName] ?? true;
        return accumulator;
      }, {})
    : undefined;

  return {
    title: {
      text: labels.title,
      left: 8,
      top: 0,
      textStyle: {
        fontSize: 14,
        fontWeight: 600,
        color: data.colorText,
      },
    },
    legend: {
      top: 0,
      right: 8,
      data: labels.legendData,
      textStyle: {
        color: data.colorTextMuted,
      },
      ...(preservedSelection ? { selected: preservedSelection } : {}),
    },
    xAxis: [
      {
        axisLabel: {
          formatter: buildAxisLabelFormatter(),
        },
      },
    ],
    series: [
      {
        id: 'price',
        name: labels.price,
      },
      {
        id: 'volume',
        name: labels.volume,
      },
      ...(data.buyMarkers.length > 0
        ? [{
            id: 'buy',
            name: labels.buy,
          }]
        : []),
      ...(data.sellMarkers.length > 0
        ? [{
            id: 'sell',
            name: labels.sell,
          }]
        : []),
    ],
  };
};

const disposeChart = () => {
  chart?.dispose();
  chart = null;
};

const renderChart = () => {
  if (!chartRef.value || !hasKlines.value) {
    disposeChart();
    return;
  }

  if (!chart) {
    chart = echarts.init(chartRef.value);
  }

  chart.setOption(buildFullChartOption(), true);
};

const patchChartLocale = () => {
  if (!chart || !hasKlines.value) return;
  chart.setOption(buildLocalePatch(), false);
};

onMounted(() => {
  renderChart();

  if (chartRef.value && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => chart?.resize());
    resizeObserver.observe(chartRef.value);
  }
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  disposeChart();
});

watch([chartData, () => props.symbol, () => props.timeframe], renderChart, { flush: 'post' });
watch(locale, patchChartLocale, { flush: 'post' });
</script>

<template>
  <div class="candlestick-chart" :style="{ minHeight: `${height}px` }">
    <div v-show="hasKlines" ref="chartRef" class="candlestick-chart__canvas" :style="{ height: `${height}px` }" />
    <div v-if="!hasKlines" class="candlestick-chart__empty" :style="{ height: `${height}px` }" aria-hidden="true" />
  </div>
</template>

<style scoped>
.candlestick-chart {
  width: 100%;
}

.candlestick-chart__canvas,
.candlestick-chart__empty {
  width: 100%;
  min-height: 320px;
}

.candlestick-chart__empty {
  border-radius: var(--ui-radius-8);
  background: linear-gradient(180deg, color-mix(in srgb, var(--ui-color-surface) 92%, transparent), var(--ui-color-surface));
}
</style>
