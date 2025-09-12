import React, { useState, useEffect } from 'react';
import './SchedulePage.css';

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

interface ScheduleInfo {
  total_trains: number;
  total_blocks: number;
  avg_delay_min: number;
  trains_on_line: number;
  sim_time: string;
  status: string;
  can_optimize: boolean;
}


const SchedulePage: React.FC = () => {
  const [scheduleData, setScheduleData] = useState<ScheduleData | null>(null);
  const [scheduleInfo, setScheduleInfo] = useState<ScheduleInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [optimizationParams, setOptimizationParams] = useState({
    max_trains: 20,
    max_time_sec: 3600,
    headway_sec: 90,
    time_limit_sec: 1.5
  });

  useEffect(() => {
    fetchScheduleInfo();
  }, []);

  const fetchScheduleInfo = async () => {
    try {
      const response = await fetch('http://localhost:8000/schedule-info');
      const result = await response.json();
      if (result.status === 'success') {
        setScheduleInfo(result.data);
      } else {
        console.error('Schedule info error:', result.error);
      }
    } catch (err) {
      console.error('Failed to fetch schedule info:', err);
    }
  };

  const optimizeSchedule = async () => {
    setLoading(true);
    setError(null);

    try {
      const params = new URLSearchParams({
        max_trains: optimizationParams.max_trains.toString(),
        max_time_sec: optimizationParams.max_time_sec.toString(),
        headway_sec: optimizationParams.headway_sec.toString(),
        time_limit_sec: optimizationParams.time_limit_sec.toString(),
      });

      const response = await fetch(`http://localhost:8000/optimize-schedule?${params}`, {
        method: 'POST'
      });

      const result = await response.json();

      if (result.status === 'success') {
        setScheduleData(result.data);
      } else {
        setError(result.error || 'Optimization failed');
      }
    } catch (err) {
      setError('Failed to optimize schedule');
      console.error('Optimization error:', err);
    } finally {
      setLoading(false);
    }
  };


  const formatDuration = (seconds: number): string => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    return `${hours}h ${minutes}m ${secs}s`;
  };

  const getTotalOptimizationTime = (): string => {
    if (!scheduleData?.trains.length) return '0h 0m 0s';

    let maxEndTime = 0;
    scheduleData.trains.forEach(train => {
      train.schedule.forEach(block => {
        if (block.end_time_sec > maxEndTime) {
          maxEndTime = block.end_time_sec;
        }
      });
    });

    return formatDuration(maxEndTime);
  };

  return (
    <div className="schedule-page">
      <div className="schedule-header">
        <h1>Railway Schedule Optimizer</h1>
        <p>Optimize train schedules using real railway data and constraint programming</p>
      </div>

      {scheduleInfo && (
        <div className="schedule-info">
          <h3>Available Data</h3>
          <div className="info-grid">
            <div className="info-item">
              <span className="info-label">Total Trains:</span>
              <span className="info-value">{scheduleInfo.total_trains.toLocaleString()}</span>
            </div>
            <div className="info-item">
              <span className="info-label">Total Blocks:</span>
              <span className="info-value">{scheduleInfo.total_blocks.toLocaleString()}</span>
            </div>
          </div>
        </div>
      )}

      <div className="optimization-controls">
        <h3>Optimization Parameters</h3>
        <div className="controls-grid">
          <div className="control-group">
            <label htmlFor="max_trains">Max Trains to Optimize:</label>
            <input
              id="max_trains"
              type="number"
              min="1"
              max="100"
              value={optimizationParams.max_trains}
              onChange={(e) => setOptimizationParams(prev => ({
                ...prev,
                max_trains: parseInt(e.target.value) || 20
              }))}
            />
          </div>

          <div className="control-group">
            <label htmlFor="max_time_sec">Max Time Window (seconds):</label>
            <input
              id="max_time_sec"
              type="number"
              min="300"
              max="7200"
              value={optimizationParams.max_time_sec}
              onChange={(e) => setOptimizationParams(prev => ({
                ...prev,
                max_time_sec: parseInt(e.target.value) || 3600
              }))}
            />
          </div>

          <div className="control-group">
            <label htmlFor="headway_sec">Headway (seconds):</label>
            <input
              id="headway_sec"
              type="number"
              min="30"
              max="300"
              value={optimizationParams.headway_sec}
              onChange={(e) => setOptimizationParams(prev => ({
                ...prev,
                headway_sec: parseInt(e.target.value) || 90
              }))}
            />
          </div>

          <div className="control-group">
            <label htmlFor="time_limit_sec">Solver Time Limit (seconds):</label>
            <input
              id="time_limit_sec"
              type="number"
              min="0.5"
              max="10"
              step="0.5"
              value={optimizationParams.time_limit_sec}
              onChange={(e) => setOptimizationParams(prev => ({
                ...prev,
                time_limit_sec: parseFloat(e.target.value) || 1.5
              }))}
            />
          </div>
        </div>

        <button
          className="optimize-button"
          onClick={optimizeSchedule}
          disabled={loading}
        >
          {loading ? 'Optimizing...' : 'Optimize Schedule'}
        </button>
      </div>

      {error && (
        <div className="error-message">
          <h4>Error</h4>
          <p>{error}</p>
        </div>
      )}

      {scheduleData && (
        <div className="schedule-results">
          <div className="results-header">
            <h3>Optimized Schedule Results</h3>
            <div className="results-summary">
              <div className="summary-item">
                <span className="summary-label">Trains Optimized:</span>
                <span className="summary-value">{scheduleData.trains.length}</span>
              </div>
              <div className="summary-item">
                <span className="summary-label">Total Schedule Time:</span>
                <span className="summary-value">{getTotalOptimizationTime()}</span>
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
