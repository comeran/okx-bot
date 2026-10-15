import { describe, expect, it } from 'vitest';

import { mount, textContent } from '@/test-utils/mount';
import type { BacktestMetrics as BacktestMetricsData } from '@/types/backtest';

const BacktestMetrics = (await import('./BacktestMetrics.vue')).default;

const metrics: BacktestMetricsData = {
  total_return: 0.1234,
  sharpe_ratio: 1.2345,
  max_drawdown: 0.0456,
  win_rate: 0.5,
  total_trades: 7,
};

describe('BacktestMetrics', () => {
  it('formats each metric card without fabricating values', async () => {
    const wrapper = await mount(BacktestMetrics, {
      locale: 'en',
      props: {
        metrics,
        loading: false,
      },
    });

    const cards = wrapper.findAll((node) => node.type === 'article' && String(node.props.class).includes('metric-card'));
    expect(cards).toHaveLength(5);
    expect(textContent(cards[0])).toContain('Total Return');
    expect(textContent(cards[0])).toContain('12.34%');
    expect(textContent(cards[1])).toContain('1.23');
    expect(textContent(cards[2])).toContain('4.56%');
    expect(textContent(cards[3])).toContain('50.00%');
    expect(textContent(cards[4])).toContain('7');
  });

  it('shows placeholders while loading and no metrics are available', async () => {
    const wrapper = await mount(BacktestMetrics, {
      locale: 'en',
      props: {
        metrics: null,
        loading: true,
      },
    });

    const cards = wrapper.findAll((node) => node.type === 'article' && String(node.props.class).includes('metric-card'));
    expect(cards).toHaveLength(5);
    expect(cards.every((card) => String(card.props['aria-busy']) === 'true' || card.props['aria-busy'] === true)).toBe(true);
    expect(wrapper.text()).not.toContain('Run a backtest to see metrics.');
  });
});
