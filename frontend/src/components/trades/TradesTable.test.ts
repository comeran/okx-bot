import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { defineComponent, h } from 'vue';
import { describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';
import type { TradeRecord } from '@/types/trades';

const tradeUtils = vi.hoisted(() => ({
  formatTradeNumber: vi.fn((value?: number | null, locale = 'en') => (
    value === null || value === undefined ? '—' : `${locale}:${value}`
  )),
  formatTradeTimestamp: vi.fn((value?: number | null, locale = 'en') => (
    value === null || value === undefined ? '—' : `${locale}:${value}`
  )),
}));

vi.mock('@/utils/trades', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/trades')>(),
  formatTradeNumber: tradeUtils.formatTradeNumber,
  formatTradeTimestamp: tradeUtils.formatTradeTimestamp,
}));

const TradesTable = (await import('./TradesTable.vue')).default;
const tradesTableSource = readFileSync(fileURLToPath(new URL('./TradesTable.vue', import.meta.url)), 'utf8');

const rows: TradeRecord[] = [
  {
    id: 11,
    strategy: 'ma_cross',
    symbol: 'BTC-USDT',
    side: 'buy',
    amount: 0.125,
    price: 62000,
    fee: 1.5,
    timestamp: 1700000000000,
  },
  {
    id: 12,
    strategy: 'rsi_mean_reversion',
    symbol: 'ETH-USDT',
    side: 'sell',
    amount: 1.5,
    price: 3100,
    fee: 0.75,
    timestamp: 1700003600000,
  },
];

let currentRows: Array<TradeRecord & { rowKey: string | number }> = [];

const components = {
  ElTable: defineComponent({
    name: 'ElTable',
    props: {
      data: { type: Array, default: () => [] },
      loading: { type: Boolean, default: false },
    },
    setup(props, { attrs, slots }) {
      return () => {
        currentRows = props.data as Array<TradeRecord & { rowKey: string | number }>;
        const isEmpty = (props.data as TradeRecord[]).length === 0 && !props.loading;
        return h('el-table', attrs, [
          slots.default?.(),
          isEmpty ? slots.empty?.() : null,
        ]);
      };
    },
  }),
  ElTableColumn: defineComponent({
    name: 'ElTableColumn',
    setup(_props, { attrs, slots }) {
      return () => h('el-table-column', attrs, slots.default?.({ row: currentRows[0] ?? rows[0] }));
    },
  }),
  ElButton: defineHostComponent('el-button'),
};

