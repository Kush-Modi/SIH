import React, { useState, useEffect, useCallback } from 'react';
import { StateMessage, EventMessage, ControlPayload, DelayInjection, BlockIssueInjection } from './types';
import { useWebSocketClient } from './ws/client';
import { KPIBar } from './components/KPIBar';
import { TrackView } from './components/TrackView';
import { ControlPanel } from './components/ControlPanel';
import { NarrativePanel } from './components/NarrativePanel';
import SchedulePage from './pages/SchedulePage';
import './App.css';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function App() {
  const { lastMessage, connectionStatus } = useWebSocketClient();
  const [state, setState] = useState<StateMessage | null>(null);
  const [recentEvents, setRecentEvents] = useState<EventMessage[]>([]);
  const [currentPage, setCurrentPage] = useState<'simulation' | 'schedule'>('simulation');
  const [rerunResult, setRerunResult] = useState<any | null>(null);

  // Process incoming WS frames
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

  // Stable handlers
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

  // Completed banner actions
  const exportSnapshot = useCallback(async () => {
    if (state?.status !== 'COMPLETED') return;
    const resp = await fetch(`${API_BASE}/export_plan_input`, { method: 'POST' });
    if (!resp.ok) {
      alert('Snapshot only available after completion.');
      return;
    }
    const data = await resp.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'snapshot.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [state?.status]);

  const rerunOptimized = useCallback(async () => {
    // Either wait for completion, or call with ?force=true to run a clean A/B pair anytime
    const completed = state?.status === 'COMPLETED';
    const url = `${API_BASE}/rerun-optimized${completed ? '' : '?force=true'}`;
    const resp = await fetch(url, { method: 'POST' });
    if (!resp.ok) {
      alert('Rerun failed');
      return;
    }
    const json = await resp.json();
    setRerunResult(json);
  }, [state?.status]);

  const isConnected = connectionStatus?.connected ?? false;
  const isReconnecting = connectionStatus?.reconnecting ?? false;
  const connClass = isConnected ? 'connected' : (isReconnecting ? 'reconnecting' : 'disconnected');
  const connText = isConnected ? 'Connected' : (isReconnecting ? 'Reconnecting…' : 'Disconnected');

  return (
    <div className="app">
      {/* Header with navigation and connection indicator */}
      <header className="app-header">
        <div className="header-content">
          <div className="header-left">
            <h1 className="app-title">Railway Control System</h1>
            <nav className="main-navigation">
              <button
                className={`nav-button ${currentPage === 'simulation' ? 'active' : ''}`}
                onClick={() => setCurrentPage('simulation')}
              >
                Live Simulation
              </button>
              <button
                className={`nav-button ${currentPage === 'schedule' ? 'active' : ''}`}
                onClick={() => setCurrentPage('schedule')}
              >
                Schedule Optimizer
              </button>
            </nav>
            {currentPage === 'simulation' && (
              <div className={`connection-indicator ${connClass}`} role="status" aria-live="polite" title={connectionStatus?.lastError || ''}>
                <span className="connection-dot" aria-hidden />
                <span>{connText}</span>
                {connectionStatus?.lastError && <span className="connection-error" aria-hidden>⚠</span>}
              </div>
            )}
          </div>
          <div className="header-right">
            {currentPage === 'simulation' && (
              <KPIBar state={state} connectionStatus={connectionStatus} />
            )}
          </div>
        </div>
        {currentPage === 'simulation' && state?.status === 'COMPLETED' && (
          <div style={{ padding: '8px 24px 16px' }}>
            <div style={{
              background: '#f1f5f9', border: '1px solid #e2e8f0', borderRadius: 8, padding: 12,
              display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'space-between'
            }}>
              <div>
                <strong>Simulation completed</strong> — Export snapshot or Rerun (optimized)
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="nav-button" onClick={exportSnapshot}>Export Snapshot</button>
                <button className="nav-button active" onClick={rerunOptimized}>Rerun (optimized)</button>
              </div>
            </div>
          </div>
        )}
      </header>

      <div className="main-content">
        {currentPage === 'simulation' ? (
          <>
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
              {state?.status === 'COMPLETED' && rerunResult && (
                <div style={{ marginTop: 12 }}>
                  <div style={{ background: '#fff', border: '1px solid #e2e8f0', borderRadius: 8, padding: 12 }}>
                    {(() => {
                      const b = rerunResult.baseline; const o = rerunResult.optimized; const d = rerunResult.diff;
                      const top = (d?.trains || []).slice(0, 3);
                      return (
                        <div>
                          <div style={{ fontWeight: 600, marginBottom: 8 }}>Rerun (optimized) results</div>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                            <div>
                              <div>Avg delay: {b.avg_delay_min}m → {o.avg_delay_min}m (Δ {d.delta_avg_delay_min}m)</div>
                              <div>Duration: {b.duration_sec}s → {o.duration_sec}s (Δ {d.delta_duration_sec}s)</div>
                            </div>
                            <div>
                              <div>Holds applied: {(rerunResult.plan?.holds || []).length}</div>
                              <div>Top trains improved: {top.length ? top.map((t: any) => `${t.name} +${t.delta_delay_min}m`).join(', ') : '—'}</div>
                            </div>
                          </div>
                        </div>
                      );
                    })()}
                  </div>
                </div>
              )}
            </div>
          </>
        ) : (
          <SchedulePage />
        )}
      </div>

      {/* Events - only show on simulation page */}
      {currentPage === 'simulation' && recentEvents.length > 0 && (
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
