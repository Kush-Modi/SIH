import React, { useMemo, useRef, useEffect } from 'react';
import { StateMessage } from '../types';
import './KPIBar.css';

type ConnectionStatus = {
  connected: boolean;
  reconnecting?: boolean;
  lastError?: string | null;
};

interface KPIBarProps {
  state: StateMessage | null;
  connectionStatus?: ConnectionStatus; // optional for safety
}

export const KPIBar: React.FC<KPIBarProps> = ({ state, connectionStatus }) => {
  // Safe connection status defaults
  const cs: ConnectionStatus = {
    connected: connectionStatus?.connected ?? false,
    reconnecting: connectionStatus?.reconnecting ?? false,
    lastError: connectionStatus?.lastError ?? null
  };

  // Safe ISO parse
  const simTime = state?.sim_time ?? null;

  const formatClock = (isoString?: string | null) => {
    if (!isoString) return '--:--:--';
    const d = new Date(isoString);
    return isNaN(d.getTime()) ? '--:--:--' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const timeAgo = (isoString?: string | null) => {
    if (!isoString) return '—';
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return '—';
    const secs = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
    if (secs < 1) return 'just now';
    if (secs < 60) return `${secs}s ago`;
    const m = Math.floor(secs / 60);
    return m === 1 ? '1m ago' : `${m}m ago`;
  };

  // KPI access tolerant to snake_case/camelCase
  const avgDelayMin = useMemo(() => {
    const k: any = state?.kpis;
    const v = typeof k?.avg_delay_min === 'number' ? k.avg_delay_min : (typeof k?.avgDelayMin === 'number' ? k.avgDelayMin : 0);
    return Number.isFinite(v) ? v : 0;
  }, [state]);

  const trainsOnLine = useMemo(() => {
    const k: any = state?.kpis;
    const raw = typeof k?.trains_on_line === 'number' ? k.trains_on_line : (typeof k?.trainsOnLine === 'number' ? k.trainsOnLine : undefined);
    const fallback = state?.trains?.length ?? 0;
    return typeof raw === 'number' && Number.isFinite(raw) ? raw : fallback;
  }, [state]);

  const totalTrains = state?.trains?.length ?? 0;

  const blockedBlocks = useMemo(() => {
    return state?.blocks?.reduce((acc, b) => acc + (b.issue ? 1 : 0), 0) ?? 0;
  }, [state]);

  // Trend for average delay
  const prevAvgRef = useRef(avgDelayMin);
  const delta = useMemo(() => {
    const d = avgDelayMin - prevAvgRef.current;
    return Math.abs(d) < 0.05 ? 0 : d; // ignore tiny jitter
  }, [avgDelayMin]);

  useEffect(() => {
    prevAvgRef.current = avgDelayMin;
  }, [avgDelayMin]);

  const connectionColor = () => {
    if (cs.connected) return '#16a34a';      // green
    if (cs.reconnecting) return '#f59e0b';   // amber
    return '#dc2626';                        // red
  };

  const connectionText = () => {
    if (cs.connected) return 'Connected';
    if (cs.reconnecting) return 'Reconnecting…';
    return 'Disconnected';
  };

  const trendIcon = () => {
    if (delta > 0) return '▲';
    if (delta < 0) return '▼';
    return '—';
  };

  const trendClass = () => {
    if (delta > 0.05) return 'worse';
    if (delta < -0.05) return 'better';
    return 'neutral';
  };

  return (
    <div className="kpi-bar">
      <div className="kpi-section">
        <div className="kpi-item">
          <span className="kpi-label">Simulation Time</span>
          <span className="kpi-value" title={simTime || ''}>
            {formatClock(simTime)}
          </span>
          <span className="kpi-subtle">({timeAgo(simTime)})</span>
        </div>

        <div className="kpi-item">
          <span className="kpi-label">Avg Delay</span>
          <span className={`kpi-value ${avgDelayMin > 0 ? 'delay' : ''}`}>
            {avgDelayMin.toFixed(1)}m
          </span>
          <span className={`kpi-trend ${trendClass()}`} aria-label={`trend ${delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat'}`}>
            {trendIcon()}
          </span>
        </div>

        <div className="kpi-item">
          <span className="kpi-label">Trains Active</span>
          <span className="kpi-value">{trainsOnLine}</span>
        </div>

        <div className="kpi-item">
          <span className="kpi-label">Total Trains</span>
          <span className="kpi-value">{totalTrains}</span>
        </div>

        <div className="kpi-item">
          <span className="kpi-label">Blocked Blocks</span>
          <span className={`kpi-value ${blockedBlocks > 0 ? 'blocked' : ''}`}>{blockedBlocks}</span>
        </div>
      </div>

      <div className="connection-status" role="status" aria-live="polite" aria-atomic="true" title={cs.lastError || ''}>
        <span
          className="status-indicator"
          style={{ backgroundColor: connectionColor() }}
          aria-hidden
        />
        <span className="status-text">{connectionText()}</span>
        {!!cs.lastError && <span className="error-text">Error</span>}
      </div>
    </div>
  );
};
