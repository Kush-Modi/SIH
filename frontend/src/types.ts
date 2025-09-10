// TypeScript interfaces matching backend schemas

export type TrainPriority = "EXPRESS" | "REGIONAL" | "FREIGHT";

export type IssueType = "BLOCKED" | "SIGNAL_FAILURE" | "MAINTENANCE";

export type EventKind =
  | "BLOCK_FAILED"
  | "BLOCK_CLEARED"
  | "DELAY_INJECTED"
  | "TRAIN_ARRIVED"
  | "TRAIN_DEPARTED";

export interface BlockState {
  id: string;
  occupied_by: string | null;
  issue: {
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

  // New optional timing fields for smooth animation (client-side interpolation)
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

export type WebSocketMessage = StateMessage | EventMessage;

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

// Topology types for rendering (optional, used by schematic-only UIs)
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
