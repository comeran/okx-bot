import { reactive } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount, textContent } from '@/test-utils/mount';
import AppSidebar from './AppSidebar.vue';

const route = reactive({ path: '/' });

vi.mock('vue-router', () => ({
  useRoute: () => route,
}));

const components = {
  ElMenu: defineHostComponent('el-menu'),
  ElMenuItem: defineHostComponent('el-menu-item'),
};

const expectedItems = [
  { route: '/', label: 'Dashboard' },
  { route: '/strategies', label: 'Strategies' },
  { route: '/backtest', label: 'Backtest' },
  { route: '/market', label: 'Market' },
  { route: '/trades', label: 'Trades' },
  { route: '/settings', label: 'Settings' },
];

describe('AppSidebar', () => {
  beforeEach(() => {
    route.path = '/';
  });

  it('renders each shell route exactly once', async () => {
    const wrapper = await mount(AppSidebar, { components, locale: 'en' });
    const nav = wrapper.find((node) => node.type === 'nav');
    const menuItems = wrapper.findAll((node) => node.type === 'el-menu-item');

    expect(nav.props['aria-label']).toBe('Primary navigation');
    expect(menuItems).toHaveLength(expectedItems.length);
    expect(menuItems.map((item) => item.props.index)).toEqual(expectedItems.map((item) => item.route));
    expect(menuItems.map(textContent)).toEqual(expectedItems.map((item) => item.label));

    wrapper.unmount();
  });

  it('renders a localized navigation landmark label in Simplified Chinese', async () => {
    route.path = '/market';
    const wrapper = await mount(AppSidebar, { components, locale: 'zh-CN' });
    const nav = wrapper.find((node) => node.type === 'nav');
    const activeItem = wrapper.find((node) => node.type === 'el-menu-item' && node.props.index === '/market');
    const menuItems = wrapper.findAll((node) => node.type === 'el-menu-item');

    expect(nav.props['aria-label']).toBe('主要导航');
    expect(menuItems.map(textContent)).toEqual(['仪表盘', '策略', '回测', '行情', '交易', '设置']);
    expect(activeItem.props.class).toContain('is-active');
    expect(activeItem.props['aria-current']).toBe('page');

    wrapper.unmount();
  });

  it('marks the current route active and exposes aria-current page', async () => {
    route.path = '/market';
    const wrapper = await mount(AppSidebar, { components, locale: 'en' });
    const activeItem = wrapper.find((node) => node.type === 'el-menu-item' && node.props.index === '/market');
    const inactiveItem = wrapper.find((node) => node.type === 'el-menu-item' && node.props.index === '/trades');

    expect(activeItem.props.class).toContain('is-active');
    expect(activeItem.props['aria-current']).toBe('page');
    expect(inactiveItem.props.class ?? '').not.toContain('is-active');
    expect(inactiveItem.props['aria-current']).toBeUndefined();

    wrapper.unmount();
  });
});
