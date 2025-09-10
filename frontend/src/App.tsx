import React, { useState, useEffect, useCallback } from 'react';
import { StateMessage, EventMessage, ControlPayload, DelayInjection, BlockIssueInjection } from './types';
import { useWebSocketClient } from './ws/client';
import { KPIBar } from './components/KPIBar';
import { TrackView } from './components/TrackView';
import { ControlPanel } from './components/ControlPanel';
import { NarrativePanel } from './components/NarrativePanel';
import './App.css';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  const { lastMessage, connectionStatus } = useWebSocketClient();
  const [state, setState] = useState<StateMessage | null>(null);
  const [recentEvents, setRecentEvents] = useState<EventMessage[]>([]);

  // Process incoming WS frames; depend only on lastMessage to avoid loops
  useEffect(() => {
    if (!lastMessage) return;
    if (lastMessage.type === 'state') {
      setState(lastMessage);
    } else if (lastMessage.type === 'event') {
      setRecentEvents(prev => {
        const updated = [lastMessage, ...prev];
        return updated.slice(0, 20);
      });
    }
  }, [lastMessage]);

  const apiCall = useCallback(async (endpoint: string, method: string = 'GET', body?: any) => {
    const resp = await fetch(`${API_BASE}${endpoint}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!resp.ok) throw new Error(`API call failed: ${resp.status} ${resp.statusText}`);
    return resp.json();
  }, []);

  // Stable handlers so children don’t re-render unnecessarily
  const handleUpdateParameters = useCallback(async (params: ControlPayload) => {
    await apiCall('/control', 'POST', params);
  }, [apiCall]);

  const handleInjectDelay = useCallback(async (injection: DelayInjection) => {
    await apiCall('/inject/delay', 'POST', injection);
  }, [apiCall]);

  const handleSetBlockIssue = useCallback(async (injection: BlockIssueInjection) => {
    await apiCall('/inject/block-issue', 'POST', injection);
  }, [apiCall]);

  const clearEvents = useCallback(() => setRecentEvents([]), []);

  const isConnected = connectionStatus?.connected ?? false;
  const isReconnecting = connectionStatus?.reconnecting ?? false;
  const connClass = isConnected ? 'connected' : (isReconnecting ? 'reconnecting' : 'disconnected');
  const connText = isConnected ? 'Connected' : (isReconnecting ? 'Reconnecting…' : 'Disconnected');

  return (
    <div className="app">
      {/* Header with connection indicator and KPI bar */}
      <header className="app-header">
        <div className="header-content">
          <div className="header-left">
            <h1 className="app-title">Railway Control</h1>
            <div className={`connection-indicator ${connClass}`} role="status" aria-live="polite" title={connectionStatus?.lastError || ''}>
              <span className="connection-dot" aria-hidden />
              <span>{connText}</span>
              {connectionStatus?.lastError && <span className="connection-error" aria-hidden>⚠</span>}
            </div>
          </div>
          <div className="header-right">
            <KPIBar state={state} connectionStatus={connectionStatus} />
          </div>
        </div>
      </header>

      <div className="main-content">
        <div className="track-section">
          {!state ? (
            <div className="status-screen" role="status" aria-live="polite">
              <div className="status-icon">🚉</div>
              <h2>Connecting to simulator…</h2>
              <p className="status-message">Waiting for live state via WebSocket</p>
              <div className="loading-spinner" aria-hidden />
            </div>
          ) : (
            <>
              <TrackView state={state} />
              <NarrativePanel state={state} />
            </>
          )}
        </div>

        <div className="control-section">
          <ControlPanel
            state={state}
            onUpdateParameters={handleUpdateParameters}
            onInjectDelay={handleInjectDelay}
            onSetBlockIssue={handleSetBlockIssue}
          />
        </div>
      </div>

      {/* Events */}
      {recentEvents.length > 0 && (
        <div className="events-panel" aria-live="polite">
          <div className="events-header">
            <h3>Recent Events</h3>
            <button className="clear-events-btn" onClick={clearEvents}>Clear</button>
          </div>
          <div className="events-list">
            {recentEvents.map((event, index) => (
              <div
                key={`${event.event_id}-${index}`}
                className={`event-item ${event.event_kind.toLowerCase()}`}
              >
                <span className="event-time">
                  {new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </span>
                <span className="event-kind">{event.event_kind.replace(/_/g, ' ').toLowerCase()}</span>
                <span className="event-note">{event.note}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