describe('TradesTable', () => {
  it('renders the trade columns, semantic side badge, and locale-aware timestamps', async () => {
    expect(tradesTableSource).toMatch(/prop="amount"[\s\S]*?align="right"[\s\S]*?header-align="right"/);
    expect(tradesTableSource).toMatch(/prop="price"[\s\S]*?align="right"[\s\S]*?header-align="right"/);
    expect(tradesTableSource).toMatch(/prop="fee"[\s\S]*?align="right"[\s\S]*?header-align="right"/);

    const wrapper = await mount(TradesTable, {
      locale: 'zh-CN',
      components,
      props: {
        trades: rows,
      },
    });

    const scrollRegion = wrapper.find((node) => node.props.role === 'region');
    expect(String(scrollRegion.props.class)).toContain('responsive-table__scroll-region');
    expect(scrollRegion.props['aria-label']).toBe('交易历史表格');
    expect(scrollRegion.props['aria-describedby']).toMatch(/^responsive-table-description-/);
    expect(scrollRegion.props.tabindex).toBe('0');
    expect(scrollRegion.props.style).toEqual({ 'overflow-x': 'auto' });

    const columns = wrapper.findAll((node) => node.type === 'el-table-column');
    const labels = columns.map((column) => column.props.label);
    expect(labels).toEqual([
      '时间',
      '策略',
      '交易对',
      '方向',
      '数量',
      '价格',
      '手续费',
    ]);
    expect(columns
      .filter((column) => ['amount', 'price', 'fee'].includes(String(column.props.prop)))
      .map((column) => ({
        prop: column.props.prop,
        align: column.props.align,
        headerAlign: column.props['header-align'] ?? column.props.headerAlign,
      })))
      .toEqual([
        { prop: 'amount', align: 'right', headerAlign: 'right' },
        { prop: 'price', align: 'right', headerAlign: 'right' },
        { prop: 'fee', align: 'right', headerAlign: 'right' },
      ]);

    expect(wrapper.text()).toContain('zh-CN:1700000000000');
    expect(wrapper.text()).toContain('zh-CN:0.125');
    expect(wrapper.text()).toContain('zh-CN:62000');
    expect(wrapper.text()).toContain('zh-CN:1.5');
    expect(wrapper.text()).toContain('买入');

    const badge = wrapper.find((node) => String(node.props.class).includes('status-badge'));
    expect(badge.props['aria-label']).toBe('买入');
    expect(badge.props.role).toBe('status');

    expect(tradeUtils.formatTradeTimestamp).toHaveBeenCalledWith(1700000000000, 'zh-CN');
    expect(tradeUtils.formatTradeNumber).toHaveBeenCalledWith(0.125, 'zh-CN');

    wrapper.unmount();
  });

  it('uses stable unique row keys for null-id trades and keeps backend ids intact', async () => {
    const nullIdTradeA: TradeRecord = {
      id: null,
      strategy: 'ma_cross',
      symbol: 'BTC-USDT',
      side: 'buy',
      amount: 0.125,
      price: 62000,
      fee: 1.5,
      timestamp: 1700000000000,
    };
    const nullIdTradeB: TradeRecord = {
      ...nullIdTradeA,
      symbol: 'ETH-USDT',
      side: 'sell',
      amount: 1.5,
      price: 3100,
      fee: 0.75,
      timestamp: 1700003600000,
    };
    const idTrade: TradeRecord = {
      ...nullIdTradeA,
      id: 88,
    };

    const initialTrades = [
      nullIdTradeA,
      { ...nullIdTradeA },
      nullIdTradeB,
      idTrade,
    ];
    const refreshedTrades = initialTrades.map((trade) => ({ ...trade }));

    const wrapper = await mount(TradesTable, {
      locale: 'en',
      components,
      props: {
        trades: initialTrades,
      },
    });

    const table = wrapper.find((node) => node.type === 'el-table');
    expect(table.props['row-key']).toBe('rowKey');
    const initialRowKeys = currentRows.map((row) => row.rowKey);
    expect(initialRowKeys).toHaveLength(4);
    expect(initialRowKeys[0]).not.toBe(initialRowKeys[1]);
    expect(initialRowKeys[0]).not.toBe(initialRowKeys[2]);
    expect(initialRowKeys[1]).not.toBe(initialRowKeys[2]);
    expect(initialRowKeys[3]).toBe(88);

    wrapper.unmount();

    const rerenderWrapper = await mount(TradesTable, {
      locale: 'en',
      components,
      props: {
        trades: refreshedTrades,
      },
    });

    expect(currentRows.map((row) => row.rowKey)).toEqual(initialRowKeys);
    expect(currentRows[3].rowKey).toBe(88);

    rerenderWrapper.unmount();
  });

  it('keeps nullable numeric values visible as an em dash and preserves the accessible empty state', async () => {
    const wrapper = await mount(TradesTable, {
      locale: 'en',
      components,
      props: {
        trades: [
          {
            id: 13,
            strategy: 'nullable',
            symbol: 'SOL-USDT',
            side: 'sell',
            amount: null,
            price: null,
            fee: null,
            timestamp: null,
          } as unknown as TradeRecord,
        ],
        emptyDescription: 'No rows match the current filters.',
      },
    });

    expect(wrapper.text()).toContain('—');
    expect(wrapper.text()).toContain('Sell');
    expect(textContent(wrapper.find((node) => node.props.role === 'status'))).toContain('Sell');
    expect(wrapper.findAll((node) => node.props.role === 'status')).toHaveLength(1);

    wrapper.unmount();
  });

  it('shows a labeled empty state when no filtered trades are left', async () => {
    const wrapper = await mount(TradesTable, {
      locale: 'en',
      components,
      props: {
        trades: [],
        emptyDescription: 'No trades match the current filters.',
      },
    });

    const emptyState = wrapper.find((node) => node.type === 'div' && String(node.props.class).includes('trades-table__empty'));
    expect(textContent(emptyState)).toBe('No trades match the current filters.');
    expect(wrapper.find((node) => node.props.role === 'region').props['aria-label']).toBe('Trade history table');

    wrapper.unmount();
  });
});
