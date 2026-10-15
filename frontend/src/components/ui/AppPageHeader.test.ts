import { describe, expect, it } from 'vitest';
import { defineComponent, h } from 'vue';

import { mount, textContent } from '../../test-utils/mount';
import AppPageHeader from './AppPageHeader.vue';

describe('AppPageHeader', () => {
  it('renders the page heading and action slot', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(AppPageHeader, {
          title: 'Strategy overview',
          description: 'Manage runtime strategy instances.',
        }, {
          actions: () => h('button', { type: 'button', id: 'header-action' }, 'Refresh'),
        });
      },
    }));

    expect(textContent(wrapper.find((node) => node.type === 'h2'))).toBe('Strategy overview');
    expect(textContent(wrapper.find((node) => node.props.id === 'header-action'))).toBe('Refresh');
    expect(textContent(wrapper.find((node) => node.type === 'p' && node.props.class === 'app-page-header__description'))).toBe('Manage runtime strategy instances.');

    wrapper.unmount();
  });
});
