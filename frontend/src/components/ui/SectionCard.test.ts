import { describe, expect, it, vi } from 'vitest';
import { Comment, Fragment, Text, defineComponent, h } from 'vue';

import { mount, textContent } from '../../test-utils/mount';
import SectionCard from './SectionCard.vue';

describe('SectionCard', () => {
  it('renders prop fallback content and the default body slot', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SectionCard, {
          title: 'Strategy settings',
          description: 'Shared configuration for runtime strategy cards.',
        }, {
          default: () => h('div', { id: 'section-card-body' }, 'Body content'),
        });
      },
    }));

    expect(wrapper.find((node) => node.type === 'article').props.class).toBe('section-card');
    expect(textContent(wrapper.find((node) => node.type === 'h3' && node.props.class === 'section-card__title'))).toBe('Strategy settings');
    expect(textContent(wrapper.find((node) => node.type === 'p' && node.props.class === 'section-card__description'))).toBe('Shared configuration for runtime strategy cards.');
    expect(textContent(wrapper.find((node) => node.props.id === 'section-card-body'))).toBe('Body content');
    expect(wrapper.find((node) => node.props.class === 'section-card__header').children.length).toBeGreaterThan(0);
    expect(wrapper.find((node) => node.props.class === 'section-card__body').props.class).toBe('section-card__body');

    wrapper.unmount();
  });

  it('calls the title, description, and actions slot functions once per render', async () => {
    const title = vi.fn(() => h('span', { id: 'slot-title' }, 'Slot title'));
    const description = vi.fn(() => h('span', { id: 'slot-description' }, 'Slot description'));
    const actions = vi.fn(() => h('button', { id: 'slot-action', type: 'button' }, 'Refresh'));

    const wrapper = await mount(defineComponent({
      props: {
        active: { type: Boolean, default: false },
      },
      setup() {
        return () => h(SectionCard, {
          title: 'Prop title',
          description: 'Prop description',
        }, {
          title,
          description,
          actions,
          default: () => h('div', { id: 'slot-body' }, 'Slot body'),
        });
      },
    }), {
      props: {
        active: false,
      },
    });

    expect(title).toHaveBeenCalledTimes(1);
    expect(description).toHaveBeenCalledTimes(1);
    expect(actions).toHaveBeenCalledTimes(1);
    expect(textContent(wrapper.getById('slot-title'))).toBe('Slot title');
    expect(textContent(wrapper.getById('slot-description'))).toBe('Slot description');
    expect(textContent(wrapper.getById('slot-action'))).toBe('Refresh');
    expect(textContent(wrapper.getById('slot-body'))).toBe('Slot body');

    await wrapper.updateProps({ active: true });

    expect(title).toHaveBeenCalledTimes(2);
    expect(description).toHaveBeenCalledTimes(2);
    expect(actions).toHaveBeenCalledTimes(2);

    wrapper.unmount();
  });

  it('prefers title, description, actions, and default slots over props', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SectionCard, {
          title: 'Prop title',
          description: 'Prop description',
        }, {
          title: () => h('span', { id: 'slot-title' }, 'Slot title'),
          description: () => h('span', { id: 'slot-description' }, 'Slot description'),
          actions: () => h('button', { id: 'slot-action', type: 'button' }, 'Refresh'),
          default: () => h('div', { id: 'slot-body' }, 'Slot body'),
        });
      },
    }));

    expect(textContent(wrapper.find((node) => node.props.id === 'slot-title'))).toBe('Slot title');
    expect(textContent(wrapper.find((node) => node.props.id === 'slot-description'))).toBe('Slot description');
    expect(textContent(wrapper.find((node) => node.props.id === 'slot-action'))).toBe('Refresh');
    expect(textContent(wrapper.find((node) => node.props.id === 'slot-body'))).toBe('Slot body');
    expect(wrapper.find((node) => node.type === 'article').props.class).toBe('section-card');
    expect(wrapper.find((node) => node.props.class === 'section-card__header').props.class).toBe('section-card__header');
    expect(wrapper.find((node) => node.props.class === 'section-card__body').props.class).toBe('section-card__body');

    wrapper.unmount();
  });

  it('prefers the named body slot over the default body slot', async () => {
    const body = vi.fn(() => h('div', { id: 'named-body' }, 'Named body'));
    const defaultSlot = vi.fn(() => h('div', { id: 'default-body' }, 'Default body'));

    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SectionCard, null, {
          body,
          default: defaultSlot,
        });
      },
    }));

    expect(body).toHaveBeenCalledTimes(1);
    expect(defaultSlot).not.toHaveBeenCalled();
    expect(textContent(wrapper.find((node) => node.props.class === 'section-card__body'))).toBe('Named body');
    expect(textContent(wrapper.getById('named-body'))).toBe('Named body');
    expect(wrapper.findAll((node) => node.props.id === 'default-body')).toHaveLength(0);

    wrapper.unmount();
  });

  it.each([
    ['undefined', undefined],
    ['null', null],
  ])('does not fall back to the default body slot when the named body slot returns %s', async (_, bodyResult) => {
    const body = vi.fn(() => bodyResult);
    const defaultSlot = vi.fn(() => h('div', { id: 'default-body' }, 'Default body'));

    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SectionCard, null, {
          body,
          default: defaultSlot,
        });
      },
    }));

    expect(body).toHaveBeenCalledTimes(1);
    expect(defaultSlot).not.toHaveBeenCalled();
    expect(textContent(wrapper.find((node) => node.props.class === 'section-card__body'))).toBe('');
    expect(wrapper.findAll((node) => node.props.id === 'default-body')).toHaveLength(0);

    wrapper.unmount();
  });

  it('hides the header when description and actions slots are empty', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SectionCard, null, {
          description: () => [h(Fragment, [])],
          actions: () => null,
          default: () => h('div', { id: 'section-card-body' }, 'Body content'),
        });
      },
    }));

    expect(wrapper.findAll((node) => node.props.class === 'section-card__header')).toHaveLength(0);
    expect(textContent(wrapper.getById('section-card-body'))).toBe('Body content');

    wrapper.unmount();
  });

  it('shows header and actions when slots render component or element vnodes and hides them for empty placeholders', async () => {
    const ActionIcon = defineComponent({
      name: 'ActionIcon',
      setup: () => () => h('svg', { id: 'action-icon', viewBox: '0 0 16 16', role: 'img', 'aria-label': 'Action icon' }),
    });

    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SectionCard, null, {
          title: () => h('span', { id: 'slot-title' }, 'Element title'),
          description: () => h(Text, '   '),
          actions: () => h(ActionIcon),
          default: () => h('div', { id: 'section-card-body' }, 'Body content'),
        });
      },
    }));

    expect(textContent(wrapper.getById('slot-title'))).toBe('Element title');
    expect(wrapper.find((node) => node.props.class === 'section-card__header')).toBeTruthy();
    expect(wrapper.find((node) => node.props.class === 'section-card__actions')).toBeTruthy();
    expect(wrapper.find((node) => node.props.id === 'action-icon')).toBeTruthy();
    expect(textContent(wrapper.getById('section-card-body'))).toBe('Body content');

    wrapper.unmount();
  });

  it('hides the header and actions when slots only render comments, nulls, booleans, empty text, or empty fragments', async () => {
    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(SectionCard, null, {
          title: () => h(Comment),
          description: () => [null, false, h(Text, ''), h(Fragment, [])],
          actions: () => [h(Fragment, []), null, false],
          default: () => h('div', { id: 'section-card-body' }, 'Body content'),
        });
      },
    }));

    expect(wrapper.findAll((node) => node.props.class === 'section-card__header')).toHaveLength(0);
    expect(wrapper.findAll((node) => node.props.class === 'section-card__actions')).toHaveLength(0);
    expect(textContent(wrapper.getById('section-card-body'))).toBe('Body content');

    wrapper.unmount();
  });
});
