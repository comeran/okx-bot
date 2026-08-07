import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

import { useDashboardStore } from '@/stores/dashboard';
import { useStrategiesStore } from '@/stores/strategies';
import type { StrategyWebSocketMessage } from '@/types/strategy';
import { useWebSocket } from './useWebSocket';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;

  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  closed = false;

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  close() {
    this.closed = true;
  }
}

function installBrowserGlobals() {
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.stubGlobal('window', {
    location: { protocol: 'http:', host: 'localhost:3000' },
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  MockWebSocket.instances = [];
});

describe('useWebSocket', () => {
  it('reconnects after the socket closes', () => {
    vi.useFakeTimers();
    installBrowserGlobals();

    const websocket = useWebSocket('/ws', { reconnectDelayMs: 100 });
    websocket.connect();

    expect(MockWebSocket.instances).toHaveLength(1);

    MockWebSocket.instances[0].onclose?.();
    vi.advanceTimersByTime(100);

    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[1].url).toBe('ws://localhost:3000/ws');
  });

  it('does not reconnect after manual disconnect', () => {
    vi.useFakeTimers();
    installBrowserGlobals();

    const websocket = useWebSocket('/ws', { reconnectDelayMs: 100 });
    websocket.connect();
    websocket.disconnect();
    MockWebSocket.instances[0].onclose?.();
    vi.advanceTimersByTime(100);

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('dispatches one received_at timestamp to dashboard and strategy stores', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-03T12:00:00Z'));
    installBrowserGlobals();
    setActivePinia(createPinia());
    const dashboard = useDashboardStore();
    const strategies = useStrategiesStore();
    const receivedAt = new Date('2026-06-03T12:00:00Z').getTime();

    const websocket = useWebSocket('/ws', {
      onMessage: (message) => {
        dashboard.addWebSocketMessage(message);
        strategies.applyWebSocketMessage(message as StrategyWebSocketMessage);
      },
    });
    websocket.connect();
    MockWebSocket.instances[0].onmessage?.({
      data: JSON.stringify({ type: 'strategy_error', strategy: 'btc_ma', error: 'boom' }),
    });

    expect(websocket.messages.value[0].received_at).toBe(receivedAt);
    expect(dashboard.websocketMessages[0].received_at).toBe(receivedAt);
    expect(dashboard.websocketMessages[0]).toBe(websocket.messages.value[0]);
    expect(strategies.errorAuthorities.btc_ma).toEqual({
      timestamp: undefined,
      receivedAt,
    });
  });

  it('does not let automatically stamped delayed timestamped errors cross snapshots', async () => {
    vi.useFakeTimers();
    installBrowserGlobals();
    setActivePinia(createPinia());
    const dashboard = useDashboardStore();
    const strategies = useStrategiesStore();
    const reconcile = vi.spyOn(strategies, 'refreshStatusesForReconciliation').mockResolvedValue();

    const websocket = useWebSocket('/ws', {
      onMessage: (message) => {
        dashboard.addWebSocketMessage(message);
        strategies.applyWebSocketMessage(message as StrategyWebSocketMessage);
      },
    });
    websocket.connect();

    vi.setSystemTime(200);
    MockWebSocket.instances[0].onmessage?.({
      data: JSON.stringify({ type: 'snapshot', data: { strategy_errors: {} } }),
    });
    vi.setSystemTime(300);
    MockWebSocket.instances[0].onmessage?.({
      data: JSON.stringify({ type: 'strategy_error', strategy: 'btc_ma', error: 'stale boom', timestamp: 100 }),
    });
    await Promise.resolve();

    const delayedMessage = websocket.messages.value[0] as { timestamp?: number; received_at?: number };
    expect(delayedMessage.timestamp).toBe(100);
    expect(delayedMessage.received_at).toBe(300);
    expect(dashboard.websocketMessages[0]).toBe(websocket.messages.value[0]);
    expect(strategies.errors).toEqual({});
    expect(reconcile).toHaveBeenCalledTimes(1);
  });

  it('does not let automatically stamped delayed timestamped statuses cross snapshots', async () => {
    vi.useFakeTimers();
    installBrowserGlobals();
    setActivePinia(createPinia());
    const dashboard = useDashboardStore();
    const strategies = useStrategiesStore();
    const reconcile = vi.spyOn(strategies, 'refreshStatusesForReconciliation').mockResolvedValue();

    const websocket = useWebSocket('/ws', {
      onMessage: (message) => {
        dashboard.addWebSocketMessage(message);
        strategies.applyWebSocketMessage(message as StrategyWebSocketMessage);
      },
    });
    websocket.connect();

    vi.setSystemTime(200);
    MockWebSocket.instances[0].onmessage?.({
      data: JSON.stringify({ type: 'snapshot', data: { strategies: [] } }),
    });
    vi.setSystemTime(300);
    MockWebSocket.instances[0].onmessage?.({
      data: JSON.stringify({ type: 'strategy_status', strategy: 'btc_ma', status: 'running', timestamp: 100 }),
    });
    await Promise.resolve();

    const delayedMessage = websocket.messages.value[0] as { timestamp?: number; received_at?: number };
    expect(delayedMessage.timestamp).toBe(100);
    expect(delayedMessage.received_at).toBe(300);
    expect(dashboard.websocketMessages[0]).toBe(websocket.messages.value[0]);
    expect(strategies.statuses).toEqual({});
    expect(reconcile).toHaveBeenCalledTimes(1);
  });
});
