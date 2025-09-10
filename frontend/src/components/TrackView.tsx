import React, { useMemo, useState, useEffect, useRef } from 'react';
import { StateMessage, TrainState, BlockState, TrainPriority } from '../types';
import './TrackView.css';

interface TrackViewProps { state: StateMessage | null; }

// Stations (schematic)
const STATIONS = [
  { id: 'S1', name: 'Central Station', x: 120, y: 400 },
  { id: 'S2', name: 'North Junction', x: 400, y: 280 },
  { id: 'S3', name: 'Industrial Zone', x: 700, y: 200 },
  { id: 'S4', name: 'South Junction', x: 880, y: 280 },
  { id: 'S5', name: 'Port Terminal', x: 880, y: 450 },
  { id: 'S6', name: 'Maintenance Depot', x: 400, y: 450 }
];

type BlockGeom = {
  id: string;
  name: string;
  startX: number; startY: number;
  endX: number; endY: number;
  station_id: string | null;
  controlX?: number; controlY?: number;
  kind?: 'MAIN' | 'LOOP' | 'SIDING';
};

// Schematic blocks
const BLOCKS: BlockGeom[] = [
  { id: 'B1', name: 'Central-North',      startX: 120, startY: 400, endX: 400, endY: 280, station_id: null,            kind: 'MAIN'  },
  { id: 'B2', name: 'North Junction',     startX: 400, startY: 280, endX: 400, endY: 280, station_id: 'S2',            kind: 'MAIN'  },
  { id: 'B3', name: 'North-Industrial',   startX: 400, startY: 280, endX: 700, endY: 200, station_id: null,            kind: 'MAIN'  },
  { id: 'B4', name: 'Industrial Zone',    startX: 700, startY: 200, endX: 700, endY: 200, station_id: 'S3',            kind: 'MAIN'  },
  { id: 'B5', name: 'Industrial-South',   startX: 700, startY: 200, endX: 880, endY: 280, station_id: null,            kind: 'MAIN'  },
  { id: 'B6', name: 'South Junction',     startX: 880, startY: 280, endX: 880, endY: 280, station_id: 'S4',            kind: 'MAIN'  },
  { id: 'B7', name: 'South-Central',      startX: 880, startY: 280, endX: 120, endY: 400, station_id: null, controlX: 500, controlY: 500, kind: 'MAIN'  },
  { id: 'B8', name: 'North Loop',         startX: 400, startY: 280, endX: 400, endY: 120, station_id: null,            kind: 'LOOP'  },
  { id: 'B9', name: 'Loop Connection',    startX: 400, startY: 120, endX: 880, endY: 280, station_id: null, controlX: 640, controlY: 150, kind: 'LOOP'  },
  { id: 'B10', name: 'Port Siding',       startX: 880, startY: 280, endX: 880, endY: 450, station_id: 'S5',            kind: 'SIDING'},
  { id: 'B11', name: 'Depot Siding',      startX: 400, startY: 280, endX: 400, endY: 450, station_id: 'S6',            kind: 'SIDING'}
];

const getTrainColor = (p: TrainPriority): string =>
  p === 'EXPRESS' ? '#e74c3c' : p === 'REGIONAL' ? '#3498db' : p === 'FREIGHT' ? '#f39c12' : '#95a5a6';

const getBlockStatus = (b?: BlockState) => ({ occupied: !!b?.occupied_by, issue: !!b?.issue });
const getBlockById = (id: string) => BLOCKS.find(b => b.id === id);

