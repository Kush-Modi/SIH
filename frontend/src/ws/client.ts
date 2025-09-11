import useWebSocket from 'react-use-websocket';
import { useEffect, useMemo, useState } from 'react';
import { WebSocketMessage, ConnectionStatus } from '../types';

// Resolve a working backend base by probing /health on candidates
async function pickBackendBase(): Promise<string> {
  const envBase = (import.meta as any).env?.VITE_API_URL as string | undefined;
  const host = window.location.hostname || 'localhost';
  const candidates = [
    envBase,
    `http://${host}:8000`,
    'http://127.0.0.1:8000',
    'http://localhost:8000',
  ].filter(Boolean) as string[];
  for (const base of candidates) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 1200);
      const r = await fetch(base.replace(/\/$/, '') + '/health', { signal: ctrl.signal });
      clearTimeout(t);
      if (r.ok) return base;
    } catch {}
  }
  return `http://${host}:8000`;
}

function toWsUrl(httpBase: string): string {
  try {
    const u = new URL(httpBase);
    u.protocol = u.protocol.startsWith('https') ? 'wss:' : 'ws:';
    u.pathname = '/ws';
    u.search = '';
    u.hash = '';
    return u.toString();
  } catch {
    return 'ws://localhost:8000/ws';
  }
}

export interface UseWebSocketClient {
  lastMessage: WebSocketMessage | null;
  connectionStatus: ConnectionStatus;
  sendMessage: (message: any) => void;
}

export function useWebSocketClient(): UseWebSocketClient {
  const [wsUrl, setWsUrl] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    pickBackendBase().then((base) => {
      if (mounted) setWsUrl(toWsUrl(base));
    });
    return () => {
      mounted = false;
    };
  }, []);

  const {
    lastMessage,
    readyState,
    sendMessage: sendRawMessage,
    lastError
  } = useWebSocket(wsUrl || 'ws://localhost:8000/ws', {
    onOpen: () => {
      console.log('WebSocket connected to:', wsUrl);
    },
    onClose: (event) => {
      console.log('WebSocket disconnected:', event.code, event.reason);
    },
    onError: (error) => {
      console.error('WebSocket error:', error);
    },
    onMessage: (event) => {
      // avoid log spam but keep a breadcrumb
      try { const obj = JSON.parse(event.data); console.debug('WS frame:', obj.type || 'unknown'); } catch {}
    },
    shouldReconnect: () => true,
    reconnectAttempts: 50,
    reconnectInterval: 1500,
    share: true,
    retryOnError: true,
  }, wsUrl ? true : false);

  const connectionStatus: ConnectionStatus = {
    connected: readyState === 1, // OPEN
    reconnecting: readyState === 0, // CONNECTING
    lastError: (lastError as any)?.message || null,
  };

  const sendMessage = (message: any) => {
    if (readyState === 1) {
      sendRawMessage(JSON.stringify(message));
    } else {
      console.warn('WebSocket not connected, cannot send message');
    }
  };

  // Parse the last message
  let parsedMessage: WebSocketMessage | null = null;
  if ((lastMessage as any)?.data) {
    try {
      parsedMessage = JSON.parse((lastMessage as any).data) as WebSocketMessage;
    } catch (error) {
      console.error('Failed to parse WebSocket message:', error);
    }
  }

  return {
    lastMessage: parsedMessage,
    connectionStatus,
    sendMessage
  };
}
