import useWebSocket from 'react-use-websocket';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { WebSocketMessage, ConnectionStatus, StateMessage } from '../types';

/**
 * Resolve a working backend base by probing /health on candidates.
 * Uses Vite env (VITE_API_URL) if present, then common localhost bases.
 */
export async function pickBackendBase(): Promise<string> {
  const envBase = (import.meta as any).env?.VITE_API_URL as string | undefined; // Vite exposes VITE_* at import.meta.env
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
} [5][3]

export function toWsUrl(httpBase: string): string {
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
} [3]

/* ----------------------- Minimal REST client ----------------------- */

export type ApiClient = {
  start: () => Promise<Response>;
  reset: () => Promise<Response>;
  rerunOptimized: (seed?: number, force?: boolean) => Promise<Response>;
  getState: () => Promise<Response>;
};

export function createApi(httpBase: string): ApiClient {
  const base = httpBase.replace(/\/$/, '');
  return {
    start: () => fetch(base + '/start', { method: 'POST' }),
    reset: () => fetch(base + '/reset', { method: 'POST' }),
    rerunOptimized: (seed: number = 42, force: boolean = false) =>
      fetch(base + `/rerun-optimized?seed=${encodeURIComponent(seed)}&force=${force ? 'true' : 'false'}`, { method: 'POST' }),
    getState: () => fetch(base + '/state'),
  };
} [3]

/* ----------------------- Hook ----------------------- */

export interface UseWebSocketClient {
  // streaming artifacts
  lastMessage: WebSocketMessage | null;
  lastState: StateMessage | null;         // latest authoritative simulator state
  connectionStatus: ConnectionStatus;
  sendMessage: (message: any) => void;

  // base + convenience REST
  httpBase: string | null;
  api: ApiClient | null;

  // helpers that POST then optionally force-refresh one GET /state
  startSimulation: (refresh?: boolean) => Promise<void>;
  resetSimulation: (refresh?: boolean) => Promise<void>;
  rerunOptimized: (seed?: number, force?: boolean) => Promise<Response>;
}

export function useWebSocketClient(): UseWebSocketClient {
  const [httpBase, setHttpBase] = useState<string | null>(null);
  const [wsUrl, setWsUrl] = useState<string | null>(null);

  // keep last "state" frame separate from general lastMessage
  const [lastState, setLastState] = useState<StateMessage | null>(null);
  const [lastParsed, setLastParsed] = useState<WebSocketMessage | null>(null);

  // resolve base once
  useEffect(() => {
    let mounted = true;
    pickBackendBase().then((base) => {
      if (!mounted) return;
      setHttpBase(base);
      setWsUrl(toWsUrl(base));
    });
    return () => { mounted = false; };
  }, []);

  const {
    lastMessage,
    readyState,
    sendMessage: sendRawMessage,
    lastError,
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
      try {
        const obj = JSON.parse(event.data) as WebSocketMessage;
        setLastParsed(obj);
        if (obj?.type === 'state') {
          // only update lastState when a state frame arrives
          setLastState(obj as unknown as StateMessage);
        }
      } catch (e) {
        console.error('WS parse error:', e);
      }
    },
    shouldReconnect: () => true,       // reconnect on close
    reconnectAttempts: 50,             // avoid flapping during dev
    reconnectInterval: 1500,           // snappier reconnects
    share: true,
    retryOnError: true,                // reconnect on error
  }, wsUrl ? true : false);            // defer connect until URL resolved [2]

  // MDN: 0 CONNECTING, 1 OPEN, 2 CLOSING, 3 CLOSED
  const connectionStatus: ConnectionStatus = {
    connected: readyState === 1,
    reconnecting: readyState === 0,
    lastError: (lastError as any)?.message || null,
  }; [4]

  const sendMessage = (message: any) => {
    if (readyState === 1) {
      sendRawMessage(JSON.stringify(message));
    } else {
      console.warn('WebSocket not connected, cannot send message');
    }
  };

  const api = useMemo(() => (httpBase ? createApi(httpBase) : null), [httpBase]);

  // Helpers that POST then optionally GET /state once to force an immediate UI flip
  const startSimulation = useCallback(async (refresh: boolean = true) => {
    if (!api) return;
    const r = await api.start();
    if (!r.ok) throw new Error('start failed');
    if (refresh) {
      const s = await api.getState();
      if (s.ok) {
        const json = (await s.json()) as StateMessage;
        setLastState(json);
      }
    }
  }, [api]);

  const resetSimulation = useCallback(async (refresh: boolean = true) => {
    if (!api) return;
    const r = await api.reset();
    if (!r.ok) throw new Error('reset failed');
    if (refresh) {
      const s = await api.getState();
      if (s.ok) {
        const json = (await s.json()) as StateMessage;
        setLastState(json);
      }
    }
  }, [api]);

  const rerunOptimized = useCallback(async (seed: number = 42, force: boolean = false) => {
    if (!api) throw new Error('api not ready');
    return api.rerunOptimized(seed, force);
  }, [api]);

  // Parse the raw lastMessage for consumers that still need it
  let parsedMessage: WebSocketMessage | null = lastParsed;
  if (!parsedMessage && (lastMessage as any)?.data) {
    try {
      parsedMessage = JSON.parse((lastMessage as any).data) as WebSocketMessage;
    } catch (error) {
      console.error('Failed to parse WebSocket message:', error);
    }
  }

  return {
    lastMessage: parsedMessage,
    lastState,
    connectionStatus,
    sendMessage,
    httpBase,
    api,
    startSimulation,
    resetSimulation,
    rerunOptimized,
  };
}