export const TrackView: React.FC<TrackViewProps> = ({ state }) => {
  const [showLoops, setShowLoops] = useState(true);
  const [showSidings, setShowSidings] = useState(true);
  const [showBlockLabels, setShowBlockLabels] = useState(false);

  // Fast map for block state lookups
  const blockStateMap = useMemo(() => {
    const m: Record<string, BlockState> = {};
    state?.blocks.forEach(b => { m[b.id] = b; });
    return m;
  }, [state?.blocks]);

  // SVG path refs + cached lengths
  const pathRefs = useRef<Record<string, SVGPathElement | null>>({});
  const pathLengths = useRef<Record<string, number>>({});

  // Client–simulation clock offset (align animations to sim_time)
  const [clockOffsetMs, setClockOffsetMs] = useState(0);
  useEffect(() => {
    if (state?.sim_time) {
      setClockOffsetMs(Date.now() - Date.parse(state.sim_time));
    }
  }, [state?.sim_time]);

  // rAF ticker (scoped here; parent unaffected)
  const [, setFrameTime] = useState(0);
  useEffect(() => {
    let raf = 0;
    const tick = (t: number) => { setFrameTime(t); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []); // requestAnimationFrame gives 60FPS, synced to repaint for smoothness [10]

  // Compute path lengths only when visibility toggles alter DOM
  useEffect(() => {
    BLOCKS.forEach(b => {
      const el = pathRefs.current[b.id];
      if (!el) return;
      try { pathLengths.current[b.id] = (el as any).getTotalLength?.() ?? 0; } catch { /* ignore */ }
    });
  }, [showLoops, showSidings]); // getTotalLength gives accurate path length for sampling [4]

  // Robust curve sampling: use length-space epsilon for heading stability
  const sampleOnPath = (block: BlockGeom, t: number) => {
    const el = pathRefs.current[block.id];
    const L = pathLengths.current[block.id] || 0;
    const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
    if (el && L > 0) {
      const tt = clamp01(t);
      const d = tt * L;
      // Small delta in length units to compute tangent reliably on both long/short paths
      const delta = Math.max(1, L * 0.02);
      const d2 = clamp01(tt + (delta / L)) * L;
      const p = (el as any).getPointAtLength(d);
      const p2 = (el as any).getPointAtLength(d2);
      const angleDeg = Math.atan2(p2.y - p.y, p2.x - p.x) * (180 / Math.PI);
      return { x: p.x, y: p.y, angleDeg };
    }
    // Fallback for straight segments if path ref not ready
    const x = block.startX + (block.endX - block.startX) * t;
    const y = block.startY + (block.endY - block.startY) * t;
    const angleDeg = Math.atan2(block.endY - block.startY, block.endX - block.startX) * (180 / Math.PI);
    return { x, y, angleDeg };
  }; // getPointAtLength returns a DOMPoint along the path, ideal for precise marker placement [1]

  // Animated positions every frame from timing fields
  const trainPositions = useMemo(() => {
    const positions = new Map<string, { x: number; y: number; angleDeg: number; train: TrainState }>();
    if (!state) return positions;

    const simNowMs = Date.now() - clockOffsetMs;

    state.trains.forEach(train => {
      const block = getBlockById(train.at_block);
      if (!block) return;

      // Dwell: pin near the start of platform for clarity
      const dwell = train.dwell_sec_remaining ?? 0;
      if (dwell > 0) {
        const pos = sampleOnPath(block, 0.05);
        positions.set(train.id, { ...pos, train });
        return;
      }

      // Interpolate progress t from entered/will_exit timestamps
      const enter = train.entered_block_at ? Date.parse(train.entered_block_at) : simNowMs - 1500;
      const exit  = train.will_exit_at     ? Date.parse(train.will_exit_at)     : simNowMs + 1500;
      const span  = Math.max(1, exit - enter);
      const u     = (simNowMs - enter) / span;

      // Smoothstep to reduce visible start/stop jerkiness on block changes
      const smooth = (s: number) => s * s * (3 - 2 * s);
      const t = Math.max(0, Math.min(1, smooth(u)));

      const pos = sampleOnPath(block, t);
      positions.set(train.id, { ...pos, train });
    });

    return positions;
  }, [state, clockOffsetMs]); // rAF drives visual updates; this memo recomputes with new frames & state [10]

  const shouldRenderBlock = (b: BlockGeom) =>
    b.kind === 'MAIN' ? true : b.kind === 'LOOP' ? showLoops : b.kind === 'SIDING' ? showSidings : true;

  return (
    <div className="track-view">
      <div className="view-controls">
        <div className="control-group">
          <span className="control-title">View Options</span>
          <label className="checkbox-label">
            <input type="checkbox" checked={showLoops} onChange={(e) => setShowLoops(e.target.checked)} />
            Show loops
          </label>
          <label className="checkbox-label">
            <input type="checkbox" checked={showSidings} onChange={(e) => setShowSidings(e.target.checked)} />
            Show sidings
          </label>
          <label className="checkbox-label">
            <input type="checkbox" checked={showBlockLabels} onChange={(e) => setShowBlockLabels(e.target.checked)} />
            Show block labels
          </label>
        </div>
      </div>

      <svg width="1000" height="600" viewBox="0 0 1000 600" className="track-svg">
        <defs>
          <pattern id="grid" width="50" height="50" patternUnits="userSpaceOnUse">
            <path d="M 50 0 L 0 0 0 50" fill="none" stroke="#ecf0f1" strokeWidth="1" opacity="0.3" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />

        {BLOCKS.filter(shouldRenderBlock).map(block => {
          const bs = blockStateMap[block.id];
          const status = getBlockStatus(bs);
          const hasCurve = block.controlX !== undefined && block.controlY !== undefined;
          const basePath = hasCurve
            ? `M ${block.startX},${block.startY} Q ${block.controlX},${block.controlY} ${block.endX},${block.endY}`
            : `M ${block.startX},${block.startY} L ${block.endX},${block.endY}`;
          const blockClass = `block-${block.kind?.toLowerCase() || 'main'}`;
          return (
            <g key={block.id} className="block-group">
              <path
                ref={(el) => { pathRefs.current[block.id] = el; }}
                d={basePath}
                className={`block-base ${blockClass}`}
                strokeDasharray={block.kind === 'SIDING' ? '8,4' : 'none'}
              />
              {status.occupied && <path d={basePath} className={`block-occupied ${blockClass}`} />}
              {status.issue    && <path d={basePath} className={`block-issue ${blockClass}`} />}
              {showBlockLabels && (
                <text
                  x={(block.startX + block.endX) / 2}
                  y={(block.startY + block.endY) / 2 - 20}
                  textAnchor="middle"
                  className="block-label"
                >
                  {block.name}
                </text>
              )}
            </g>
          );
        })}

        {STATIONS.map(s => (
          <g key={s.id} className="station-group">
            <circle cx={s.x} cy={s.y} r="14" className="station-node" />
            <text x={s.x} y={s.y + 32} textAnchor="middle" className="station-label">{s.name}</text>
          </g>
        ))}

        {Array.from(trainPositions.entries()).map(([trainId, { x, y, angleDeg, train }]) => {
          const label = train.name;
          const labelWidth = Math.max(60, label.length * 7 + 16);
          const labelHeight = 18;
          const labelX = -labelWidth / 2;
          const labelY = 24;
          return (
            <g key={trainId} className="train-group" transform={`translate(${x} ${y})`}>
              <circle r="14" className="train-halo" />
              <circle r="9" className="train-marker" fill={getTrainColor(train.priority)} />
              <polygon points="0,-7 12,0 0,7" className="direction-arrow" transform={`translate(16 0) rotate(${angleDeg})`} />
              <g className="train-label-group" transform={`translate(0 ${labelY})`}>
                <rect x={labelX} y={-labelHeight + 4} width={labelWidth} height={labelHeight} className="train-label-bg" />
                <text x={0} y={-2} textAnchor="middle" className="train-label">{label}</text>
              </g>
              {train.delay_min > 0 && (
                <g className="delay-indicator" transform="translate(20 -18)">
                  <rect width={30} height={14} className="delay-badge" />
                  <text x={15} y={11} textAnchor="middle" className="delay-text">+{train.delay_min}m</text>
                </g>
              )}
            </g>
          );
        })}

        <g className="legend" transform="translate(20, 20)">
          <rect width="180" height="100" fill="rgba(255,255,255,0.95)" stroke="#bdc3c7" rx="8" />
          <text x={10} y={20} className="legend-title">Train Types</text>
          <g transform="translate(10, 35)"><circle r="6" fill="#e74c3c" /><text x={15} y={5} className="legend-text">Express</text></g>
          <g transform="translate(10, 55)"><circle r="6" fill="#3498db" /><text x={15} y={5} className="legend-text">Regional</text></g>
          <g transform="translate(10, 75)"><circle r="6" fill="#f39c12" /><text x={15} y={5} className="legend-text">Freight</text></g>
          <g transform="translate(90, 35)"><rect width="20" height="4" fill="#2ecc71" /><text x={25} y={7} className="legend-text">Occupied</text></g>
          <g transform="translate(90, 55)"><rect width="20" height="4" fill="#e74c3c" /><text x={25} y={7} className="legend-text">Issue</text></g>
        </g>
      </svg>
    </div>
  );
};
