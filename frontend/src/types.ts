// TypeScript interfaces matching backend schemas and frontend needs

// ---------- Enums & String literal unions ----------

export type TrainPriority = "EXPRESS" | "REGIONAL" | "FREIGHT";

export type IssueType = "BLOCKED" | "SIGNAL_FAILURE" | "MAINTENANCE";

// Backend emits this on completion as an event as well
export type EventKind =
  | "BLOCK_FAILED"
  | "BLOCK_CLEARED"
  | "DELAY_INJECTED"
  | "TRAIN_ARRIVED"
  | "TRAIN_DEPARTED"
  | "SIMULATION_COMPLETED";

// Lifecycle status from backend state messages
export type SimStatus = "IDLE" | "RUNNING" | "COMPLETED";

// ---------- Core state messages ----------

export interface BlockState {
  id: string;
  occupied_by: string | null;
  // Backend may omit issue when absent; treat as nullable or undefined
  issue?: {
    type: IssueType;
    since: string;
  } | null;
}

export interface TrainState {
  id: string;
  name: string;
  priority: TrainPriority;
  at_block: string;
  next_block: string | null;
  eta_next: string | null;

  // Optional timing fields for smooth animation (client-side interpolation)
  entered_block_at?: string | null; // ISO time the train entered current block
  will_exit_at?: string | null;     // ISO expected time to leave current block

  delay_min: number;
  dwell_sec_remaining: number;
  speed_kmh: number;
}

export interface KPIMetrics {
  avg_delay_min: number;
  trains_on_line: number;
  // Optional extras so backend can omit them without breaking the UI
  conflicts_resolved?: number;
  energy_efficiency?: number;
}

export interface StateMessage {
  type: "state";
  sim_time: string; // ISO simulation clock from backend
  blocks: BlockState[];
  trains: TrainState[];
  kpis: KPIMetrics;
  status: SimStatus; // explicit lifecycle status
}

export interface EventMessage {
  type: "event";
  event_id: string;
  event_kind: EventKind;
  block_id: string | null;
  train_id: string | null;
  timestamp: string;
  note: string;
}

// Server heartbeat frames to keep the socket alive
export interface HeartbeatMessage {
  type: "heartbeat";
  ts: string;
}

// Union of all frames received over WebSocket
export type WebSocketMessage = StateMessage | EventMessage | HeartbeatMessage;

// ---------- Control & actions ----------

export interface ControlPayload {
  headway_sec?: number;
  dwell_sec?: number;
  energy_stop_penalty?: number;
  simulation_speed?: number;
}

export interface DelayInjection {
  train_id: string;
  delay_minutes: number;
}

export interface BlockIssueInjection {
  block_id: string;
  blocked: boolean;
}

// UI-specific types

export interface ConnectionStatus {
  connected: boolean;
  reconnecting: boolean;
  lastError: string | null;
}

export interface TrainPosition {
  trainId: string;
  blockId: string;
  progress: number; // 0-1 along the block
}

// ---------- Optimizer / Rerun A/B types (frontend rendering) ----------

export type RerunTrainRow = { train_id: string; name: string; delay_min: number };

export type RerunMetrics = {
  avg_delay_min: number;
  trains_on_line: number;
  duration_sec: number;
  by_train: RerunTrainRow[];
  by_block: { block_id: string; occupancy_sec: number }[];
};

export type RerunDiffTrain = { train_id: string; name: string; delta_delay_min: number };
export type RerunDiffBlock = { block_id: string; delta_occupancy_sec: number };

export type RerunDiff = {
  delta_avg_delay_min: number;
  delta_duration_sec: number;
  trains: RerunDiffTrain[];
  blocks: RerunDiffBlock[];
};

export type PlanHold = { train_id: string; block_id: string; not_before_offset_sec: number };
export type PlanIn = { holds: PlanHold[] };

export type RerunResponse = {
  baseline: RerunMetrics;
  optimized: RerunMetrics;
  plan: PlanIn;
  diff: RerunDiff;
};

// Enriched meta from /rerun-optimized with trials and confidence intervals
export type RerunMeta = {
  trials: number;
  seeds_used: number[];
  holds_applied: number;
  avg_delay_min_delta_mean: number;
  avg_delay_min_delta_ci95: [number, number];
  duration_sec_delta_mean: number;
  duration_sec_delta_ci95: [number, number];
};

// Complete enriched payload shape returned by /rerun-optimized
export type RerunResponseEnriched = RerunResponse & { meta?: RerunMeta };

// ---------- Optional topology types for rendering ----------

export interface Station {
  id: string;
  name: string;
  x: number;
  y: number;
}

export interface BlockGeometry {
  id: string;
  name: string;
  startX: number;
  startY: number;
  endX: number;
  endY: number;
  station_id: string | null;
}
