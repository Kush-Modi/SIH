import React, { useMemo, useState, useCallback } from 'react';
import { StateMessage, TrainPriority } from '../types';
import './NarrativePanel.css';

interface NarrativePanelProps {
  state: StateMessage | null;
}

const PRIORITY_EMOJI: Record<TrainPriority, string> = {
  EXPRESS: '🚄',
  REGIONAL: '🚈',
  FREIGHT: '🚛',
};

function titleCase(s: string) {
  if (!s) return '';
  return s.toLowerCase().split(' ').map(w => (w ? w.toUpperCase() + w.slice(1) : '')).join(' ');
}

function minutesLeft(sec?: number | null) {
  if (!sec || sec <= 0) return null;
  const m = Math.ceil(sec / 60);
  return m <= 1 ? '≈1 min left' : `≈${m} min left`;
}

function fmtETA(iso?: string | null) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

type Tone = 'ok' | 'warn' | 'info';
type Filter = 'ALL' | 'DELAYED' | 'DWELLING';

export const NarrativePanel: React.FC<NarrativePanelProps> = ({ state }) => {
  const [filter, setFilter] = useState<Filter>('ALL');
  const [collapsed, setCollapsed] = useState(true); // show top N first
  const MAX_ROWS = 6;

  const blockNameById: Record<string, string> = useMemo(() => {
    const m: Record<string, string> = {};
    state?.blocks.forEach(b => { m[b.id] = b.id; });
    return m;
  }, [state?.blocks]);

  const lines = useMemo(() => {
    if (!state) {
      return [{ key: 'waiting', text: 'Waiting for simulation data…', tone: 'info' as Tone }];
    }

    // Build items with derived tone and helpful chips
    const items = state.trains.map(t => {
      const emoji = PRIORITY_EMOJI[t.priority] ?? '🚆';
      const at = blockNameById[t.at_block] || t.at_block;
      const next = t.next_block ? (blockNameById[t.next_block] || t.next_block) : null;
      const where = next ? `moving from ${at} → ${next}` : `at ${at}`;
      const dwellHint = minutesLeft(t.dwell_sec_remaining);
      const dwellText = dwellHint ? ` • dwelling ${dwellHint}` : '';
      const delay = t.delay_min || 0;
      const eta = fmtETA(t.eta_next);
      const etaText = eta ? ` • ETA ${eta}` : '';
      const delayText = delay > 0 ? ` • +${delay}m` : ' • on time';
      const text = `${emoji} ${t.name} (${titleCase(t.priority)}): ${where}${dwellText}${delayText}${etaText}.`;
      const tone: Tone = delay > 0 ? 'warn' : dwellHint ? 'info' : 'ok';
      return { key: t.id, text, tone, delay, dwelling: !!dwellHint };
    });

    // Filter: delayed/dwelling/all
    const filtered = items.filter(it => {
      if (filter === 'ALL') return true;
      if (filter === 'DELAYED') return it.delay > 0;
      if (filter === 'DWELLING') return it.dwelling;
      return true;
    });

    // Sort: by delay desc, then by priority (EXPRESS first), then by name
    const prRank: Record<TrainPriority, number> = { EXPRESS: 0, REGIONAL: 1, FREIGHT: 2 };
    filtered.sort((a, b) => {
      if (b.delay !== a.delay) return b.delay - a.delay;
      // derive priority from text if needed (kept simple: EXPRESS earlier)
      const aP = a.text.includes('(Express)') ? 0 : a.text.includes('(Regional)') ? 1 : 2;
      const bP = b.text.includes('(Express)') ? 0 : b.text.includes('(Regional)') ? 1 : 2;
      if (aP !== bP) return aP - bP;
      return a.text.localeCompare(b.text);
    });

    return filtered;
  }, [state, blockNameById, filter]);

  const visible = useMemo(() => {
    if (collapsed && lines.length > MAX_ROWS) return lines.slice(0, MAX_ROWS);
    return lines;
  }, [lines, collapsed]);

  const toggleFilter = useCallback((f: Filter) => setFilter(f), []);
  const toggleCollapsed = useCallback(() => setCollapsed(c => !c), []);

  return (
    <div className="narrative-panel" aria-live="polite">
      <div className="narrative-header">
        What’s happening now
        <div className="np-controls" role="group" aria-label="Narrative filters">
          <button
            className={`np-chip ${filter === 'ALL' ? 'active' : ''}`}
            onClick={() => toggleFilter('ALL')}
          >
            All
          </button>
          <button
            className={`np-chip ${filter === 'DELAYED' ? 'active' : ''}`}
            onClick={() => toggleFilter('DELAYED')}
          >
            Delayed
          </button>
          <button
            className={`np-chip ${filter === 'DWELLING' ? 'active' : ''}`}
            onClick={() => toggleFilter('DWELLING')}
          >
            Dwelling
          </button>
        </div>
      </div>

      <ul className="narrative-list">
        {visible.map(item => (
          <li key={item.key} className={`narrative-line ${item.tone}`}>
            {item.text}
          </li>
        ))}
      </ul>

      {lines.length > MAX_ROWS && (
        <div className="np-footer">
          <button className="np-toggle" onClick={toggleCollapsed}>
            {collapsed ? `Show more (${lines.length - MAX_ROWS})` : 'Show less'}
          </button>
        </div>
      )}
    </div>
  );
};
