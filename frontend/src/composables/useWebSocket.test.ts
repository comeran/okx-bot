import { afterEach, describe, expect, it, vi } from 'vitest';

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
});
