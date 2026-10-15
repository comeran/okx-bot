import { describe, expect, it } from 'vitest';
import { defineComponent, h } from 'vue';

import { mount, textContent } from '../../test-utils/mount';
import ResponsiveTable from './ResponsiveTable.vue';

const ElTable = defineComponent({
  name: 'ElTable',
  props: {
    data: { type: Array, default: () => [] },
    loading: { type: Boolean, default: false },
  },
  setup(props, { attrs, slots }) {
    return () => h('el-table', attrs, [
      slots.header?.({ columns: ['symbol'] }),
      slots.append?.(),
      slots.summary?.({ columns: [{ key: 'symbol' }], data: props.data }),
      (props.data as unknown[]).map((row) => h('div', { class: 'responsive-table__row-text' }, String((row as Record<string, unknown>).symbol ?? ''))),
      !props.loading && (props.data as unknown[]).length === 0
        ? h('div', { id: 'empty-marker' }, slots.empty?.())
        : null,
      slots.default?.(),
    ]);
  },
});

const ElTableColumn = defineComponent({
  name: 'ElTableColumn',
  setup(_props, { attrs }) {
    return () => h('el-table-column', attrs);
  },
});

describe('ResponsiveTable', () => {
  it('marks the scroll wrapper with an accessible label', async () => {
    const wrapper = await mount(ResponsiveTable, {
      locale: 'en',
      components: {
        ElTable,
        ElTableColumn,
      },
      props: {
        data: [{ symbol: 'BTC-USDT' }],
      },
      directives: {},
    });

    const region = wrapper.find((node) => node.props.role === 'region');
    expect(String(region.props.class)).toContain('responsive-table__scroll-region');
    expect(region.props['aria-label']).toBe('Scrollable table');
    expect(region.props['aria-describedby']).toContain('responsive-table-description-');

    wrapper.unmount();
  });

  it('keeps stale rows visible while loading and blocks interaction through the overlay', async () => {
    const loadingWrapper = await mount(defineComponent({
      props: {
        loading: { type: Boolean, default: true },
      },
      setup(props) {
        return () => h(ResponsiveTable, { data: [{ symbol: 'BTC-USDT' }], loading: props.loading }, {
          loading: () => h('div', { id: 'custom-loading' }, 'custom table loading'),
          default: () => h(ElTableColumn),
        });
      },
    }), {
      locale: 'en',
      props: {
        loading: true,
      },
      components: {
        ElTable,
        ElTableColumn,
      },
    });

    const loadingRegion = loadingWrapper.find((node) => node.props.role === 'status');
    const viewport = loadingWrapper.find((node) => String(node.props.class).includes('responsive-table__viewport'));
    expect(textContent(loadingRegion)).toBe('custom table loading');
    expect(textContent(viewport)).toContain('BTC-USDT');
    expect(loadingRegion.props.class).toContain('responsive-table__loading-overlay');
    expect(loadingRegion.props.style).toEqual({ pointerEvents: 'auto', zIndex: '1' });
    expect(viewport.props.class).toContain('responsive-table__viewport--loading');
    expect(viewport.props.style).toEqual({ pointerEvents: 'none' });
    expect(viewport.props.inert).toBeTruthy();
    expect(viewport.props['aria-hidden']).toBe(true);

    await loadingWrapper.updateProps({ loading: false });
    const restoredViewport = loadingWrapper.find((node) => String(node.props.class).includes('responsive-table__viewport'));
    expect(restoredViewport.props.class).not.toContain('responsive-table__viewport--loading');
    expect(restoredViewport.props.style).toBeUndefined();
    expect(restoredViewport.props.inert).toBeUndefined();
    expect(restoredViewport.props['aria-hidden']).toBeUndefined();

    loadingWrapper.unmount();
  });

  it('renders the default empty state without relying on loading', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(ResponsiveTable, { data: [] });
      },
    }), {
      locale: 'en',
      components: {
        ElTable,
        ElTableColumn,
      },
    });

    expect(textContent(wrapper.getById('empty-marker'))).toBe('Empty');
    expect(wrapper.findAll((node) => node.props.role === 'status')).toHaveLength(0);
    expect(wrapper.findAll((node) => String(node.props.class).includes('responsive-table__loading-overlay'))).toHaveLength(0);

    wrapper.unmount();
  });

  it('forwards table attrs and column content to the inner table', async () => {
    let tableProps: { data: unknown[]; loading: boolean } | undefined;
    let tableAttrs: Record<string, unknown> | undefined;

    const InspectingElTable = defineComponent({
      name: 'ElTable',
      props: {
        data: { type: Array, default: () => [] },
        loading: { type: Boolean, default: false },
      },
      setup(props, { attrs, slots }) {
        tableProps = {
          data: props.data as unknown[],
          loading: props.loading,
        };
        tableAttrs = { ...attrs };

        return () => h('el-table', attrs, [
          slots.default?.(),
        ]);
      },
    });

    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(ResponsiveTable, {
          data: [{ symbol: 'BTC-USDT' }],
          border: true,
        }, {
          default: () => h(ElTableColumn, { prop: 'symbol', label: 'Symbol' }),
        });
      },
    }), {
      locale: 'en',
      components: {
        ElTable: InspectingElTable,
        ElTableColumn,
      },
    });

    expect(tableProps).toMatchObject({
      data: [{ symbol: 'BTC-USDT' }],
      loading: false,
    });
    expect(tableAttrs).toMatchObject({ border: true });
    expect(wrapper.find((node) => node.type === 'el-table').props.class).toContain('responsive-table__table');
    expect(wrapper.find((node) => node.type === 'el-table-column').props.prop).toBe('symbol');
    expect(wrapper.find((node) => node.type === 'el-table-column').props.label).toBe('Symbol');

    wrapper.unmount();
  });

  it('forwards header, append, and scoped summary slots to the table', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(ResponsiveTable, { data: [{ symbol: 'BTC-USDT' }] }, {
          header: ({ columns }: { columns: string[] }) => h('div', { id: 'slot-header' }, columns.join(',')),
          append: () => h('div', { id: 'slot-append' }, 'append row'),
          summary: ({ data }: { data: unknown[] }) => h('div', { id: 'slot-summary' }, `summary:${data.length}`),
          default: () => h(ElTableColumn),
        });
      },
    }), {
      locale: 'en',
      components: {
        ElTable,
        ElTableColumn,
      },
    });

    expect(textContent(wrapper.getById('slot-header'))).toBe('symbol');
    expect(textContent(wrapper.getById('slot-append'))).toBe('append row');
    expect(textContent(wrapper.getById('slot-summary'))).toBe('summary:1');

    wrapper.unmount();
  });

  it('updates forwarded named slots when they appear and disappear between renders', async () => {
    const wrapper = await mount(defineComponent({
      props: {
        showSlots: { type: Boolean, default: false },
      },
      setup(props) {
        return () => h(ResponsiveTable, { data: [{ symbol: 'BTC-USDT' }] }, {
          header: props.showSlots ? ({ columns }: { columns: string[] }) => h('div', { id: 'slot-header' }, columns.join(',')) : undefined,
          append: props.showSlots ? () => h('div', { id: 'slot-append' }, 'append row') : undefined,
          summary: props.showSlots ? ({ data }: { data: unknown[] }) => h('div', { id: 'slot-summary' }, `summary:${data.length}`) : undefined,
          default: () => h(ElTableColumn),
        });
      },
    }), {
      locale: 'en',
      components: {
        ElTable,
        ElTableColumn,
      },
      props: {
        showSlots: false,
      },
    });

    expect(wrapper.findAll((node) => node.props.id === 'slot-header')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props.id === 'slot-append')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props.id === 'slot-summary')).toHaveLength(0);

    await wrapper.updateProps({ showSlots: true });

    expect(textContent(wrapper.getById('slot-header'))).toBe('symbol');
    expect(textContent(wrapper.getById('slot-append'))).toBe('append row');
    expect(textContent(wrapper.getById('slot-summary'))).toBe('summary:1');

    await wrapper.updateProps({ showSlots: false });

    expect(wrapper.findAll((node) => node.props.id === 'slot-header')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props.id === 'slot-append')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props.id === 'slot-summary')).toHaveLength(0);

    wrapper.unmount();
  });
});
