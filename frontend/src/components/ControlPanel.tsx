import React, { useCallback, useMemo, useState } from 'react';
import { StateMessage, ControlPayload, DelayInjection, BlockIssueInjection } from '../types';
import './ControlPanel.css';

interface ControlPanelProps {
  state: StateMessage | null;
  onUpdateParameters: (params: ControlPayload) => Promise<void>;
  onInjectDelay: (injection: DelayInjection) => Promise<void>;
  onSetBlockIssue: (injection: BlockIssueInjection) => Promise<void>;
}

export const ControlPanel: React.FC<ControlPanelProps> = ({
  state,
  onUpdateParameters,
  onInjectDelay,
  onSetBlockIssue
}) => {
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

  // UI state
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);

  // Helpers
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

  // Handlers (stable)
  const handleUpdateParameters = useCallback(async () => {
    setLoading(true);
    try {
      await onUpdateParameters({
        headway_sec: clamp(Number(headwaySec) || 0, 0, 600),
        dwell_sec: clamp(Number(dwellSec) || 0, 0, 600),
        energy_stop_penalty: clamp(Number(energyPenalty) || 0, 0, 100),
        simulation_speed: clamp(Number(simulationSpeed) || 1, 0.1, 10.0)
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

  const handleReset = useCallback(async () => {
    setLoading(true);
    try {
      const API_BASE = (import.meta as any).env.VITE_API_URL || 'http://localhost:8000';
      const resp = await fetch(`${API_BASE}/reset`, { method: 'POST' });
      if (!resp.ok) throw new Error('reset failed');
      showMessage('success', 'Simulation restarted');
      // Optional: clear quick selections
      setSelectedTrain('');
      setSelectedBlock('');
    } catch {
      showMessage('error', 'Failed to restart simulation');
    } finally {
      setLoading(false);
    }
  }, [showMessage]);

  // Demo presets (optional, handy for hackathons)
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
          <button onClick={handleReset} disabled={loading} className="btn reset-button">Restart Simulation</button>
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={applyPresetDemo} disabled={loading} className="btn success">Demo Preset</button>
            <button onClick={applyPresetDefault} disabled={loading} className="btn">Defaults</button>
          </div>
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
    </div>
  );
};
