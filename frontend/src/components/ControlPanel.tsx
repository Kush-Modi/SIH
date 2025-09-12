import React, { useCallback, useMemo, useState, useEffect } from 'react';
import { StateMessage, ControlPayload, DelayInjection, BlockIssueInjection } from '../types';
import './ControlPanel.css';
import { pickBackendBase } from '../ws/client'; // reuse backend base resolver

interface ControlPanelProps {
  state: StateMessage | null;
  onUpdateParameters: (params: ControlPayload) => Promise<void>;
  onInjectDelay: (injection: DelayInjection) => Promise<void>;
  onSetBlockIssue: (injection: BlockIssueInjection) => Promise<void>;
}

// Types for /rerun-optimized response (kept local to avoid breaking global types)
type RerunTrainRow = { train_id: string; name: string; delay_min: number };
type RerunMetrics = {
  avg_delay_min: number;
  trains_on_line: number;
  duration_sec: number;
  by_train: RerunTrainRow[];
  by_block: { block_id: string; occupancy_sec: number }[];
};
type RerunDiffTrain = { train_id: string; name: string; delta_delay_min: number };
type RerunDiff = {
  delta_avg_delay_min: number;
  delta_duration_sec: number;
  trains: RerunDiffTrain[];
  blocks: { block_id: string; delta_occupancy_sec: number }[];
};
type PlanIn = { holds: { train_id: string; block_id: string; not_before_offset_sec: number }[] };

// Enriched response: core fields + meta with trials and confidence intervals
type RerunResponseEnriched = {
  baseline: RerunMetrics;
  optimized: RerunMetrics;
  plan: PlanIn;
  diff: RerunDiff;
  meta?: {
    trials: number;
    seeds_used: number[];
    holds_applied: number;
    avg_delay_min_delta_mean: number;
    avg_delay_min_delta_ci95: [number, number];
    duration_sec_delta_mean: number;
    duration_sec_delta_ci95: [number, number];
  };
};

