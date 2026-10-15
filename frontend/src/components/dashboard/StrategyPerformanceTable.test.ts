import { computed, defineComponent, h, inject, provide, type ComputedRef, type Ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';
import StrategyPerformanceTable from './StrategyPerformanceTable.vue';

const tableRowsKey = Symbol('tableRows');
const tableStateKey = Symbol('tableState');
const localeState = vi.hoisted(() => ({
  locale: null as Ref<string> | null,
}));

function createTableStubs() {
  const ElTable = defineComponent({
    name: 'ElTable',
    props: {
      data: { type: Array, default: () => [] },
      loading: { type: Boolean, default: false },
      rowKey: { type: [String, Function], default: 'name' },
      expandRowKeys: { type: Array, default: () => [] },
    },
    setup(props, { attrs, slots }) {
      provide(tableRowsKey, computed(() => props.data as unknown[]));
      provide(tableStateKey, computed(() => ({
        rowKey: props.rowKey,
        expandRowKeys: props.expandRowKeys as string[],
      })));
      return () => h('el-table', attrs, slots.default?.());
    },
  });

  const ElTableColumn = defineComponent({
    name: 'ElTableColumn',
    props: {
      type: { type: String, default: '' },
    },
    setup(props, { attrs, slots }) {
      const rows = inject<ComputedRef<unknown[]>>(tableRowsKey, computed(() => []));
      const state = inject<ComputedRef<{ rowKey: string | ((row: unknown) => string); expandRowKeys: string[] }>>(tableStateKey, computed(() => ({ rowKey: 'name', expandRowKeys: [] })));
      function rowKeyFor(row: unknown): string {
        return typeof state.value.rowKey === 'function'
          ? state.value.rowKey(row)
          : String((row as Record<string, unknown>)[state.value.rowKey] ?? '');
      }
      return () => {
        const children: unknown[] = [];
        rows.value.forEach((row, index) => {
          if (props.type === 'expand') {
            if (!state.value.expandRowKeys.includes(rowKeyFor(row))) return;
            if (slots.default) children.push(slots.default({ row, $index: index }));
            return;
          }
          if (slots.default) {
            children.push(slots.default({ row, $index: index }));
            return;
          }
          const propName = String(attrs.prop ?? '');
          children.push(h('span', String((row as Record<string, unknown>)[propName] ?? '')));
        });
        return h('el-table-column', attrs, children as any);
      };
    },
  });

  return {
    ElTable,
    ElTableColumn,
    ElButton: defineHostComponent('el-button'),
    ElEmpty: defineHostComponent('el-empty'),
  };
}

const StrategyPerformanceTableWithLocale = defineComponent({
  name: 'StrategyPerformanceTableWithLocale',
  setup(_, { attrs }) {
    const { locale } = useI18n({ useScope: 'global' });
    localeState.locale = locale;

    return () => h(StrategyPerformanceTable as any, attrs as any);
  },
});

describe('StrategyPerformanceTable', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders nullable return, win rate, and last order values as em dashes', async () => {
    const wrapper = await mount(StrategyPerformanceTable, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        rows: [
          {
            name: 'alpha',
            status: 'unknown',
            equity: 1000,
            return_pct: null,
            realized_pnl: 0,
            unrealized_pnl: 0,
            position_notional: 0,
            open_positions: 0,
            closed_trade_count: 0,
            win_rate: null,
            fees_paid: 0,
            order_count: 0,
            filled_order_count: 0,
            last_order_at: null,
            positions: [],
            recent_orders: [],
          },
        ],
      },
    });

    const returnColumn = wrapper.findAll((node) => node.type === 'el-table-column' && node.props.label === 'Return %')[0];
    const winRateColumn = wrapper.findAll((node) => node.type === 'el-table-column' && node.props.label === 'Win Rate')[0];

    await wrapper.trigger(wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Expand alpha'), 'click');

    const summaryItems = wrapper.findAll((node) => String(node.props.class).includes('strategy-performance-table__summary-item'));
    const lastOrderValue = summaryItems[3].children.find((child) => child.type === 'strong');

    expect(textContent(returnColumn)).toBe('—');
    expect(textContent(winRateColumn)).toBe('—');
    expect(textContent(lastOrderValue as never)).toBe('—');

    wrapper.unmount();
  });

  it('keeps the last successful table visible beside a refresh warning', async () => {
    const wrapper = await mount(StrategyPerformanceTable, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        rows: [
          {
            name: 'alpha',
            status: 'running',
            equity: 1000,
            return_pct: 0.05,
            realized_pnl: 25,
            unrealized_pnl: 5,
            position_notional: 100,
            open_positions: 1,
            closed_trade_count: 3,
            win_rate: 0.66,
            fees_paid: 1.25,
            order_count: 8,
            filled_order_count: 6,
            last_order_at: 1700000000000,
            positions: [],
            recent_orders: [],
          },
        ],
        error: 'refresh failed',
        stale: true,
      },
    });

    expect(wrapper.text()).toContain('Stale data');
    expect(wrapper.text()).toContain('refresh failed');
    expect(wrapper.text()).toContain('alpha');
    expect(wrapper.text()).toContain('Running');

    wrapper.unmount();
  });

  it('shows loading overlay and keeps the previous rows visible while refreshing', async () => {
    const wrapper = await mount(StrategyPerformanceTable, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        rows: [
          {
            name: 'alpha',
            status: 'running',
            equity: 1000,
            return_pct: 0.05,
            realized_pnl: 25,
            unrealized_pnl: 5,
            position_notional: 100,
            open_positions: 1,
            closed_trade_count: 3,
            win_rate: 0.66,
            fees_paid: 1.25,
            order_count: 8,
            filled_order_count: 6,
            last_order_at: 1700000000000,
            positions: [],
            recent_orders: [],
          },
        ],
        loading: true,
      },
    });

    const scrollRegion = wrapper.find((node) => String(node.props.class).includes('responsive-table__scroll-region'));
    const viewport = wrapper.find((node) => String(node.props.class).includes('responsive-table__viewport'));
    const loadingOverlay = wrapper.find((node) => String(node.props.class).includes('responsive-table__loading-overlay'));

    expect(scrollRegion.props['aria-busy']).toBe(true);
    expect(textContent(viewport)).toContain('alpha');
    expect(textContent(viewport)).toContain('Running');
    expect(loadingOverlay.props.role).toBe('status');
    expect(loadingOverlay.props.style).toEqual({ pointerEvents: 'auto', zIndex: '1' });
    expect(viewport.props.style).toEqual({ pointerEvents: 'none' });
    expect(viewport.props.inert).toBeTruthy();
    expect(viewport.props['aria-hidden']).toBe(true);

    wrapper.unmount();
  });

  it('toggles an accessible expand control and details for a strategy row', async () => {
    const wrapper = await mount(StrategyPerformanceTable, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        rows: [
          {
            name: 'alpha',
            status: 'running',
            equity: 1000,
            return_pct: 0.05,
            realized_pnl: 25,
            unrealized_pnl: 5,
            position_notional: 100,
            open_positions: 1,
            closed_trade_count: 3,
            win_rate: 0.66,
            fees_paid: 1.25,
            order_count: 8,
            filled_order_count: 6,
            last_order_at: 1700000000000,
            positions: [
              {
                strategy: 'alpha',
                symbol: 'BTC-USDT',
                side: 'long',
                amount: 1,
                entry_price: 100,
                mark_price: 110,
                unrealized_pnl: 10,
              },
            ],
            recent_orders: [],
          },
        ],
      },
    });

    const expandButton = wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Expand alpha');
    expect(expandButton.props['aria-expanded']).toBe('false');
    expect(wrapper.text()).not.toContain('Fees Paid');

    await wrapper.trigger(expandButton, 'click');

    expect(wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Collapse alpha').props['aria-expanded']).toBe('true');
    expect(wrapper.text()).toContain('Fees Paid');
    expect(wrapper.text()).toContain('BTC-USDT');

    await wrapper.trigger(wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Collapse alpha'), 'click');

    expect(wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Expand alpha').props['aria-expanded']).toBe('false');
    expect(wrapper.text()).not.toContain('Fees Paid');

    wrapper.unmount();
  });

  it('keeps expanded detail ids distinct for similarly normalized strategy names', async () => {
    const wrapper = await mount(StrategyPerformanceTable, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        rows: [
          {
            name: 'desk:btc',
            status: 'running',
            equity: 1000,
            return_pct: 0.05,
            realized_pnl: 25,
            unrealized_pnl: 5,
            position_notional: 100,
            open_positions: 1,
            closed_trade_count: 3,
            win_rate: 0.66,
            fees_paid: 1.25,
            order_count: 8,
            filled_order_count: 6,
            last_order_at: 1700000000000,
            positions: [],
            recent_orders: [],
          },
          {
            name: 'desk-btc',
            status: 'running',
            equity: 1000,
            return_pct: 0.05,
            realized_pnl: 25,
            unrealized_pnl: 5,
            position_notional: 100,
            open_positions: 1,
            closed_trade_count: 3,
            win_rate: 0.66,
            fees_paid: 1.25,
            order_count: 8,
            filled_order_count: 6,
            last_order_at: 1700000000000,
            positions: [],
            recent_orders: [],
          },
        ],
      },
    });

    const firstButton = wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Expand desk:btc');
    const secondButton = wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Expand desk-btc');

    expect(firstButton.props['aria-controls']).not.toBe(secondButton.props['aria-controls']);

    await wrapper.trigger(firstButton, 'click');

    const firstDetailId = String(firstButton.props['aria-controls']);
    expect(wrapper.getById(firstDetailId).props.id).toBe(firstDetailId);
    expect(wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Collapse desk:btc').props['aria-expanded']).toBe('true');

    await wrapper.trigger(secondButton, 'click');

    const secondDetailId = String(secondButton.props['aria-controls']);
    expect(wrapper.getById(secondDetailId).props.id).toBe(secondDetailId);
    expect(wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Collapse desk-btc').props['aria-expanded']).toBe('true');

    wrapper.unmount();
  });

  it('reformats the expanded last order timestamp when locale changes', async () => {
    const wrapper = await mount(StrategyPerformanceTableWithLocale, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        rows: [
          {
            name: 'alpha',
            status: 'running',
            equity: 1000,
            return_pct: 0.05,
            realized_pnl: 25,
            unrealized_pnl: 5,
            position_notional: 100,
            open_positions: 1,
            closed_trade_count: 3,
            win_rate: 0.66,
            fees_paid: 1.25,
            order_count: 8,
            filled_order_count: 6,
            last_order_at: 1700000000000,
            positions: [],
            recent_orders: [],
          },
        ],
      },
    });

    const expandButton = wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Expand alpha');
    await wrapper.trigger(expandButton, 'click');

    const summaryItems = wrapper.findAll((node) => String(node.props.class).includes('strategy-performance-table__summary-item'));
    const lastOrderValue = summaryItems[3].children.find((child) => child.type === 'strong');
    const expectedEn = new Date(1700000000000).toLocaleString('en-US');
    const expectedZh = new Date(1700000000000).toLocaleString('zh-CN');

    expect(textContent(lastOrderValue as never)).toBe(expectedEn);

    if (!localeState.locale) {
      throw new Error('Locale ref was not initialized');
    }
    localeState.locale.value = 'zh-CN';
    await wrapper.flush();

    expect(textContent(lastOrderValue as never)).toBe(expectedZh);
    wrapper.unmount();
  });

  it('renders expanded fees, nested positions, and recent orders', async () => {
    const wrapper = await mount(StrategyPerformanceTable, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        rows: [
          {
            name: 'alpha',
            status: 'running',
            equity: 1000,
            return_pct: 0.05,
            realized_pnl: 25,
            unrealized_pnl: 5,
            position_notional: 100,
            open_positions: 1,
            closed_trade_count: 3,
            win_rate: 0.66,
            fees_paid: 1.25,
            order_count: 8,
            filled_order_count: 6,
            last_order_at: 1700000000000,
            positions: [
              {
                strategy: 'alpha',
                symbol: 'BTC-USDT',
                side: 'long',
                amount: 1,
                entry_price: 100,
                mark_price: 110,
                unrealized_pnl: 10,
              },
            ],
            recent_orders: [
              {
                strategy: 'alpha',
                symbol: 'BTC-USDT',
                side: 'buy',
                type: 'limit',
                price: 100,
                amount: 1,
                status: 'filled',
                timestamp: 1700000001000,
              },
            ],
          },
        ],
      },
    });

    await wrapper.trigger(wrapper.find((node) => (node.type === 'el-button' || node.type === 'button') && node.props['aria-label'] === 'Expand alpha'), 'click');

    expect(wrapper.text()).toContain('Fees Paid');
    expect(wrapper.text()).toContain('$1.25');
    expect(wrapper.text()).toContain('BTC-USDT');
    expect(wrapper.text()).toContain('limit');
    expect(wrapper.text()).toContain('Last Order');

    wrapper.unmount();
  });
});
