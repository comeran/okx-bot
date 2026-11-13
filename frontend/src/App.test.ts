import { onBeforeUnmount, reactive, ref, type Ref } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { defineHostComponent, mount } from '@/test-utils/mount';
import App from './App.vue';
import type { ExchangeAuthentication } from '@/types/executionContext';

const mocks = vi.hoisted(() => ({
  useWebSocket: vi.fn(),
  connect: vi.fn(),
  disconnect: vi.fn(),
  websocketOptions: [] as Array<{ onMessage?: (message: object) => void; onClose?: () => void }>,
  websocketPaths: [] as string[],
  addWebSocketMessage: vi.fn(),
  applyWebSocketMessage: vi.fn(),
  setWebSocketConnected: vi.fn(),
  ensureContext: vi.fn(),
  reloadContext: vi.fn(),
  loadData: vi.fn(),
  autoSyncExchangeAccount: vi.fn(),
  invalidateContext: vi.fn(),
  checkAuthentication: vi.fn(),
  socketConnections: [] as Ref<boolean>[],
}));

const authority = reactive({
  epoch: 1,
  context: { available: true, account_scope_id: 'scope-A', environment: 'sandbox' },
  loading: false,
  authentication: null as ExchangeAuthentication | null,
  authenticationChecking: false,
  checkAuthentication: mocks.checkAuthentication,
  ensure: mocks.ensureContext,
  reload: mocks.reloadContext,
  invalidate: mocks.invalidateContext,
});
vi.mock('@/stores/accountContext', () => ({ useAccountContextStore: () => authority }));

const route = reactive({ path: '/', fullPath: '/', name: 'dashboard' as string | symbol | null | undefined });

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: mocks.useWebSocket,
}));

vi.mock('@/stores/dashboard', () => ({
  useDashboardStore: () => ({
    websocketConnected: false,
    loadInitialData: mocks.loadData,
    autoSyncExchangeAccount: mocks.autoSyncExchangeAccount,
    addWebSocketMessage: mocks.addWebSocketMessage,
    setWebSocketConnected: mocks.setWebSocketConnected,
  }),
}));

vi.mock('@/stores/strategies', () => ({
  useStrategiesStore: () => ({ applyWebSocketMessage: mocks.applyWebSocketMessage }),
}));

