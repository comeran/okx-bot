import { Teleport, defineComponent, getCurrentInstance, h, onMounted, type Component } from 'vue';
import { describe, expect, it, vi } from 'vitest';
import { useI18n } from 'vue-i18n';
import { createMemoryHistory, createRouter, RouterView } from 'vue-router';

import { defineHostComponent, mount, textContent, type TestHostNode } from './mount';

function teleportedProbe(to: string, onMountedNode: (node: TestHostNode) => void): Component {
  const Probe = defineComponent({
    setup() {
      const instance = getCurrentInstance();
      onMounted(() => {
        const node = instance?.vnode.el as TestHostNode | undefined;
        if (node) onMountedNode(node);
      });
      return () => h('span', { id: `probe-${to}` }, 'teleported');
    },
  });

  return defineComponent({
    setup() {
      return () => h(Teleport, { to }, h(Probe));
    },
  });
}

describe('mount host node DOM surface', () => {
  it('keeps props.class and classList synchronized when transition classes are added and removed', async () => {
    const wrapper = await mount(defineComponent({
      setup: () => () => h('div', { class: 'static-class' }, 'content'),
    }));
    const target = wrapper.find((node) => node.type === 'div');

    expect(target.props.class).toBe('static-class');
    expect(target.classList.contains('static-class')).toBe(true);

    target.classList.add('temporary-transition');

    expect(target.classList.contains('static-class')).toBe(true);
    expect(target.classList.contains('temporary-transition')).toBe(true);
    expect(target.props.class).toBe('static-class temporary-transition');

    target.classList.remove('temporary-transition');

    expect(target.classList.contains('static-class')).toBe(true);
    expect(target.classList.contains('temporary-transition')).toBe(false);
    expect(target.props.class).toBe('static-class');

    target.setAttribute('class', 'static-class attribute-class');

    expect(target.props.class).toBe('static-class attribute-class');
    expect(target.classList.contains('static-class')).toBe(true);
    expect(target.classList.contains('attribute-class')).toBe(true);

    wrapper.unmount();
  });

  it('clones nodes without sharing identity, mutation state, children, or parent links', async () => {
    const wrapper = await mount(defineComponent({
      setup: () => () => h('section', { id: 'source', class: 'source-class', style: { color: 'red' } }, [
        h('span', { id: 'child' }, 'child'),
      ]),
    }));
    const source = wrapper.getById('source');
    const child = wrapper.getById('child');
    const clone = (source as TestHostNode & { cloneNode: (deep?: boolean) => TestHostNode }).cloneNode(true);

    expect(clone).not.toBe(source);
    expect(clone.isSameNode(source)).toBe(false);
    expect(source.isSameNode(clone)).toBe(false);
    expect(clone.parent).toBeNull();
    expect(clone.parentNode).toBeNull();
    expect(clone.props).toEqual(source.props);
    expect(clone.props).not.toBe(source.props);
    expect(clone.props.style).toEqual(source.props.style);
    expect(clone.props.style).not.toBe(source.props.style);
    expect(clone.style).toEqual(source.style);
    expect(clone.style).not.toBe(source.style);
    expect(clone.classList.contains('source-class')).toBe(true);
    expect(clone.children).toHaveLength(1);
    expect(clone.children[0]).not.toBe(child);
    expect(clone.children[0].parent).toBe(clone);
    expect(clone.children[0].parentNode).toBe(clone);

    clone.setAttribute('id', 'clone');
    clone.classList.add('clone-class');
    clone.focus();
    (clone.props.style as { color: string }).color = 'blue';
    clone.children[0].setAttribute('id', 'clone-child');

    expect(source.props.id).toBe('source');
    expect((source.props.style as { color: string }).color).toBe('red');
    expect(source.classList.contains('clone-class')).toBe(false);
    expect(source.props['data-focused']).toBeUndefined();
    expect(child.props.id).toBe('child');

    const parent = source.parent;
    parent?.children.push(clone);
    clone.parent = parent;

    expect(clone.parentNode).toBe(parent);
    expect(source.parentNode).toBe(parent);
    expect(source.parent?.children.filter((node) => node === source)).toHaveLength(1);
    expect(source.parent?.children.filter((node) => node === clone)).toHaveLength(1);

    wrapper.unmount();
  });

  it('keeps the mount-local defaultView proxy callable without losing its document', async () => {
    const wrapper = await mount(defineComponent({
      setup: () => () => h('div', { id: 'default-view-probe' }, 'content'),
    }));
    const ownerDocument = wrapper.root.ownerDocument;
    const defaultView = ownerDocument.defaultView;

    expect(defaultView.document).toBe(ownerDocument);
    expect(defaultView.document.body).toBe(ownerDocument.body);
    expect(defaultView.document.defaultView).toBe(defaultView);
    expect(typeof defaultView.getComputedStyle).toBe('function');
    expect(ownerDocument.body.ownerDocument).toBe(ownerDocument);

    wrapper.unmount();
  });

  it('keeps named and default slots separate while preserving scoped props', async () => {
    const RelayHost = defineHostComponent('el-relay', {
      slotRenderer(slots) {
        return [
          h('div', { id: 'default-container' }, slots.default?.({ scoped: 'default-scoped' }) ?? []),
          h('div', { id: 'header-container' }, slots.header?.({ label: 'header-label' }) ?? []),
        ];
      },
    });

    const namedSlot = vi.fn(({ label }: { label: string }) => h('span', { id: 'named-slot' }, label));
    const defaultSlot = vi.fn(({ scoped }: { scoped: string }) => h('span', { id: 'default-slot' }, scoped));

    const wrapper = await mount(defineComponent({
      setup() {
        return () => h(RelayHost, null, {
          header: namedSlot,
          default: defaultSlot,
        });
      },
    }));

    expect(namedSlot).toHaveBeenCalledTimes(1);
    expect(defaultSlot).toHaveBeenCalledTimes(1);
    expect(namedSlot.mock.calls[0]?.[0]).toEqual({ label: 'header-label' });
    expect(defaultSlot.mock.calls[0]?.[0]).toEqual({ scoped: 'default-scoped' });
    expect(textContent(wrapper.getById('default-container'))).toBe('default-scoped');
    expect(textContent(wrapper.getById('header-container'))).toBe('header-label');
    expect(wrapper.findAll((node) => node.props.id === 'named-slot')).toHaveLength(1);
    expect(wrapper.findAll((node) => node.props.id === 'default-slot')).toHaveLength(1);

    wrapper.unmount();
  });

  it('respects host component attr inheritance when forwarding attrs to the host node', async () => {
    const ForwardingHost = defineHostComponent('el-forwarding-host');
    const NonForwardingHost = defineHostComponent('el-non-forwarding-host', { inheritAttrs: false });

    const forwardingWrapper = await mount(defineComponent({
      setup() {
        return () => h(ForwardingHost, {
          class: 'forwarded-class',
          style: { color: 'red' },
          'data-testid': 'forwarded-host',
          'data-custom': 'kept',
        }, {
          default: () => h('span', { id: 'forwarded-content' }, 'forwarded content'),
        });
      },
    }));

    const forwardingHost = forwardingWrapper.find((node) => node.type === 'el-forwarding-host');
    expect(forwardingHost.props.class).toBe('forwarded-class');
    expect(forwardingHost.props.style).toEqual({ color: 'red' });
    expect(forwardingHost.props['data-testid']).toBe('forwarded-host');
    expect(forwardingHost.props['data-custom']).toBe('kept');
    expect(textContent(forwardingWrapper.getById('forwarded-content'))).toBe('forwarded content');

    forwardingWrapper.unmount();

    const nonForwardingWrapper = await mount(defineComponent({
      setup() {
        return () => h(NonForwardingHost, {
          class: 'hidden-class',
          style: { color: 'blue' },
          'data-testid': 'hidden-host',
          'data-custom': 'hidden',
        }, {
          default: () => h('span', { id: 'non-forwarded-content' }, 'non-forwarded content'),
        });
      },
    }));

    const nonForwardingHost = nonForwardingWrapper.find((node) => node.type === 'el-non-forwarding-host');
    expect(nonForwardingHost.props.class).toBeUndefined();
    expect(nonForwardingHost.props.style).toBeUndefined();
    expect(nonForwardingHost.props['data-testid']).toBeUndefined();
    expect(nonForwardingHost.props['data-custom']).toBeUndefined();
    expect(textContent(nonForwardingWrapper.getById('non-forwarded-content'))).toBe('non-forwarded content');

    nonForwardingWrapper.unmount();
  });
});

