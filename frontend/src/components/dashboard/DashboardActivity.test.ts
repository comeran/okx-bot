import { computed, defineComponent, h, inject, provide, type ComputedRef, type Ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';
import DashboardActivity from './DashboardActivity.vue';

const tableRowsKey = Symbol('tableRows');
const localeState = vi.hoisted(() => ({
  locale: null as Ref<string> | null,
}));

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
    ElTooltip: defineHostComponent('el-tooltip'),
  };
}

const DashboardActivityWithLocale = defineComponent({
  name: 'DashboardActivityWithLocale',
  setup(_, { attrs }) {
    const { locale } = useI18n({ useScope: 'global' });
    localeState.locale = locale;

    return () => h(DashboardActivity as any, attrs as any);
  },
});

describe('DashboardActivity', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('reformats received timestamps when locale changes', async () => {
    const wrapper = await mount(DashboardActivityWithLocale, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        recentOrders: [
          { symbol: 'BTC-USDT', side: 'buy', type: 'limit', price: 100, amount: 1, status: 'filled', timestamp: 1700000000000 },
        ],
        positions: [],
        runtimeSummaries: [],
        websocketMessages: [
          { type: 'raw', data: 'payload', received_at: 1700000001000 },
        ],
        loading: false,
      },
    });

    expect(wrapper.text()).toContain(new Date(1700000001000).toLocaleString('en-US'));

    if (!localeState.locale) {
      throw new Error('Locale ref was not initialized');
    }
    localeState.locale.value = 'zh-CN';
    await wrapper.flush();

    expect(wrapper.text()).toContain(new Date(1700000001000).toLocaleString('zh-CN'));
    wrapper.unmount();
  });

  it('renders the activity sections and clamps long payload previews with accessible full values', async () => {
    const longPayload = 'x'.repeat(160);
    const wrapper = await mount(DashboardActivity, {
      locale: 'en',
      components: createTableStubs(),
      props: {
        recentOrders: [
          { symbol: 'BTC-USDT', side: 'buy', type: 'limit', price: 100, amount: 1, status: 'filled', timestamp: 1700000000000 },
        ],
        positions: [
          { symbol: 'BTC-USDT', side: 'long', amount: 1, entry_price: 100, mark_price: 110, unrealized_pnl: 10 },
        ],
        runtimeSummaries: [
          { name: 'alpha', status: 'running' },
        ],
        runtimeErrors: {
          alpha: 'late fill',
        },
        websocketMessages: [
          { type: 'raw', data: longPayload, received_at: 1700000001000 },
        ],
        loading: false,
      },
    });

    expect(wrapper.text()).toContain('Recent Orders');
    expect(wrapper.text()).toContain('Positions');
    expect(wrapper.text()).toContain('Strategies');
    expect(wrapper.text()).toContain('WebSocket Messages');
    expect(wrapper.text()).toContain('alpha');
    expect(wrapper.text()).toContain('Running');
    expect(wrapper.text()).toContain('late fill');

    const payload = wrapper.find((node) => node.type === 'code' && String(node.props.class).includes('dashboard-activity__payload'));
    const tooltip = wrapper.find((node) => node.type === 'el-tooltip');

    expect(textContent(payload)).toContain('…');
    expect(payload.props['aria-label']).toBe(longPayload);
    expect(payload.props.title).toBe(longPayload);
    expect(tooltip.props.content).toBe(longPayload);

    wrapper.unmount();
  });
});
