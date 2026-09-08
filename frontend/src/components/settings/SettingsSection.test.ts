import { describe, expect, it } from 'vitest';
import { defineComponent, h, reactive } from 'vue';

import { mount, textContent } from '@/test-utils/mount';
import SettingsSection from './SettingsSection.vue';

describe('SettingsSection', () => {
  it('renders title and description props and falls back to the default body slot', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SettingsSection, {
          title: 'Exchange',
          description: 'Manage connection settings.',
        }, {
          default: () => h('div', { id: 'section-body' }, 'Section content'),
        });
      },
    }));

    expect(textContent(wrapper.find((node) => node.type === 'h3' && node.props.class === 'section-card__title'))).toBe('Exchange');
    expect(textContent(wrapper.find((node) => node.type === 'p' && node.props.class === 'section-card__description'))).toBe('Manage connection settings.');
    expect(textContent(wrapper.getById('section-body'))).toBe('Section content');
  });

  it('prefers body over content over default for section content', async () => {
    const withBody = await mount(defineComponent({
      setup() {
        return () => h(SettingsSection, {
          title: 'Runtime',
          description: 'Choose how the bot should run.',
        }, {
          body: () => h('div', { id: 'body-slot' }, 'Body content'),
          content: () => h('div', { id: 'content-slot' }, 'Content content'),
          default: () => h('div', { id: 'default-slot' }, 'Default content'),
        });
      },
    }));

    expect(textContent(withBody.getById('body-slot'))).toBe('Body content');
    expect(withBody.findAll((node) => node.props?.id === 'content-slot')).toHaveLength(0);
    expect(withBody.findAll((node) => node.props?.id === 'default-slot')).toHaveLength(0);

    const withContent = await mount(defineComponent({
      setup() {
        return () => h(SettingsSection, {
          title: 'Runtime',
          description: 'Choose how the bot should run.',
        }, {
          content: () => h('div', { id: 'content-slot' }, 'Content content'),
          default: () => h('div', { id: 'default-slot' }, 'Default content'),
        });
      },
    }));

    expect(textContent(withContent.getById('content-slot'))).toBe('Content content');
    expect(withContent.findAll((node) => node.props?.id === 'default-slot')).toHaveLength(0);
  });

  it('renders status and actions in the header action area', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SettingsSection, {
          title: 'Exchange',
          description: 'Manage connection settings.',
        }, {
          status: () => h('span', { id: 'section-status' }, 'Configured'),
          actions: () => h('button', { id: 'section-action', type: 'button' }, 'Reload'),
          default: () => h('div', { id: 'section-body' }, 'Section content'),
        });
      },
    }));

    expect(wrapper.findAll((node) => node.props.class === 'section-card__actions')).toHaveLength(1);
    expect(wrapper.findAll((node) => node.props.class === 'settings-section__meta')).toHaveLength(1);
    expect(textContent(wrapper.getById('section-status'))).toBe('Configured');
    expect(textContent(wrapper.getById('section-action'))).toBe('Reload');
    expect(textContent(wrapper.getById('section-body'))).toBe('Section content');
  });

  it('reacts to dynamic status and actions slots without leaving an empty meta shell', async () => {
    const state = reactive({ showStatus: false, showActions: false });
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SettingsSection, {
          title: 'Exchange',
          description: 'Manage connection settings.',
        }, {
          status: () => (state.showStatus ? h('span', { id: 'section-status' }, 'Configured') : undefined),
          actions: () => (state.showActions ? h('button', { id: 'section-action', type: 'button' }, 'Reload') : undefined),
          default: () => h('div', { id: 'section-body' }, 'Section content'),
        });
      },
    }));

    expect(wrapper.findAll((node) => node.props.class === 'settings-section__meta')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props.class === 'section-card__actions')).toHaveLength(0);

    state.showStatus = true;
    await wrapper.flush();

    expect(wrapper.findAll((node) => node.props.class === 'settings-section__meta')).toHaveLength(1);
    expect(textContent(wrapper.getById('section-status'))).toBe('Configured');
    expect(wrapper.findAll((node) => node.props.class === 'section-card__actions')).toHaveLength(1);

    state.showActions = true;
    await wrapper.flush();

    expect(textContent(wrapper.getById('section-action'))).toBe('Reload');

    state.showStatus = false;
    state.showActions = false;
    await wrapper.flush();

    expect(wrapper.findAll((node) => node.props.class === 'settings-section__meta')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props.class === 'section-card__actions')).toHaveLength(0);
    expect(textContent(wrapper.getById('section-body'))).toBe('Section content');
  });
});
