import { onBeforeUnmount, reactive, ref } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount } from '@/test-utils/mount';
import App from './App.vue';

const mocks = vi.hoisted(() => ({
  useWebSocket: vi.fn(),
  connect: vi.fn(),
  disconnect: vi.fn(),
  websocketOptions: null as null | { onMessage?: (message: object) => void },
  websocketPath: '',
  addWebSocketMessage: vi.fn(),
  applyWebSocketMessage: vi.fn(),
  setWebSocketConnected: vi.fn(),
}));

const route = reactive({ path: '/', fullPath: '/', name: 'dashboard' as string | symbol | null | undefined });

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: mocks.useWebSocket,
}));

vi.mock('@/stores/dashboard', () => ({
  useDashboardStore: () => ({
    websocketConnected: false,
    addWebSocketMessage: mocks.addWebSocketMessage,
    setWebSocketConnected: mocks.setWebSocketConnected,
  }),
}));

vi.mock('@/stores/strategies', () => ({
  useStrategiesStore: () => ({ applyWebSocketMessage: mocks.applyWebSocketMessage }),
}));

vi.mock('vue-router', () => ({
  useRoute: () => route,
}));

const components = {
  ElContainer: defineHostComponent('el-container'),
  ElAside: defineHostComponent('el-aside'),
  ElMenu: defineHostComponent('el-menu'),
  ElMenuItem: defineHostComponent('el-menu-item'),
  ElDrawer: defineHostComponent('el-drawer'),
  ElMain: defineHostComponent('el-main'),
  ElSelect: defineHostComponent('el-select'),
  ElOption: defineHostComponent('el-option'),
  RouterView: defineHostComponent('router-view'),
};

async function mountApp() {
  return mount(App, {
    components,
    locale: 'en',
  });
}

function findAppShellMain(wrapper: Awaited<ReturnType<typeof mountApp>>) {
  return wrapper.find((node) => node.type === 'el-container' && node.props.class === 'app-shell__main');
}

describe('App shell', () => {
  beforeEach(() => {
    route.path = '/';
    route.fullPath = '/';
    route.name = 'dashboard';
    mocks.useWebSocket.mockClear();
    mocks.connect.mockReset();
    mocks.disconnect.mockReset();
    mocks.addWebSocketMessage.mockReset();
    mocks.applyWebSocketMessage.mockReset();
    mocks.setWebSocketConnected.mockReset();
    mocks.websocketOptions = null;
    mocks.websocketPath = '';
    mocks.useWebSocket.mockImplementation((path: string, options: { onMessage?: (message: object) => void }) => {
      mocks.websocketPath = path;
      mocks.websocketOptions = options;
      const socket = {
        connected: ref(false),
        messages: ref([]),
        connect: mocks.connect,
        disconnect: mocks.disconnect,
      };
      onBeforeUnmount(socket.disconnect);
      return socket;
    });
  });

  it('keeps the app main container vertical with header and content stacked', async () => {
    const wrapper = await mountApp();
    const main = findAppShellMain(wrapper);
    const style = main.props.style as Record<string, string> | undefined;

    expect(main.props.direction).toBe('vertical');
    expect(main.props.class).toBe('app-shell__main');
    expect(style?.['flex-direction']).toBe('column');
    expect(main.children.map((child) => child.type)).toEqual(['header', 'el-main']);
    expect(main.children[0].parent).toBe(main);
    expect(main.children[1].parent).toBe(main);

    wrapper.unmount();
  });

  it('owns one websocket connection and forwards each message to both stores', async () => {
    const wrapper = await mountApp();

    expect(mocks.useWebSocket).toHaveBeenCalledTimes(1);
    expect(mocks.websocketPath).toBe('/ws');
    expect(mocks.connect).toHaveBeenCalledTimes(1);
    expect(wrapper.find((node) => node.type === 'router-view')).toBeTruthy();

    const message = { type: 'strategy_status', strategy: 'desk:btc', status: 'running' };
    mocks.websocketOptions?.onMessage?.(message);
    expect(mocks.addWebSocketMessage).toHaveBeenCalledWith(message);
    expect(mocks.applyWebSocketMessage).toHaveBeenCalledWith(message);
    expect(mocks.addWebSocketMessage.mock.calls[0][0]).toBe(message);
    expect(mocks.applyWebSocketMessage.mock.calls[0][0]).toBe(message);

    route.path = '/strategies';
    route.fullPath = '/strategies';
    route.name = 'strategies';
    await wrapper.flush();
    expect(mocks.useWebSocket).toHaveBeenCalledTimes(1);
    expect(mocks.connect).toHaveBeenCalledTimes(1);

    wrapper.unmount();
    expect(mocks.disconnect).toHaveBeenCalledTimes(1);
  });

  it('switches locale without changing the current route', async () => {
    route.path = '/market';
    route.fullPath = '/market?tab=orders#recent';
    route.name = 'market';

    const beforeSwitchFullPath = route.fullPath;
    const wrapper = await mountApp();
    const localeSelect = wrapper.find((node) => node.type === 'el-select');

    expect(wrapper.text()).toContain('Quant Trading Console');
    expect(wrapper.text()).toContain('Market');

    await wrapper.invoke(localeSelect, 'onUpdate:modelValue', 'zh-CN');

    expect(wrapper.text()).toContain('量化交易控制台');
    expect(wrapper.text()).toContain('行情');
    expect(route.fullPath).toBe(beforeSwitchFullPath);

    wrapper.unmount();
  });

  it('closes mobile navigation on menu selection and route changes, then restores focus', async () => {
    const wrapper = await mountApp();
    const menuButton = wrapper.find((node) => node.type === 'button' && node.props['aria-label'] === 'Open navigation menu');
    const drawer = () => wrapper.find((node) => node.type === 'el-drawer');
    const drawerMenu = () => {
      const menu = wrapper.findAll((node) => node.type === 'el-menu').find((node) => {
        let current = node.parent;
        while (current) {
          if (current.type === 'el-drawer') return true;
          current = current.parent;
        }
        return false;
      });
      if (!menu) throw new Error('Drawer menu not found');
      return menu;
    };

    expect(drawer().props.modelValue).toBe(false);
    await wrapper.trigger(menuButton, 'click');
    expect(drawer().props.modelValue).toBe(true);
    expect(drawer().props).toMatchObject({ title: 'Open navigation menu', 'aria-label': 'Open navigation menu' });

    await wrapper.invoke(drawerMenu(), 'onSelect', '/');
    expect(drawer().props.modelValue).toBe(false);

    await wrapper.invoke(drawer(), 'onClosed');
    await wrapper.flush();
    expect(menuButton.props['data-focused']).toBe(true);

    route.path = '/market';
    route.fullPath = '/market';
    route.name = 'market';
    await wrapper.flush();

    await wrapper.trigger(menuButton, 'click');
    expect(drawer().props.modelValue).toBe(true);

    route.fullPath = '/market?tab=orders#recent';
    await wrapper.flush();
    expect(drawer().props.modelValue).toBe(false);

    wrapper.unmount();
  });
});
