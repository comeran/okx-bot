import { describe, expect, it } from 'vitest';

import { mount } from '@/test-utils/mount';

const TradeSummary = (await import('./TradeSummary.vue')).default;

describe('TradeSummary', () => {
  it('renders filtered trade aggregates compactly', async () => {
    const wrapper = await mount(TradeSummary, {
      locale: 'en',
      props: {
        summary: {
          totalTrades: 4,
          totalNotional: 12500.5,
          totalFees: 3.75,
          positivePnlCount: 2,
          negativePnlCount: 1,
        },
      },
    });

    expect(wrapper.text()).toContain('Results');
    expect(wrapper.text()).toContain('12,500.5');
    expect(wrapper.text()).toContain('3.75');
    expect(wrapper.text()).toContain('2');
    expect(wrapper.text()).toContain('1');

    wrapper.unmount();
  });

  it('shows missing financial aggregates with an em dash', async () => {
    const wrapper = await mount(TradeSummary, {
      locale: 'en',
      props: {
        summary: {
          totalTrades: 0,
          totalNotional: null,
          totalFees: null,
          positivePnlCount: null,
          negativePnlCount: null,
        },
      },
    });

    expect(wrapper.text()).toContain('—');
    expect(wrapper.text()).not.toContain('0.00');

    wrapper.unmount();
  });
});
