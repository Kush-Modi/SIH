import React, { useEffect, useMemo, useState } from 'react';
import './SchedulePage.css';
import { pickBackendBase } from '../ws/client';
import { RerunResponseEnriched } from '../types';

interface ScheduleBlock {
  block_index: number;
  block_id: string;
  block_name: string;
  start_time_sec: number;
  end_time_sec: number;
  duration_sec: number;
  start_time_formatted: string;
  end_time_formatted: string;
}

interface TrainSchedule {
  train_id: string;
  schedule: ScheduleBlock[];
}

interface OptimizationParams {
  max_trains: number;
  max_time_sec: number;
  headway_sec: number;
  time_limit_sec: number;
}

interface ScheduleData {
  optimization_params: OptimizationParams;
  trains: TrainSchedule[];
}

const SchedulePage: React.FC = () => {
  const [apiBase, setApiBase] = useState<string>('http://localhost:8000');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Optional classic schedule payload if you later expose it
  const [scheduleData, setScheduleData] = useState<ScheduleData | null>(null);

  // A/B optimize & rerun payload
  const [rerun, setRerun] = useState<RerunResponseEnriched | null>(null);

  // Controls
  const [trials, setTrials] = useState<number>(10);
  const [seed, setSeed] = useState<number>(42);

  useEffect(() => {
    let mounted = true;
    pickBackendBase().then((b) => mounted && setApiBase(b));
    return () => { mounted = false; };
  }, []);

  const formatDuration = (seconds: number): string => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return `${h}h ${m}m ${s}s`;
  };

  const getTotalOptimizationTime = (data: ScheduleData | null): string => {
    if (!data?.trains?.length) return '0h 0m 0s';
    let maxEndTime = 0;
    data.trains.forEach(train => {
      train.schedule.forEach(block => {
        if (block.end_time_sec > maxEndTime) maxEndTime = block.end_time_sec;
      });
    });
    return formatDuration(maxEndTime);
  };

  // Optional: classic schedule fetcher if/when backend adds it again
  const fetchScheduleInfo = async () => {
    try {
      const resp = await fetch(`${apiBase.replace(/\/$/, '')}/schedule-info`);
      if (!resp.ok) return;
      const result = await resp.json();
      if (result.status === 'success') {
        // adapt to your returned shape if needed
      }
    } catch {
      // ignore soft
    }
  };

  useEffect(() => {
    fetchScheduleInfo();
  }, [apiBase]);

  // Optimize & rerun using the new backend endpoint
  const runOptimizeAndRerun = async () => {
    setLoading(true);
    setError(null);
    try {
      const url = `${apiBase.replace(/\/$/, '')}/rerun-optimized?seed=${encodeURIComponent(seed)}&trials=${encodeURIComponent(trials)}`;
      const resp = await fetch(url, { method: 'POST' });
      if (resp.status === 409) {
        setError('Rerun available only after live simulation completes');
        setRerun(null);
        return;
      }
      if (!resp.ok) throw new Error('rerun failed');
      const json: RerunResponseEnriched = await resp.json();
      setRerun(json);
    } catch (e) {
      setRerun(null);
      setError('Failed to run optimizer');
    } finally {
      setLoading(false);
    }
  };

  // Derived summaries from rerun
  const baselineAvg = rerun?.baseline?.avg_delay_min ?? null;
  const optimizedAvg = rerun?.optimized?.avg_delay_min ?? null;
  const deltaAvg = rerun?.diff?.delta_avg_delay_min ?? null;
  const baselineDur = rerun?.baseline?.duration_sec ?? null;
  const optimizedDur = rerun?.optimized?.duration_sec ?? null;
  const deltaDur = rerun?.diff?.delta_duration_sec ?? null;
  const ciAvg = rerun?.meta?.avg_delay_min_delta_ci95 ?? null;
  const ciDur = rerun?.meta?.duration_sec_delta_ci95 ?? null;
  const holdsApplied = rerun?.plan?.holds?.length ?? 0;

  // If later you return per-train block schedules from backend, put them in scheduleData and they’ll render below
  const hasTimeline = Boolean(scheduleData?.trains?.length);

  return (
    <div className="schedule-page">
      <div className="schedule-header">
        <h1>Railway Schedule Optimizer</h1>
        <p>Optimize and compare schedules using constraint programming and paired A/B reruns</p>
      </div>

      <div className="optimization-controls">
        <h3>Optimize & Rerun</h3>
        <div className="controls-grid">
          <div className="control-group">
            <label htmlFor="trials">Trials</label>
            <select
              id="trials"
              value={trials}
              onChange={(e) => setTrials(parseInt(e.target.value) || 10)}
            >
              {[1, 5, 10, 20].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <div className="control-group">
            <label htmlFor="seed">Seed</label>
            <input
              id="seed"
              type="number"
              inputMode="numeric"
              value={seed}
              onChange={(e) => setSeed(parseInt(e.target.value) || 42)}
            />
          </div>
        </div>

        <button
          className="optimize-button"
          onClick={runOptimizeAndRerun}
          disabled={loading}
          title="Runs paired baseline vs optimized with common random numbers"
        >
          {loading ? 'Running…' : 'Optimize & Rerun'}
        </button>
      </div>

      {error && (
        <div className="error-message">
          <h4>Error</h4>
          <p>{error}</p>
        </div>
      )}

      {rerun && (
        <div className="schedule-results">
          <div className="results-header">
            <h3>Results</h3>
            <div className="results-summary">
              <div className="summary-item">
                <span className="summary-label">Avg delay</span>
                <span className="summary-value">
                  {baselineAvg}m → {optimizedAvg}m ({deltaAvg !== null ? `Δ ${deltaAvg.toFixed(2)}m` : '—'})
                </span>
              </div>
              {ciAvg && (
                <div className="summary-item">
                  <span className="summary-label">Avg Δ 95% CI</span>
                  <span className="summary-value">
                    [{ciAvg.toFixed(2)}, {ciAvg[22].toFixed(2)}] m
                  </span>
                </div>
              )}
              <div className="summary-item">
                <span className="summary-label">Duration</span>
                <span className="summary-value">
                  {baselineDur}s → {optimizedDur}s ({deltaDur !== null ? `Δ ${Number(deltaDur).toFixed(0)}s` : '—'})
                </span>
              </div>
              {ciDur && (
                <div className="summary-item">
                  <span className="summary-label">Duration Δ 95% CI</span>
                  <span className="summary-value">
                    [{Number(ciDur).toFixed(0)}, {Number(ciDur[22]).toFixed(0)}] s
                  </span>
                </div>
              )}
              <div className="summary-item">
                <span className="summary-label">Holds applied</span>
                <span className="summary-value">{holdsApplied}</span>
              </div>
            </div>
          </div>

          {rerun.diff?.trains?.length ? (
            <div className="trains-list">
              <h4>Top trains improved</h4>
              <ul>
                {rerun.diff.trains.slice(0, 5).map(t => (
                  <li key={t.train_id}>
                    {t.name}: +{t.delta_delay_min.toFixed(2)}m
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}

      {hasTimeline && scheduleData && (
        <div className="schedule-results">
          <div className="results-header">
            <h3>Optimized Schedule Timeline</h3>
            <div className="results-summary">
              <div className="summary-item">
                <span className="summary-label">Trains Optimized:</span>
                <span className="summary-value">{scheduleData.trains.length}</span>
              </div>
              <div className="summary-item">
                <span className="summary-label">Total Schedule Time:</span>
                <span className="summary-value">{getTotalOptimizationTime(scheduleData)}</span>
              </div>
              <div className="summary-item">
                <span className="summary-label">Headway:</span>
                <span className="summary-value">{scheduleData.optimization_params.headway_sec}s</span>
              </div>
            </div>
          </div>

          <div className="trains-list">
            {scheduleData.trains.map((train) => (
              <div key={train.train_id} className="train-schedule">
                <div className="train-header">
                  <h4>Train {train.train_id}</h4>
                  <span className="train-stats">
                    {train.schedule.length} blocks •
                    {formatDuration(train.schedule[train.schedule.length - 1]?.end_time_sec || 0)} total time
                  </span>
                </div>

                <div className="schedule-timeline">
                  {train.schedule.map((block, index) => (
                    <div key={index} className="schedule-block">
                      <div className="block-info">
                        <div className="block-name">{block.block_name}</div>
                        <div className="block-id">ID: {block.block_id}</div>
                      </div>
                      <div className="block-timing">
                        <div className="time-entry">
                          <span className="time-label">Start:</span>
                          <span className="time-value">{block.start_time_formatted}</span>
                        </div>
                        <div className="time-entry">
                          <span className="time-label">End:</span>
                          <span className="time-value">{block.end_time_formatted}</span>
                        </div>
                        <div className="time-entry">
                          <span className="time-label">Duration:</span>
                          <span className="time-value">{formatDuration(block.duration_sec)}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default SchedulePage;
