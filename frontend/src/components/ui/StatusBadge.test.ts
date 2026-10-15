import { describe, expect, it } from 'vitest';

import { mount, textContent } from '../../test-utils/mount';
import StatusBadge from './StatusBadge.vue';

describe('StatusBadge', () => {
  it('renders status text with a non-color indicator', async () => {
    const wrapper = await mount(StatusBadge, {
      props: {
        status: 'Running',
        tone: 'success',
      },
    });

    const badge = wrapper.find((node) => node.type === 'span' && String(node.props.class).includes('status-badge'));
    expect(textContent(badge)).toContain('Running');
    expect(wrapper.find((node) => node.props.class === 'status-badge__indicator status-badge__indicator--dot')).toBeTruthy();
    expect(badge.props['data-tone']).toBe('success');

    wrapper.unmount();
  });

  it('renders every tone with a visible label and dot indicator', async () => {
    const cases = [
      ['neutral', 'Idle'],
      ['primary', 'Syncing'],
      ['success', 'Healthy'],
      ['warning', 'Degraded'],
      ['danger', 'Failed'],
      ['info', 'Informational'],
    ] as const;

    for (const [tone, status] of cases) {
      const wrapper = await mount(StatusBadge, {
        props: {
          status,
          tone,
        },
      });

      const badge = wrapper.find((node) => node.type === 'span' && String(node.props.class).includes('status-badge'));
      expect(badge.props['data-tone']).toBe(tone);
      expect(textContent(badge)).toContain(status);
      expect(wrapper.find((node) => node.props.class === 'status-badge__indicator status-badge__indicator--dot')).toBeTruthy();

      wrapper.unmount();
    }
  });
});
