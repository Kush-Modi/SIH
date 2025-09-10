from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from enum import Enum

# ==== Enums ====

class TrainPriority(str, Enum):
    EXPRESS = "EXPRESS"
    REGIONAL = "REGIONAL"
    FREIGHT = "FREIGHT"


class IssueType(str, Enum):
    BLOCKED = "BLOCKED"
    SIGNAL_FAILURE = "SIGNAL_FAILURE"
    MAINTENANCE = "MAINTENANCE"


class EventKind(str, Enum):
    BLOCK_FAILED = "BLOCK_FAILED"
    BLOCK_CLEARED = "BLOCK_CLEARED"
    DELAY_INJECTED = "DELAY_INJECTED"
    TRAIN_ARRIVED = "TRAIN_ARRIVED"
    TRAIN_DEPARTED = "TRAIN_DEPARTED"
    SIMULATION_COMPLETED = "SIMULATION_COMPLETED"  # new: one-shot completion signal


# ==== State payloads ====

class Issue(BaseModel):
    """Block issue payload (serialized to {type, since})."""
    type: IssueType = Field(..., description="Issue type (e.g., BLOCKED)")
    since: str = Field(..., description="ISO datetime when the issue started")


class BlockState(BaseModel):
    id: str
    occupied_by: Optional[str] = Field(None, description="Train ID occupying the block (if any)")
    issue: Optional[Issue] = Field(None, description="Issue attached to this block")


class TrainState(BaseModel):
    id: str
    name: str
    priority: TrainPriority
    at_block: str
    next_block: Optional[str] = None
    eta_next: Optional[str] = Field(None, description="ISO time when the train is expected to leave current block")

    # Optional timing fields for smooth front-end interpolation
    entered_block_at: Optional[str] = Field(None, description="ISO time when train entered current block")
    will_exit_at: Optional[str] = Field(None, description="ISO time when train is expected to leave current block")

    delay_min: int = Field(0, ge=0, description="Total delay minutes accrued")
    dwell_sec_remaining: int = Field(0, ge=0, description="Remaining dwell time in seconds")
    speed_kmh: float = Field(80.0, ge=0, description="Nominal speed used for travel-time estimation")


class KPIMetrics(BaseModel):
    avg_delay_min: float
    trains_on_line: int
    # Optional/derived KPIs with defaults so the UI never breaks if omitted
    conflicts_resolved: int = 0
    energy_efficiency: float = 0.0


class StateMessage(BaseModel):
    type: Literal["state"] = "state"
    sim_time: str  # ISO datetime
    blocks: List[BlockState]
    trains: List[TrainState]
    kpis: KPIMetrics
    # New: status to mark lifecycle end; optional and backward-compatible
    status: Optional[Literal["RUNNING", "COMPLETED"]] = "RUNNING"


# ==== Event payloads ====

class EventMessage(BaseModel):
    type: Literal["event"] = "event"
    event_id: str
    event_kind: EventKind
    block_id: Optional[str] = None
    train_id: Optional[str] = None
    timestamp: str  # ISO datetime
    note: str


# ==== Control payloads ====

class ControlPayload(BaseModel):
    headway_sec: Optional[int] = Field(None, ge=0, description="Minimum seconds between trains on a block")
    dwell_sec: Optional[int] = Field(None, ge=0, description="Station dwell time seconds")
    energy_stop_penalty: Optional[float] = Field(None, ge=0.0, description="Weight for energy-aware objectives")
    simulation_speed: Optional[float] = Field(None, gt=0.0, description="Time multiplier for the simulator clock")


class DelayInjection(BaseModel):
    train_id: str
    delay_minutes: int = Field(..., ge=1, le=60)


class BlockIssueInjection(BaseModel):
    block_id: str
    blocked: bool


# ==== Topology ====

class Platform(BaseModel):
    id: str
    name: str
    capacity: int = Field(1, ge=1)


class Station(BaseModel):
    id: str
    name: str
    platforms: List[Platform]


class Block(BaseModel):
    id: str
    name: str
    length_km: float = Field(..., ge=0.0)
    max_speed_kmh: float = Field(..., gt=0.0)
    adjacent_blocks: List[str]
    station_id: Optional[str] = None
    platform_id: Optional[str] = None


class RailwayTopology(BaseModel):
    stations: List[Station]
    blocks: List[Block]
    default_headway_sec: int = Field(120, ge=0)
    default_dwell_sec: int = Field(60, ge=0)
    default_speed_kmh: float = Field(80.0, gt=0.0)
