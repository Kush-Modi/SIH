import useWebSocket from 'react-use-websocket';
import { WebSocketMessage, ConnectionStatus } from '../types';

const WS_URL = 'ws://localhost:8000/ws';
console.log('WebSocket URL:', WS_URL);

export interface UseWebSocketClient {
  lastMessage: WebSocketMessage | null;
  connectionStatus: ConnectionStatus;
  sendMessage: (message: any) => void;
}

export function useWebSocketClient(): UseWebSocketClient {
  const {
    lastMessage,
    readyState,
    sendMessage: sendRawMessage,
    lastError
  } = useWebSocket(WS_URL, {
    onOpen: () => {
      console.log('WebSocket connected to:', WS_URL);
    },
    onClose: (event) => {
      console.log('WebSocket disconnected:', event.code, event.reason);
    },
    onError: (error) => {
      console.error('WebSocket error:', error);
    },
    onMessage: (event) => {
      console.log('WebSocket message received:', event.data);
    },
    shouldReconnect: () => true,
    reconnectAttempts: 10,
    reconnectInterval: 3000,
  });

  const connectionStatus: ConnectionStatus = {
    connected: readyState === 1, // WebSocket.OPEN
    reconnecting: readyState === 2, // WebSocket.CONNECTING
    lastError: (lastError as any)?.message || null
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