describe('mount teleport support', () => {
  it('uses mount-local body targets and removes teleported nodes on unmount', async () => {
    let firstNode: TestHostNode | undefined;
    const first = await mount(teleportedProbe('body', (node) => { firstNode = node; }));
    await first.flush();
    const firstTarget = firstNode?.parent;

    expect(firstTarget?.type).toBe('body');
    expect(firstTarget?.children).toContain(firstNode);

    first.unmount();

    expect(firstNode?.parent).toBeNull();
    expect(firstTarget?.children).not.toContain(firstNode);

    let secondNode: TestHostNode | undefined;
    const second = await mount(teleportedProbe('body', (node) => { secondNode = node; }));
    await second.flush();
    const secondTarget = secondNode?.parent;

    expect(secondTarget?.type).toBe('body');
    expect(secondTarget).not.toBe(firstTarget);
    expect(secondTarget?.children).toContain(secondNode);
    expect(secondTarget?.children).not.toContain(firstNode);

    second.unmount();
  });

  it('uses mount-local Element Plus popper targets and removes teleported nodes on unmount', async () => {
    let firstNode: TestHostNode | undefined;
    const first = await mount(teleportedProbe('#el-popper-container-1024', (node) => { firstNode = node; }));
    await first.flush();
    const firstTarget = first.queryTeleportTarget('#el-popper-container-1024');

    expect(firstTarget?.type).toBe('body');
    expect(firstNode?.parent).toBe(firstTarget);
    expect(firstTarget?.children).toContain(firstNode);

    first.unmount();

    expect(firstNode?.parent).toBeNull();
    expect(firstTarget?.children).not.toContain(firstNode);

    let secondNode: TestHostNode | undefined;
    const second = await mount(teleportedProbe('#el-popper-container-1024', (node) => { secondNode = node; }));
    await second.flush();
    const secondTarget = second.queryTeleportTarget('#el-popper-container-1024');

    expect(secondTarget?.type).toBe('body');
    expect(secondTarget).not.toBe(firstTarget);
    expect(secondNode?.parent).toBe(secondTarget);
    expect(secondTarget?.children).toContain(secondNode);
    expect(secondTarget?.children).not.toContain(firstNode);

    second.unmount();
  });

  it('does not create teleport targets for unsupported selectors', async () => {
    const wrapper = await mount(defineComponent({ setup: () => () => h('main') }));

    expect(wrapper.queryTeleportTarget('#unsupported')).toBeNull();

    wrapper.unmount();
  });
});

