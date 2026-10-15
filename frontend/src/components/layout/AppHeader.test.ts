import { defineComponent, h, ref } from 'vue';
import { describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';
import type { Locale } from '@/i18n';
import AppHeader from './AppHeader.vue';

const components = {
  ElSelect: defineHostComponent('el-select'),
  ElOption: defineHostComponent('el-option'),
};

const availableLocales = ['en', 'zh-CN'] as const satisfies readonly Locale[];

describe('AppHeader', () => {
  it('renders a localized mobile menu aria-label and status slot', async () => {
    const wrapper = await mount(AppHeader, {
      props: {
        pageTitle: '仪表盘',
        locale: 'zh-CN',
        availableLocales,
      },
      components,
      locale: 'zh-CN',
    });

    const menuButton = wrapper.find((node) => node.type === 'button' && node.props.class === 'app-header__menu-button');

    expect(menuButton.props['aria-label']).toBe('打开导航菜单');
    expect(wrapper.find((node) => node.type === 'el-select').props['aria-label']).toBe('语言');
    expect(wrapper.text()).toContain('量化交易控制台');
    expect(wrapper.text()).toContain('仪表盘');

    wrapper.unmount();
  });

  it('lets the parent restore focus to the menu button by component ref', async () => {
    const Parent = defineComponent({
      setup() {
        const headerRef = ref<InstanceType<typeof AppHeader> | null>(null);
        const selectedLocale = ref<Locale>('en');
        const opened = ref(false);

        return () => h('div', [
          h(AppHeader, {
            ref: headerRef,
            pageTitle: 'Dashboard',
            locale: selectedLocale.value,
            availableLocales,
            onMenuTrigger: () => { opened.value = true; },
            'onUpdate:locale': (value: Locale) => { selectedLocale.value = value; },
          }, {
            status: () => h('span', { id: 'connection-status' }, opened.value ? 'opened' : 'closed'),
          }),
          h('button', {
            id: 'restore-focus',
            type: 'button',
            onClick: () => headerRef.value?.focusMenuButton(),
          }, 'Restore focus'),
        ]);
      },
    });

    const wrapper = await mount(Parent, { components, locale: 'en' });
    const menuButton = wrapper.find((node) => node.type === 'button' && node.props.class === 'app-header__menu-button');
    const restoreButton = wrapper.getById('restore-focus');

    expect(textContent(wrapper.getById('connection-status'))).toBe('closed');
    await wrapper.trigger(menuButton, 'click');
    expect(textContent(wrapper.getById('connection-status'))).toBe('opened');
    await wrapper.trigger(restoreButton, 'click');
    expect(menuButton.props['data-focused']).toBe(true);

    wrapper.unmount();
  });
});
