import { onBeforeUnmount, reactive, ref, type Component } from 'vue';
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
  saveLocale: vi.fn(),
}));

const route = reactive({ path: '/' });

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: mocks.useWebSocket,
}));

vi.mock('@/stores/dashboard', () => ({
  useDashboardStore: () => ({
    addWebSocketMessage: mocks.addWebSocketMessage,
    setWebSocketConnected: mocks.setWebSocketConnected,
  }),
}));

vi.mock('@/stores/strategies', () => ({
  useStrategiesStore: () => ({ applyWebSocketMessage: mocks.applyWebSocketMessage }),
}));

vi.mock('vue-router', () => ({ useRoute: () => route }));

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: ref('en'),
    t: (key: string) => key,
  }),
}));

vi.mock('./i18n', () => ({
  locales: ['en', 'zh-CN'],
  saveLocale: mocks.saveLocale,
}));

const components: Record<string, Component> = {
  ElContainer: defineHostComponent('el-container'),
  ElAside: defineHostComponent('el-aside'),
  ElMenu: defineHostComponent('el-menu'),
  ElMenuItem: defineHostComponent('el-menu-item'),
  ElDrawer: defineHostComponent('el-drawer'),
  ElHeader: defineHostComponent('el-header'),
  ElMain: defineHostComponent('el-main'),
  ElSelect: defineHostComponent('el-select'),
  ElOption: defineHostComponent('el-option'),
  RouterView: defineHostComponent('router-view'),
};

async function mountApp() {
  return mount(App, { components });
}

describe('App shell', () => {
  beforeEach(() => {
    route.path = '/';
    mocks.useWebSocket.mockClear();
    mocks.connect.mockReset();
    mocks.disconnect.mockReset();
    mocks.addWebSocketMessage.mockReset();
    mocks.applyWebSocketMessage.mockReset();
    mocks.setWebSocketConnected.mockReset();
    mocks.saveLocale.mockReset();
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

  it('owns one connection across route changes, dispatches the same message object, and cleans up', async () => {
    const wrapper = await mountApp();

    expect(mocks.useWebSocket).toHaveBeenCalledTimes(1);
    expect(mocks.websocketPath).toBe('/ws');
    expect(mocks.connect).toHaveBeenCalledTimes(1);

    const message = { type: 'strategy_status', strategy: 'desk:btc', status: 'running' };
    mocks.websocketOptions?.onMessage?.(message);
    expect(mocks.addWebSocketMessage).toHaveBeenCalledWith(message);
    expect(mocks.applyWebSocketMessage).toHaveBeenCalledWith(message);
    expect(mocks.addWebSocketMessage.mock.calls[0][0]).toBe(message);
    expect(mocks.applyWebSocketMessage.mock.calls[0][0]).toBe(message);

    route.path = '/strategies';
    await wrapper.flush();
    expect(mocks.useWebSocket).toHaveBeenCalledTimes(1);
    expect(mocks.connect).toHaveBeenCalledTimes(1);

    wrapper.unmount();
    expect(mocks.disconnect).toHaveBeenCalledTimes(1);
  });

  it('opens the mobile drawer, closes it on route changes, and restores menu-button focus', async () => {
    const wrapper = await mountApp();
    const menuButton = wrapper.find((node) => node.type === 'button' && node.props['aria-label'] === 'app.mobileMenu');
    const drawer = () => wrapper.find((node) => node.type === 'el-drawer');

    expect(drawer().props.modelValue).toBe(false);
    await wrapper.trigger(menuButton, 'click');
    expect(drawer().props.modelValue).toBe(true);
    expect(drawer().props).toMatchObject({ title: 'app.mobileMenu', 'aria-label': 'app.mobileMenu' });

    route.path = '/market';
    await wrapper.flush();
    expect(drawer().props.modelValue).toBe(false);

    await wrapper.invoke(drawer(), 'onClosed');
    await wrapper.flush();
    expect(menuButton.props['data-focused']).toBe(true);
  });

  it('renders the desktop sidebar at its specified width', async () => {
    const wrapper = await mountApp();
    const sidebar = wrapper.find((node) => node.type === 'el-aside' && node.props.class === 'sidebar');

    expect(sidebar.props.width).toBe('220px');
    expect(wrapper.find((node) => node.type === 'router-view')).toBeTruthy();
  });
});
