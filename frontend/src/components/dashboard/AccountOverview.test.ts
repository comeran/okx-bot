import { computed, defineComponent, h, inject, provide, type ComputedRef } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount } from '@/test-utils/mount';
import AccountOverview from './AccountOverview.vue';

const echartsMock = vi.hoisted(() => ({
  init: vi.fn(),
  getInstanceByDom: vi.fn(),
  use: vi.fn(),
}));

vi.mock('echarts/core', () => ({
  init: echartsMock.init,
  getInstanceByDom: echartsMock.getInstanceByDom,
  use: echartsMock.use,
}));

vi.mock('echarts/charts', () => ({
  PieChart: {},
}));

vi.mock('echarts/components', () => ({
  LegendComponent: {},
  TooltipComponent: {},
}));

vi.mock('echarts/renderers', () => ({
  CanvasRenderer: {},
}));

const tableRowsKey = Symbol('tableRows');

function createTableStubs() {
  const ElTable = defineComponent({
    name: 'ElTable',
    props: {
      data: { type: Array, default: () => [] },
      loading: { type: Boolean, default: false },
    },
    setup(props, { attrs, slots }) {
      provide(tableRowsKey, computed(() => props.data as unknown[]));
      return () => h('el-table', attrs, slots.default?.());
    },
  });

  const ElTableColumn = defineComponent({
    name: 'ElTableColumn',
    setup(_props, { attrs, slots }) {
      const rows = inject<ComputedRef<unknown[]>>(tableRowsKey, computed(() => []));
      return () => h('el-table-column', attrs, rows.value.map((row, index) => {
        if (slots.default) return slots.default({ row, $index: index });
        const propName = String(attrs.prop ?? '');
        return h('span', String((row as Record<string, unknown>)[propName] ?? ''));
      }));
    },
  });

  return {
    ElTable,
    ElTableColumn,
    ElEmpty: defineHostComponent('el-empty'),
  };
}

describe('AccountOverview', () => {
  beforeEach(() => {
    echartsMock.init.mockReset();
    echartsMock.getInstanceByDom.mockReset();
    echartsMock.use.mockReset();
    echartsMock.getInstanceByDom.mockReturnValue(null);
  });

  it('renders the empty state without initializing an empty chart', async () => {
    const wrapper = await mount(AccountOverview, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        assets: [],
      },
    });

    await wrapper.flush();

    expect(wrapper.text()).toContain('No account assets');
    expect(echartsMock.init).not.toHaveBeenCalled();
    expect(echartsMock.getInstanceByDom).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it('does not initialize the chart for an initial error without assets', async () => {
    const wrapper = await mount(AccountOverview, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        assets: [],
        error: 'load failed',
      },
    });

    await wrapper.flush();

    expect(wrapper.text()).toContain('load failed');
    expect(echartsMock.init).not.toHaveBeenCalled();
    expect(echartsMock.getInstanceByDom).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it('keeps the existing chart and assets visible when refreshed data becomes stale', async () => {
    const chart = {
      setOption: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
    };
    echartsMock.init.mockReturnValue(chart);

    const wrapper = await mount(AccountOverview, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        assets: [
          { ccy: 'USDT', cash_bal: 100, eq: 100, eq_utd: 100, avail_bal: 90, upl: 0 },
          { ccy: 'BTC', cash_bal: 0, eq: 0.5, eq_utd: 50, avail_bal: 0, upl: 2 },
        ],
      },
    });

    await wrapper.flush();

    expect(echartsMock.init).toHaveBeenCalledTimes(1);
    expect(chart.setOption).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain('USDT');
    expect(wrapper.text()).toContain('$100.00');

    await wrapper.updateProps({ accountError: 'refresh failed', stale: true });
    await wrapper.flush();

    expect(chart.dispose).not.toHaveBeenCalled();
    expect(echartsMock.init).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain('Stale data');
    expect(wrapper.text()).toContain('refresh failed');
    expect(wrapper.text()).toContain('USDT');
    expect(wrapper.text()).toContain('$100.00');

    wrapper.unmount();
  });

  it.each([
    ['loading', { loading: true }],
    ['empty', { assets: [] }],
  ])('disposes the pie chart when the visible state turns %s', async (_label, nextProps) => {
    const chart = {
      setOption: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
    };
    echartsMock.init.mockReturnValue(chart);

    const wrapper = await mount(AccountOverview, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        assets: [
          { ccy: 'USDT', cash_bal: 100, eq: 100, eq_utd: 100, avail_bal: 90, upl: 0 },
          { ccy: 'BTC', cash_bal: 0, eq: 0.5, eq_utd: 50, avail_bal: 0, upl: 2 },
        ],
      },
    });

    await wrapper.flush();

    expect(echartsMock.init).toHaveBeenCalledTimes(1);
    expect(chart.setOption).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain('USDT');
    expect(wrapper.text()).toContain('$100.00');
    expect(wrapper.text()).toContain('66.66666667%');

    await wrapper.updateProps(nextProps);
    await wrapper.flush();

    expect(chart.dispose).toHaveBeenCalledTimes(1);
    if ('assets' in nextProps && nextProps.assets.length === 0) {
      expect(wrapper.text()).toContain('No account assets');
    }

    wrapper.unmount();
  });

  it('recreates the pie chart when visibility returns after loading or error', async () => {
    const firstChart = {
      setOption: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
    };
    const secondChart = {
      setOption: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
    };
    echartsMock.init.mockReturnValueOnce(firstChart).mockReturnValueOnce(secondChart);

    const wrapper = await mount(AccountOverview, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        assets: [
          { ccy: 'USDT', cash_bal: 100, eq: 100, eq_utd: 100, avail_bal: 90, upl: 0 },
        ],
      },
    });

    await wrapper.flush();

    expect(echartsMock.init).toHaveBeenCalledTimes(1);
    expect(firstChart.setOption).toHaveBeenCalledTimes(1);

    await wrapper.updateProps({ loading: true });
    await wrapper.flush();

    expect(firstChart.dispose).toHaveBeenCalledTimes(1);

    await wrapper.updateProps({ loading: false, error: null });
    await wrapper.flush();

    expect(echartsMock.init).toHaveBeenCalledTimes(2);
    expect(secondChart.setOption).toHaveBeenCalledTimes(1);
    expect(secondChart.resize).toHaveBeenCalledTimes(1);

    wrapper.unmount();
  });
});
