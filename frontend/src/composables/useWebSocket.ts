import { getCurrentInstance, onBeforeUnmount, ref } from 'vue';

import type { DashboardWebSocketMessage } from '@/types/dashboard';

interface UseWebSocketOptions {
  onMessage?: (message: DashboardWebSocketMessage) => void;
  reconnectDelayMs?: number;
}

const LOCAL_MESSAGE_HISTORY_LIMIT = 20;
const DEFAULT_RECONNECT_DELAY_MS = 3000;

function resolveWebSocketUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${path}`;
}

export function useWebSocket(path = '/ws', options: UseWebSocketOptions = {}) {
  const connected = ref(false);
  const messages = ref<DashboardWebSocketMessage[]>([]);
  const reconnectDelayMs = options.reconnectDelayMs ?? DEFAULT_RECONNECT_DELAY_MS;
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let manuallyDisconnected = false;

  const clearReconnectTimer = () => {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const disconnect = () => {
    manuallyDisconnected = true;
    clearReconnectTimer();
    if (!socket) {
      connected.value = false;
      return;
    }

    socket.close();
    socket = null;
    connected.value = false;
  };

  const connect = () => {
    manuallyDisconnected = false;
    clearReconnectTimer();
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    socket = new WebSocket(resolveWebSocketUrl(path));

    socket.onopen = () => {
      connected.value = true;
    };

    socket.onclose = () => {
      connected.value = false;
      socket = null;
      if (!manuallyDisconnected) {
        reconnectTimer = window.setTimeout(connect, reconnectDelayMs);
      }
    };

    socket.onerror = () => {
      connected.value = false;
    };

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as DashboardWebSocketMessage;
        messages.value.unshift(message);
        messages.value = messages.value.slice(0, LOCAL_MESSAGE_HISTORY_LIMIT);
        options.onMessage?.(message);
      } catch {
        const message: DashboardWebSocketMessage = {
          type: 'raw',
          data: event.data,
        };
        messages.value.unshift(message);
        messages.value = messages.value.slice(0, LOCAL_MESSAGE_HISTORY_LIMIT);
        options.onMessage?.(message);
      }
    };
  };

  if (getCurrentInstance()) {
    onBeforeUnmount(disconnect);
  }

  return {
    connected,
    messages,
    connect,
    disconnect,
  };
}
