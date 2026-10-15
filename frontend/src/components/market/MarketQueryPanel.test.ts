import { defineComponent, h, type Ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount } from '@/test-utils/mount';

import MarketQueryPanel from './MarketQueryPanel.vue';

const localeState = vi.hoisted(() => ({
  locale: null as Ref<string> | null,
}));

const stubs = {
  ElForm: defineHostComponent('el-form'),
  ElFormItem: defineHostComponent('el-form-item'),
  ElSelect: defineHostComponent('el-select'),
  ElOption: defineHostComponent('el-option'),
  ElDatePicker: defineHostComponent('el-date-picker'),
  ElButton: defineHostComponent('el-button'),
};

const baseProps = {
  marketType: 'spot',
  symbol: 'BTC-USDT',
  timeframe: '1h',
  limit: 100,
  startTime: null,
  endTime: null,
  marketTypeOptions: ['spot', 'swap', 'future', 'option'],
  symbolOptions: ['BTC-USDT', 'ETH-USDT'],
  timeframeOptions: ['1m', '1h'],
  limitOptions: [50, 100],
  loading: false,
  tickersLoading: false,
};

const MarketQueryPanelWithLocale = defineComponent({
  name: 'MarketQueryPanelWithLocale',
  props: {
    chartQuery: {
      type: Object,
      default: null,
    },
  },
  setup(props) {
    const { locale } = useI18n({ useScope: 'global' });
    localeState.locale = locale;

    return () => h(MarketQueryPanel as any, {
      ...baseProps,
      chartQuery: props.chartQuery ?? undefined,
    } as any);
  },
});

function formatRangeSummary(locale: 'en' | 'zh-CN', startTime: number, endTime: number): string {
  const start = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(startTime));
  const end = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(endTime));
  return locale === 'zh-CN' ? `从 ${start} 到 ${end}` : `From ${start} to ${end}`;
}

describe('MarketQueryPanel', () => {
  it('emits submit with the current values', async () => {
    const submit = vi.fn();
    const wrapper = await mount(MarketQueryPanel, {
      locale: 'en',
      components: stubs,
      props: {
        ...baseProps,
        symbol: 'DOGE-USDT',
        startTime: new Date('2026-08-01T00:00:00.000Z'),
        endTime: new Date('2026-08-02T00:00:00.000Z'),
        onSubmit: submit,
      },
    });

    await wrapper.trigger(wrapper.getByTestId('market-query-form'), 'submit');

    expect(submit).toHaveBeenCalledWith({
      marketType: 'spot',
      symbol: 'DOGE-USDT',
      timeframe: '1h',
      limit: 100,
      startTime: new Date('2026-08-01T00:00:00.000Z'),
      endTime: new Date('2026-08-02T00:00:00.000Z'),
    });
    wrapper.unmount();
  });

  it('uses responsive query grid and full-width mobile submit classes', async () => {
    const wrapper = await mount(MarketQueryPanel, {
      locale: 'en',
      components: stubs,
      props: baseProps,
    });

    expect(String(wrapper.getByTestId('market-query-grid').props.class)).toContain('market-query-panel__grid');
    expect(String(wrapper.getByTestId('market-query-submit').props.class)).toContain('market-query-panel__submit');
    wrapper.unmount();
  });

  it('does not show an active query summary until a successful chart query exists', async () => {
    const wrapper = await mount(MarketQueryPanel, {
      locale: 'en',
      components: stubs,
      props: baseProps,
    });

    await wrapper.trigger(wrapper.getByTestId('market-query-form'), 'submit');

    expect(wrapper.text()).not.toContain('Active query');
    expect(wrapper.text()).not.toContain('BTC-USDT');

    await wrapper.updateProps({
      chartQuery: {
        marketType: 'spot',
        symbol: 'BTC-USDT',
        timeframe: '1h',
        startTime: null,
        endTime: null,
      },
    });

    expect(wrapper.text()).toContain('Active query');
    expect(wrapper.text()).toContain('Spot');
    expect(wrapper.text()).toContain('BTC-USDT');
    expect(wrapper.text()).toContain('1h');
    expect(wrapper.text()).toContain('Latest candles');
    wrapper.unmount();
  });

  it('keeps the summary synced to the last successful chart query', async () => {
    const wrapper = await mount(MarketQueryPanel, {
      locale: 'en',
      components: stubs,
      props: {
        ...baseProps,
        chartQuery: {
          marketType: 'spot',
          symbol: 'BTC-USDT',
          timeframe: '1h',
          startTime: 1700000000000,
          endTime: 1700003600000,
        },
      },
    });

    expect(wrapper.text()).toContain('BTC-USDT');
    expect(wrapper.text()).toContain(formatRangeSummary('en', 1700000000000, 1700003600000));

    await wrapper.updateProps({
      chartQuery: {
        marketType: 'swap',
        symbol: 'ETH-USDT-SWAP',
        timeframe: '4h',
        startTime: 1700000000000,
        endTime: 1700003600000,
      },
    });

    expect(wrapper.text()).toContain('ETH-USDT-SWAP');
    expect(wrapper.text()).toContain('4h');
    expect(wrapper.text()).toContain('Swap');
    expect(wrapper.text()).toContain(formatRangeSummary('en', 1700000000000, 1700003600000));
    wrapper.unmount();
  });

  it('reformats the active query range when locale changes', async () => {
    const wrapper = await mount(MarketQueryPanelWithLocale, {
      locale: 'en',
      components: stubs,
      props: {
        chartQuery: {
          marketType: 'spot',
          symbol: 'BTC-USDT',
          timeframe: '1h',
          startTime: 1700000000000,
          endTime: 1700003600000,
        },
      },
    });

    expect(wrapper.text()).toContain(formatRangeSummary('en', 1700000000000, 1700003600000));

    if (!localeState.locale) {
      throw new Error('Locale ref was not initialized');
    }
    localeState.locale.value = 'zh-CN';
    await wrapper.flush();

    expect(wrapper.text()).toContain(formatRangeSummary('zh-CN', 1700000000000, 1700003600000));
    wrapper.unmount();
  });
});
