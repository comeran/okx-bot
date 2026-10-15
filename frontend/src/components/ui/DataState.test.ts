import { describe, expect, it, vi } from 'vitest';

import { defineComponent, h } from 'vue';

import { mount, textContent } from '../../test-utils/mount';
import DataState from './DataState.vue';

describe('DataState', () => {
  it('renders an accessible retry button for errors', async () => {
    const retry = vi.fn();
    const wrapper = await mount(DataState, {
      locale: 'en',
      props: {
        error: 'Unable to refresh',
        onRetry: retry,
      },
    });

    const button = wrapper.find((node) => node.type === 'button');
    expect(button.props['aria-label']).toBe('Retry');
    expect(textContent(button)).toBe('Retry');

    await wrapper.invoke(button, 'onClick');
    expect(retry).toHaveBeenCalledTimes(1);

    wrapper.unmount();
  });

  it('shows a stale warning without hiding default content', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(DataState, {
          stale: true,
        }, {
          default: () => h('div', { id: 'cached-content' }, 'Cached content'),
        });
      },
    }), { locale: 'en' });

    expect(wrapper.text()).toContain('Stale data');
    expect(wrapper.text()).toContain('Cached content');
    expect(wrapper.text()).toContain('Retry');
    expect(wrapper.text()).not.toContain('Error');

    wrapper.unmount();
  });

  it('preserves default content while showing a stale warning', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(DataState, {
          error: 'Refresh failed',
          stale: true,
        }, {
          default: () => h('div', { id: 'cached-content' }, 'Cached content'),
        });
      },
    }), { locale: 'en' });

    expect(wrapper.text()).toContain('Stale data');
    expect(wrapper.text()).toContain('Refresh failed');
    expect(wrapper.text()).toContain('Cached content');
    expect(wrapper.text()).toContain('Retry');
    expect(wrapper.findAll((node) => String(node.props.class).includes('data-state__panel--error'))).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props.role === 'alert')).toHaveLength(0);

    wrapper.unmount();
  });

  it('shows stale empty state content alongside the stale warning', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(DataState, {
          stale: true,
          empty: true,
          emptyDescription: 'Nothing to show',
        }, {
          default: () => h('div', { id: 'default-content' }, 'default content'),
        });
      },
    }), { locale: 'en' });

    expect(wrapper.text()).toContain('Stale data');
    expect(wrapper.text()).toContain('Empty');
    expect(wrapper.text()).toContain('Nothing to show');
    expect(wrapper.text()).not.toContain('default content');

    wrapper.unmount();
  });

  it('selects loading and empty slots predictably', async () => {
    const loadingWrapper = await mount(defineComponent({
      setup() {
        return () => h(DataState, { loading: true }, {
          loading: () => h('div', { id: 'custom-loading' }, 'custom loading'),
          default: () => h('div', { id: 'default-loading' }, 'default content'),
        });
      },
    }), { locale: 'en' });

    expect(loadingWrapper.text()).toContain('custom loading');
    expect(loadingWrapper.text()).not.toContain('default content');
    expect(loadingWrapper.text()).not.toContain('Loading');

    const emptyWrapper = await mount(defineComponent({
      setup() {
        return () => h(DataState, { empty: true, emptyDescription: 'Nothing to show' }, {
          empty: () => h('div', { id: 'custom-empty' }, 'custom empty'),
          default: () => h('div', { id: 'default-empty' }, 'default content'),
        });
      },
    }), { locale: 'en' });

    expect(emptyWrapper.text()).toContain('custom empty');
    expect(emptyWrapper.text()).not.toContain('default content');
    expect(emptyWrapper.text()).not.toContain('Empty');

    loadingWrapper.unmount();
    emptyWrapper.unmount();
  });

  it('passes error and retry into the error slot', async () => {
    const retry = vi.fn();
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(DataState, {
          error: 'Unable to refresh',
          onRetry: retry,
        }, {
          error: (slotProps: { error: string | null; retry: () => void }) => h('div', { id: 'custom-error' }, [
            h('p', { id: 'slot-error' }, slotProps.error ?? ''),
            h('button', { id: 'slot-retry', type: 'button', onClick: slotProps.retry }, 'Try again'),
          ]),
        });
      },
    }), { locale: 'en' });

    expect(textContent(wrapper.getById('slot-error'))).toBe('Unable to refresh');
    expect(textContent(wrapper.getById('slot-retry'))).toBe('Try again');

    await wrapper.invoke(wrapper.getById('slot-retry'), 'onClick');
    expect(retry).toHaveBeenCalledTimes(1);

    wrapper.unmount();
  });
});