vi.mock('vue-router', async () => {
  const { defineComponent, h } = await import('vue');
  return {
    useRoute: () => route,
    RouterLink: defineComponent({
      props: { to: { type: String, required: true } },
      setup(props, { slots }) {
        return () => h('a', { href: props.to }, slots.default?.());
      },
    }),
  };
});

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
    authority.epoch = 1;
    authority.context = { available: true, account_scope_id: 'scope-A', environment: 'sandbox' };
    mocks.ensureContext.mockResolvedValue(authority.context);
    authority.authentication = null;
    authority.authenticationChecking = false;
    mocks.checkAuthentication.mockReset().mockResolvedValue(null);
    mocks.socketConnections = [];
    mocks.reloadContext.mockReset();
    mocks.reloadContext.mockResolvedValue(authority.context);
    mocks.loadData.mockReset();
    mocks.loadData.mockResolvedValue(undefined);
    mocks.autoSyncExchangeAccount.mockReset();
    mocks.autoSyncExchangeAccount.mockResolvedValue(undefined);
    mocks.invalidateContext.mockReset();
    mocks.invalidateContext.mockImplementation(() => {
      authority.context = null as never;
      authority.epoch += 1;
    });
    route.path = '/';
    route.fullPath = '/';
    route.name = 'dashboard';
    mocks.useWebSocket.mockClear();
    mocks.connect.mockReset();
    mocks.disconnect.mockReset();
    mocks.addWebSocketMessage.mockReset();
    mocks.applyWebSocketMessage.mockReset();
    mocks.setWebSocketConnected.mockReset();
    mocks.websocketOptions = [];
    mocks.websocketPaths = [];
    mocks.useWebSocket.mockImplementation((
      path: string | (() => string),
      options: { onMessage?: (message: object) => void },
    ) => {
      mocks.websocketPaths.push(typeof path === 'function' ? path() : path);
      mocks.websocketOptions.push(options);
      const socket = {
        connected: ref(false),
        messages: ref([]),
        connect: mocks.connect,
        disconnect: mocks.disconnect,
      };
      mocks.socketConnections.push(socket.connected);
      onBeforeUnmount(socket.disconnect);
      return socket;
    });
  });

  it('shows local transport separately from failed OKX authentication and keeps the failure visible across pages', async () => {
    authority.authentication = {
      account_scope_id: 'scope-A', environment: 'sandbox', status: 'failed',
      reason: 'invalid_credentials', provider_code: '50119', checked_at: 1,
    };
    const wrapper = await mountApp();
    mocks.socketConnections[0]!.value = true;
    mocks.socketConnections[1]!.value = true;
    await wrapper.flush();
    expect(wrapper.text()).toContain('Local service connected');
    expect(wrapper.text()).toContain('OKX authentication failed');
    expect(wrapper.text()).toContain('50119');
    expect(wrapper.text()).toContain('OKX sandbox');
    expect(wrapper.findAll((node) => node.type === 'a' && node.props.href === '/settings')).toHaveLength(1);
    expect(wrapper.text()).not.toContain('OKX authentication verified');
    expect(mocks.checkAuthentication).toHaveBeenCalledTimes(1);
    route.name = 'settings'; route.fullPath = '/settings';
    await wrapper.flush();
    expect(wrapper.findAll((node) => node.props.role === 'alert')).toHaveLength(1);
    mocks.socketConnections[1]!.value = false;
    await wrapper.flush();
    expect(wrapper.text()).toContain('Local service disconnected');
    expect(wrapper.text()).toContain('OKX authentication failed');
    wrapper.unmount();
  });

  it('starts one scoped account sync when authentication becomes verified', async () => {
    const wrapper = await mountApp();
    expect(mocks.autoSyncExchangeAccount).not.toHaveBeenCalled();

    authority.authentication = {
      account_scope_id: 'scope-A', environment: 'sandbox', status: 'authenticated',
      reason: null, provider_code: null, checked_at: 2,
    };
    await wrapper.flush();

    expect(mocks.autoSyncExchangeAccount).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it('does not render untrusted provider reason text or unsafe codes in its persistent auth notice', async () => {
    authority.authentication = {
      account_scope_id: 'scope-A', environment: 'sandbox', status: 'unavailable',
      reason: 'private-secret' as never, provider_code: '12345', checked_at: 1,
    };
    const wrapper = await mountApp();
    expect(wrapper.text()).toContain('OKX authentication unavailable');
    expect(wrapper.text()).not.toContain('private-secret');
    expect(wrapper.text()).not.toContain('12345');
    wrapper.unmount();
  });

  it('keeps the app main container vertical with header and content stacked', async () => {
    const wrapper = await mountApp();
    const main = findAppShellMain(wrapper);
    const style = main.props.style as Record<string, string> | undefined;

    expect(main.props.direction).toBe('vertical');
    expect(main.props.class).toBe('app-shell__main');
    expect(style?.['flex-direction']).toBe('column');
    expect(main.children.map((child) => child.type)).toEqual(['header', 'main']);
    expect(main.children[0].parent).toBe(main);
    expect(main.children[1].parent).toBe(main);

    wrapper.unmount();
  });

  it('lets keyboard users skip navigation and focus the main content', async () => {
    const wrapper = await mountApp();
    const skipLinks = wrapper.findAll((node) => node.type === 'a' && node.props.href === '#main-content');

    expect(skipLinks).toHaveLength(1);
    const main = wrapper.getById('main-content');
    expect(main.type).toBe('main');
    expect(main.props.tabindex).toBe(-1);

    await wrapper.trigger(skipLinks[0]!, 'click');
    expect(main.props['data-focused']).toBe(true);

    wrapper.unmount();
  });

  it('owns separate exchange and wildcard strategy connections with scoped message routing', async () => {
    const wrapper = await mountApp();

    expect(mocks.useWebSocket).toHaveBeenCalledTimes(2);
    expect(mocks.websocketPaths).toEqual([
      '/ws?scope=exchange&expected_account_scope_id=scope-A',
      '/ws?scope=strategy&strategy=*',
    ]);
    expect(mocks.connect).toHaveBeenCalledTimes(2);
    expect(wrapper.find((node) => node.type === 'router-view')).toBeTruthy();

    const exchangeMessage = { type: 'account', scope: 'exchange', account: { equity: 100, daily_pnl: 0 } };
    mocks.websocketOptions[0]?.onMessage?.(exchangeMessage);
    expect(mocks.addWebSocketMessage).toHaveBeenCalledWith(exchangeMessage);
    expect(mocks.applyWebSocketMessage).not.toHaveBeenCalled();

    const strategyMessage = { type: 'strategy_status', scope: 'strategy', strategy: 'desk:btc', status: 'running' };
    mocks.websocketOptions[1]?.onMessage?.(strategyMessage);
    const configMessage = { type: 'strategy_config_updated', scope: 'strategy', strategy: 'desk:btc', config: { name: 'desk:btc' } };
    mocks.websocketOptions[1]?.onMessage?.(configMessage);
    expect(mocks.addWebSocketMessage).toHaveBeenNthCalledWith(2, strategyMessage);
    expect(mocks.addWebSocketMessage).toHaveBeenNthCalledWith(3, configMessage);
    expect(mocks.applyWebSocketMessage).toHaveBeenNthCalledWith(1, strategyMessage);
    expect(mocks.applyWebSocketMessage).toHaveBeenNthCalledWith(2, configMessage);

    route.path = '/strategies';
    route.fullPath = '/strategies';
    route.name = 'strategies';
    await wrapper.flush();
    expect(mocks.useWebSocket).toHaveBeenCalledTimes(2);
    expect(mocks.connect).toHaveBeenCalledTimes(2);

    wrapper.unmount();
    expect(mocks.disconnect).toHaveBeenCalledTimes(2);
  });

  it('reloads authoritative context on a control event without using its claimed account', async () => {
    const wrapper = await mountApp();
    mocks.websocketOptions[0]?.onMessage?.({ type: 'context_changed', context: { account_scope_id: 'forged' } });
    await wrapper.flush();
    expect(mocks.reloadContext).toHaveBeenCalledTimes(1);
    expect(mocks.addWebSocketMessage).not.toHaveBeenCalled();
    expect(authority.context.account_scope_id).toBe('scope-A');
    expect(mocks.loadData).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it('delays close retries, invalidates immediately, and cancels stale timers on reload and unmount', async () => {
    vi.useFakeTimers();
    const wrapper = await mountApp();
    mocks.websocketOptions[0]?.onClose?.();
    expect(mocks.invalidateContext).toHaveBeenCalledTimes(1);
    expect(mocks.reloadContext).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2999); expect(mocks.reloadContext).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1); expect(mocks.reloadContext).toHaveBeenCalledTimes(1);
    mocks.websocketOptions[0]?.onClose?.();
    authority.epoch += 1;
    await vi.advanceTimersByTimeAsync(3000); expect(mocks.reloadContext).toHaveBeenCalledTimes(1);
    mocks.websocketOptions[0]?.onClose?.(); wrapper.unmount();
    await vi.advanceTimersByTimeAsync(3000); expect(mocks.reloadContext).toHaveBeenCalledTimes(1);
  });
  afterEach(() => { vi.useRealTimers(); });

  it('switches locale without changing the current route', async () => {
    route.path = '/market';
    route.fullPath = '/market?tab=orders#recent';
    route.name = 'market';

    const beforeSwitchFullPath = route.fullPath;
    const wrapper = await mountApp();
    const localeSelect = wrapper.find((node) => node.type === 'el-select');

    expect(wrapper.text()).toContain('Workspace');
    expect(wrapper.text()).toContain('Market');

    await wrapper.invoke(localeSelect, 'onUpdate:modelValue', 'zh-CN');

    expect(wrapper.text()).toContain('工作空间');
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
    expect(menuButton.props['aria-expanded']).toBe(false);
    expect(menuButton.props['aria-controls']).toBe('mobile-navigation');
    expect(drawer().props.id).toBe('mobile-navigation');
    await wrapper.trigger(menuButton, 'click');
    expect(drawer().props.modelValue).toBe(true);
    expect(menuButton.props['aria-expanded']).toBe(true);
    expect(drawer().props).toMatchObject({ title: 'Open navigation menu', 'aria-label': 'Open navigation menu' });

    await wrapper.invoke(drawerMenu(), 'onSelect', '/');
    expect(drawer().props.modelValue).toBe(false);
    expect(menuButton.props['aria-expanded']).toBe(false);

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