export const ControlPanel: React.FC<ControlPanelProps> = ({
  state,
  onUpdateParameters,
  onInjectDelay,
  onSetBlockIssue
}) => {
  // Backend base (shared with WS probing logic)
  const [apiBase, setApiBase] = useState<string>('http://localhost:8000');

  useEffect(() => {
    let mounted = true;
    pickBackendBase().then((b) => mounted && setApiBase(b));
    return () => { mounted = false; };
  }, []);

  // Parameters
  const [headwaySec, setHeadwaySec] = useState(120);
  const [dwellSec, setDwellSec] = useState(60);
  const [energyPenalty, setEnergyPenalty] = useState(0.0);
  const [simulationSpeed, setSimulationSpeed] = useState(1.0);

  // Actions
  const [selectedTrain, setSelectedTrain] = useState('');
  const [delayMinutes, setDelayMinutes] = useState(10);

  const [selectedBlock, setSelectedBlock] = useState('');
  const [blockAction, setBlockAction] = useState<'block' | 'clear'>('block');

  // Rerun controls
  const [trials, setTrials] = useState<number>(10);
  const [seed, setSeed] = useState<number>(42);

  // UI state
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [rerunResult, setRerunResult] = useState<RerunResponseEnriched | null>(null);

  const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

  const trains = useMemo(() => {
    return [...(state?.trains ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  }, [state?.trains]);

  const blocks = useMemo(() => {
    return [...(state?.blocks ?? [])].sort((a, b) => a.id.localeCompare(b.id));
  }, [state?.blocks]);

  const showMessage = useCallback((type: 'success' | 'error' | 'info', text: string) => {
    setMessage({ type, text });
    window.clearTimeout((showMessage as any)._t);
    (showMessage as any)._t = window.setTimeout(() => setMessage(null), 3000);
  }, []);

  const status = state?.status ?? 'IDLE';
  const canStart = status === 'IDLE';               // only start from IDLE
  const canReset = true;                            // allow reset anytime
  const canOptimize = status === 'COMPLETED';       // optimized A/B after completion

  // ---------------- Handlers ----------------

  const handleStart = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${apiBase.replace(/\/$/, '')}/start`, { method: 'POST' });
      if (!resp.ok) throw new Error('start failed');
      showMessage('success', 'Simulation started');
    } catch {
      showMessage('error', 'Failed to start simulation');
    } finally {
      setLoading(false);
    }
  }, [apiBase, showMessage]);

  const handleReset = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${apiBase.replace(/\/$/, '')}/reset`, { method: 'POST' });
      if (!resp.ok) throw new Error('reset failed');
      showMessage('success', 'Simulation reset to IDLE');
      setSelectedTrain('');
      setSelectedBlock('');
      setRerunResult(null);
    } catch {
      showMessage('error', 'Failed to reset simulation');
    } finally {
      setLoading(false);
    }
  }, [apiBase, showMessage]);

  const handleUpdateParameters = useCallback(async () => {
    setLoading(true);
    try {
      await onUpdateParameters({
        headway_sec: clamp(Number(headwaySec) || 0, 0, 600),
        dwell_sec: clamp(Number(dwellSec) || 0, 0, 600),
        energy_stop_penalty: clamp(Number(energyPenalty) || 0, 0, 100),
        simulation_speed: clamp(Number(simulationSpeed) || 1, 0.1, 10.0),
      });
      showMessage('success', 'Parameters updated successfully');
    } catch {
      showMessage('error', 'Failed to update parameters');
    } finally {
      setLoading(false);
    }
  }, [headwaySec, dwellSec, energyPenalty, simulationSpeed, onUpdateParameters, showMessage]);

  const handleInjectDelay = useCallback(async () => {
    if (!selectedTrain) {
      showMessage('error', 'Please select a train');
      return;
    }
    setLoading(true);
    try {
      const mins = clamp(Number(delayMinutes) || 1, 1, 60);
      await onInjectDelay({ train_id: selectedTrain, delay_minutes: mins });
      showMessage('success', `Injected ${mins} minute delay into ${selectedTrain}`);
    } catch {
      showMessage('error', 'Failed to inject delay');
    } finally {
      setLoading(false);
    }
  }, [selectedTrain, delayMinutes, onInjectDelay, showMessage]);

  const handleSetBlockIssue = useCallback(async () => {
    if (!selectedBlock) {
      showMessage('error', 'Please select a block');
      return;
    }
    setLoading(true);
    try {
      await onSetBlockIssue({ block_id: selectedBlock, blocked: blockAction === 'block' });
      showMessage('success', `${blockAction === 'block' ? 'Blocked' : 'Cleared'} block ${selectedBlock}`);
    } catch {
      showMessage('error', 'Failed to update block status');
    } finally {
      setLoading(false);
    }
  }, [selectedBlock, blockAction, onSetBlockIssue, showMessage]);

  // Presets
  const applyPresetDemo = useCallback(() => {
    setHeadwaySec(90);
    setDwellSec(45);
    setEnergyPenalty(0.5);
    setSimulationSpeed(4.0);
  }, []);

  const applyPresetDefault = useCallback(() => {
    setHeadwaySec(120);
    setDwellSec(60);
    setEnergyPenalty(0.0);
    setSimulationSpeed(1.0);
  }, []);

  // Batch optimize rerun with trials and seed
  const handleRerunOptimized = useCallback(async () => {
    setLoading(true);
    try {
      const base = apiBase.replace(/\/$/, '');
      const url = `${base}/rerun-optimized?seed=${encodeURIComponent(seed)}&trials=${encodeURIComponent(trials)}`;
      const resp = await fetch(url, { method: 'POST' });
      if (resp.status === 409) {
        showMessage('info', 'Available only after simulation completes');
        setRerunResult(null);
        return;
      }
      if (!resp.ok) throw new Error('rerun failed');
      const json: RerunResponseEnriched = await resp.json();
      setRerunResult(json);
      const trialsTxt = json.meta?.trials ? ` (${json.meta.trials} trials)` : '';
      showMessage('success', `Optimize & Rerun completed${trialsTxt}`);
    } catch {
      setRerunResult(null);
      showMessage('error', 'Optimization failed');
    } finally {
      setLoading(false);
    }
  }, [apiBase, showMessage, seed, trials]);

  // Results card helpers
  const holdsCount = rerunResult?.plan?.holds?.length ?? 0;
  const topTrains = (rerunResult?.diff?.trains ?? []).slice(0, 3);
  const ciAvg = rerunResult?.meta?.avg_delay_min_delta_ci95;
  const ciDur = rerunResult?.meta?.duration_sec_delta_ci95;
  const meanAvg = rerunResult?.meta?.avg_delay_min_delta_mean;
  const meanDur = rerunResult?.meta?.duration_sec_delta_mean;

  return (
    <div className="control-panel">
      <h3 className="cp-title">Simulation Controls</h3>

      {message && (
        <div className={`message ${message.type}`} role="status" aria-live="polite">
          {message.text}
        </div>
      )}

      <fieldset className="control-section" disabled={loading}>
        <legend>Basics</legend>
        <div className="control-grid">
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={handleStart} disabled={loading || !canStart} className="btn primary">
              Start Simulation
            </button>
            <button onClick={handleReset} disabled={loading || !canReset} className="btn reset-button">
              Reset Simulation
            </button>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={applyPresetDemo} disabled={loading} className="btn success">Demo Preset</button>
            <button onClick={applyPresetDefault} disabled={loading} className="btn">Defaults</button>
          </div>
          <div className="status-chip" data-status={status} aria-live="polite">Status: {status}</div>
        </div>
      </fieldset>

      <fieldset className="control-section" disabled={loading}>
        <legend>Parameters</legend>
        <div className="control-grid">
          <div className="control-group">
            <label className="control-label">Headway (seconds)</label>
            <input
              type="number"
              inputMode="numeric"
              value={headwaySec}
              onChange={(e) => setHeadwaySec(Number(e.target.value))}
              onBlur={(e) => setHeadwaySec(clamp(Number(e.target.value) || 0, 0, 600))}
              min={0}
              max={600}
              aria-describedby="headwayHelp"
            />
            <small id="headwayHelp" className="help">Minimum spacing between trains entering a block.</small>
          </div>

          <div className="control-group">
            <label className="control-label">Dwell Time (seconds)</label>
            <input
              type="number"
              inputMode="numeric"
              value={dwellSec}
              onChange={(e) => setDwellSec(Number(e.target.value))}
              onBlur={(e) => setDwellSec(clamp(Number(e.target.value) || 0, 0, 600))}
              min={0}
              max={600}
              aria-describedby="dwellHelp"
            />
            <small id="dwellHelp" className="help">Station stop duration at platforms.</small>
          </div>

          <div className="control-group">
            <label className="control-label">Energy Stop Penalty</label>
            <input
              type="number"
              inputMode="decimal"
              value={energyPenalty}
              onChange={(e) => setEnergyPenalty(Number(e.target.value))}
              onBlur={(e) => setEnergyPenalty(clamp(Number(e.target.value) || 0, 0, 100))}
              min={0}
              max={100}
              step={0.1}
              aria-describedby="energyHelp"
            />
            <small id="energyHelp" className="help">Low values discourage unnecessary stops.</small>
          </div>

          <div className="control-group">
            <label className="control-label">Simulation Speed</label>
            <input
              type="number"
              inputMode="decimal"
              value={simulationSpeed}
              onChange={(e) => setSimulationSpeed(Number(e.target.value))}
              onBlur={(e) => setSimulationSpeed(clamp(Number(e.target.value) || 1, 0.1, 10.0))}
              min={0.1}
              max={10.0}
              step={0.1}
              aria-describedby="speedHelp"
            />
            <small id="speedHelp" className="help">Multiplies simulation clock for demos.</small>
          </div>
        </div>

        <button onClick={handleUpdateParameters} disabled={loading} className="btn primary">
          Update Parameters
        </button>
      </fieldset>

      <fieldset className="control-section" disabled={loading}>
        <legend>Delay Injection</legend>
        <div className="control-grid">
          <div className="control-group">
            <label className="control-label">Select Train</label>
            <select
              value={selectedTrain}
              onChange={(e) => setSelectedTrain(e.target.value)}
            >
              <option value="">Choose a train…</option>
              {trains.map(train => (
                <option key={train.id} value={train.id}>
                  {train.name} ({train.priority})
                </option>
              ))}
            </select>
          </div>

          <div className="control-group">
            <label className="control-label">Delay (minutes)</label>
            <input
              type="number"
              inputMode="numeric"
              value={delayMinutes}
              onChange={(e) => setDelayMinutes(Number(e.target.value))}
              onBlur={(e) => setDelayMinutes(clamp(Number(e.target.value) || 1, 1, 60))}
              min={1}
              max={60}
            />
          </div>
        </div>

        <button
          onClick={handleInjectDelay}
          disabled={loading || !selectedTrain}
          className="btn warn"
        >
          Inject Delay
        </button>
      </fieldset>

      <fieldset className="control-section" disabled={loading}>
        <legend>Block Issues</legend>
        <div className="control-grid">
          <div className="control-group">
            <label className="control-label">Select Block</label>
            <select
              value={selectedBlock}
              onChange={(e) => setSelectedBlock(e.target.value)}
            >
              <option value="">Choose a block…</option>
              {blocks.map(block => (
                <option key={block.id} value={block.id}>
                  {block.id} {state?.blocks.find(b => b.id === block.id)?.issue ? '(BLOCKED)' : ''}
                </option>
              ))}
            </select>
          </div>

          <div className="control-group">
            <label className="control-label">Action</label>
            <select
              value={blockAction}
              onChange={(e) => setBlockAction(e.target.value as 'block' | 'clear')}
            >
              <option value="block">Block</option>
              <option value="clear">Clear</option>
            </select>
          </div>
        </div>

        <button
          onClick={handleSetBlockIssue}
          disabled={loading || !selectedBlock}
          className={`btn ${blockAction === 'block' ? 'danger' : 'success'}`}
        >
          {blockAction === 'block' ? 'Block' : 'Clear'} Block
        </button>
      </fieldset>

      <fieldset className="control-section" disabled={loading}>
        <legend>Batch Optimization</legend>
        <div className="control-grid">
          <div className="control-group">
            <label className="control-label">Trials</label>
            <select value={trials} onChange={(e) => setTrials(Number(e.target.value))}>
              {[1, 5, 10, 20].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <div className="control-group">
            <label className="control-label">Seed</label>
            <input
              type="number"
              inputMode="numeric"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
            />
          </div>
          <button
            onClick={handleRerunOptimized}
            disabled={loading || !canOptimize}
            className="btn primary"
            title={canOptimize ? 'Run optimizer and show A/B diff' : 'Available after completion'}
          >
            Optimize & Rerun
          </button>
        </div>

        {rerunResult && (
          <div className="results-card" role="status" aria-live="polite">
            <div className="results-row">
              <span className="results-label">Avg delay</span>
              <span className="results-value">
                {rerunResult.baseline.avg_delay_min}m → {rerunResult.optimized.avg_delay_min}m{' '}
                (<strong>Δ {rerunResult.diff.delta_avg_delay_min.toFixed(2)}m</strong>)
              </span>
            </div>
            {meanAvg !== undefined && ciAvg && (
              <div className="results-row">
                <span className="results-label">Avg Δ (mean, 95% CI)</span>
                <span className="results-value">
                  {meanAvg.toFixed(2)}m [{ciAvg[0].toFixed(2)}, {ciAvg[1].toFixed(2)}]
                </span>
              </div>
            )}
            <div className="results-row">
              <span className="results-label">Duration</span>
              <span className="results-value">
                {rerunResult.baseline.duration_sec}s → {rerunResult.optimized.duration_sec}s{' '}
                (<strong>Δ {Number(rerunResult.diff.delta_duration_sec).toFixed(0)}s</strong>)
              </span>
            </div>
            {meanDur !== undefined && ciDur && (
              <div className="results-row">
                <span className="results-label">Duration Δ (mean, 95% CI)</span>
                <span className="results-value">
                  {meanDur.toFixed(0)}s [{Number(ciDur[0]).toFixed(0)}, {Number(ciDur[1]).toFixed(0)}]
                </span>
              </div>
            )}
            <div className="results-row">
              <span className="results-label">Holds applied</span>
              <span className="results-value">{holdsCount} {rerunResult.meta?.holds_applied !== undefined ? `(optimizer: ${rerunResult.meta.holds_applied})` : ''}</span>
            </div>
            {topTrains.length > 0 && (
              <div className="results-sub">
                <div className="results-subtitle">Top trains improved</div>
                <ul className="results-list">
                  {topTrains.map(t => (
                    <li key={t.train_id}>
                      {t.name}: +{t.delta_delay_min.toFixed(1)}m
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </fieldset>
    </div>
  );
};