describe('mount locale support', () => {
  function localizedProbe(): Component {
    return defineComponent({
      setup() {
        const { locale, t } = useI18n({ useScope: 'global' });

        return () => h('p', { id: 'localized-probe' }, `${locale.value}:${t('app.language')}`);
      },
    });
  }

  it.each([
    ['en', 'en:Language'],
    ['zh-CN', 'zh-CN:语言'],
  ] as const)('initializes %s locale with the matching messages', async (locale, expected) => {
    const wrapper = await mount(localizedProbe(), { locale });

    expect(wrapper.text()).toBe(expected);

    wrapper.unmount();
  });
});

describe('mount router support', () => {
  it('installs the router plugin and renders route changes', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/',
          component: defineComponent({
            setup: () => () => h('p', { id: 'route-content' }, 'home route'),
          }),
        },
        {
          path: '/strategies',
          component: defineComponent({
            setup: () => () => h('p', { id: 'route-content' }, 'strategies route'),
          }),
        },
      ],
    });

    await router.push('/');

    const wrapper = await mount(defineComponent({ setup: () => () => h(RouterView) }), { router });

    expect(wrapper.text()).toBe('home route');

    await router.push('/strategies');
    await wrapper.flush();

    expect(wrapper.text()).toBe('strategies route');

    wrapper.unmount();
  });
});
