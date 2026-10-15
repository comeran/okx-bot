import { describe, expect, it } from 'vitest';

import { mount, textContent } from '../../test-utils/mount';
import MetricCard from './MetricCard.vue';

describe('MetricCard', () => {
  it('renders the value and semantic delta', async () => {
    const wrapper = await mount(MetricCard, {
      props: {
        label: 'Return',
        value: '12.4%',
        delta: '+1.8%',
        tone: 'success',
      },
    });

    expect(textContent(wrapper.find((node) => node.props.class === 'metric-card__label'))).toBe('Return');
    expect(textContent(wrapper.find((node) => node.props.class === 'metric-card__value'))).toBe('12.4%');
    expect(textContent(wrapper.find((node) => node.props.class === 'metric-card__delta'))).toBe('+1.8%');
    expect(wrapper.find((node) => node.type === 'article').props['data-tone']).toBe('success');

    wrapper.unmount();
  });

  it('shows a stable loading placeholder with busy state', async () => {
    const wrapper = await mount(MetricCard, {
      props: {
        label: 'Return',
        value: '12.4%',
        delta: '+1.8%',
        loading: true,
      },
    });

    const article = wrapper.find((node) => node.type === 'article');
    const value = wrapper.find((node) => node.type === 'span' && String(node.props.class).split(' ').includes('metric-card__value'));

    expect(article.props['aria-busy']).toBe(true);
    expect(wrapper.text()).toContain('—');
    expect(value.props.class).toContain('metric-card__value--loading');
    expect(wrapper.text()).not.toContain('12.4%');
    expect(wrapper.text()).not.toContain('+1.8%');

    wrapper.unmount();
  });
});
