import json
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field

from .schemas import (
    StateMessage, EventMessage, TrainState, BlockState, KPIMetrics,
    TrainPriority, EventKind, RailwayTopology, ControlPayload
)

ISO = "%Y-%m-%dT%H:%M:%S.%fZ"

def iso(dt: datetime) -> str:
    # Always emit Z time with milliseconds for smooth client-side interpolation
    return (dt if dt.tzinfo is None else dt.astimezone(tz=None)).strftime(ISO)

# -------------------- Domain models --------------------

@dataclass
class Train:
    id: str
    name: str
    priority: TrainPriority
    current_block: str
    route: List[str]
    route_index: int = 0
    speed_kmh: float = 80.0
    next_block: Optional[str] = None
    delay_minutes: int = 0
    dwell_remaining: int = 0  # seconds
    entered_block_at: Optional[datetime] = None
    will_exit_at: Optional[datetime] = None
    waiting_sec: float = 0.0   # accumulated wait converts to delay

@dataclass
class Block:
    id: str
    name: str
    length_km: float
    max_speed_kmh: float
    adjacent_blocks: List[str]
    station_id: Optional[str] = None
    platform_id: Optional[str] = None
    occupied_by: Optional[str] = None
    issue: Optional[Dict] = None
    issue_since: Optional[datetime] = None
    last_exit_time: Optional[datetime] = None  # for headway enforcement

# -------------------- Simulator --------------------

class RailwaySimulator:
    """
    Discrete-time railway simulator with per-block headway, station dwell,
    and frontend-friendly timing for smooth animation along the schematic.
    """

    def __init__(self):
        self.topology: Optional[RailwayTopology] = None
        self.blocks: Dict[str, Block] = {}
        self.trains: Dict[str, Train] = {}
        self.sim_time = datetime.utcnow()
        self.tick_count = 0

        # Tunables (can be updated through control API)
        self.base_tick_sec: float = 5.0       # seconds of sim time per step at speed=1.0
        self.headway_sec: int = 120           # minimum time gap between trains on a block
        self.dwell_sec: int = 60              # station stop time in seconds
        self.energy_stop_penalty: float = 0.0
        self.simulation_speed: float = 1.0    # multiplier for time passage

        # Demo aid: cap max travel to keep motion visible in short demos
        self.max_block_travel_sec: int = 45

        # Lifecycle
        self.completed: bool = False          # true once all trains finish
        self._completion_emitted: bool = False

        # Events
        self.event_counter = 0
        self.recent_events: List[EventMessage] = []

    # --------------- Lifecycle ---------------

    async def initialize(self):
        self.reset()

    def reset(self):
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        topology_path = os.path.join(current_dir, "topology.json")
        with open(topology_path, "r") as f:
            topology_data = json.load(f)
        self.topology = RailwayTopology(**topology_data)

        # Reset clocks/counters
        self.sim_time = datetime.utcnow()
        self.tick_count = 0
        self.event_counter = 0
        self.recent_events.clear()
        self.completed = False
        self._completion_emitted = False

        # Load blocks
        self.blocks = {}
        for b in self.topology.blocks:
            if not isinstance(b.id, str):
                raise ValueError(f"Block.id must be string, got {type(b.id)}: {b.id!r}")
            self.blocks[b.id] = Block(
                id=b.id,
                name=b.name,
                length_km=b.length_km,
                max_speed_kmh=b.max_speed_kmh,
                adjacent_blocks=list(b.adjacent_blocks),
                station_id=b.station_id,
                platform_id=b.platform_id,
            )

        # Parameters from topology defaults
        self.headway_sec = int(getattr(self.topology, "default_headway_sec", self.headway_sec))
        self.dwell_sec = int(getattr(self.topology, "default_dwell_sec", self.dwell_sec))

        # Trains
        self.trains = {}
        self._initialize_trains()

        print(f"Simulator reset with {len(self.blocks)} blocks and {len(self.trains)} trains")

    # --------------- Helpers ---------------

    def _flatten_route(self, route) -> List[str]:
        flat: List[str] = []
        def walk(x):
            if isinstance(x, (list, tuple)):
                for y in x:
                    walk(y)
            else:
                s = str(x).strip()
                if s:
                    flat.append(s)
        walk(route)
        return flat

    def _priority_speed(self, pr: TrainPriority) -> float:
        return 100.0 if pr == TrainPriority.EXPRESS else (70.0 if pr == TrainPriority.REGIONAL else 60.0)

    def _block_travel_seconds(self, train: Train, block_id: str) -> float:
        b = self.blocks[block_id]
        if b.station_id:
            return 0.0  # dwell governs at stations
        speed = min(train.speed_kmh, max(b.max_speed_kmh, 1.0))
        travel = (b.length_km / max(speed, 1e-6)) * 3600.0
        return max(1.0, min(travel, float(self.max_block_travel_sec)))  # cap to keep motion visible

    def _compute_will_exit(self, train: Train, block_id: str, enter: datetime) -> datetime:
        if self.blocks[block_id].station_id:
            return enter + timedelta(seconds=self.dwell_sec)
        return enter + timedelta(seconds=self._block_travel_seconds(train, block_id))

    def _is_completed(self) -> bool:
        # Completed when every train is at final route index, not traversing, and not dwelling
        for t in self.trains.values():
            at_end = t.route_index >= len(t.route) - 1
            traversing = bool(t.will_exit_at and self.sim_time < t.will_exit_at)
            dwelling = (t.dwell_remaining or 0) > 0
            if not at_end or traversing or dwelling:
                return False
        return True

    # --------------- Seeding ---------------

    def _initialize_trains(self):
        train_configs = [
            {"id": "T1", "name": "EXP-12001", "priority": TrainPriority.EXPRESS,  "route": ["B1", "B2", "B3", "B4", "B5", "B6", "B7"]},
            {"id": "T2", "name": "REG-22002", "priority": TrainPriority.REGIONAL, "route": ["B7", "B6", "B5", "B4", "B3", "B2", "B1"]},
            {"id": "T3", "name": "EXP-12003", "priority": TrainPriority.EXPRESS,  "route": ["B1", "B2", "B8", "B9", "B6", "B7"]},
            {"id": "T4", "name": "FRE-32004", "priority": TrainPriority.FREIGHT,  "route": ["B3", "B4", "B5", "B10"]},
            {"id": "T5", "name": "REG-22005", "priority": TrainPriority.REGIONAL, "route": ["B6", "B9", "B8", "B2", "B1"]},
            {"id": "T6", "name": "EXP-12006", "priority": TrainPriority.EXPRESS,  "route": ["B1", "B2", "B3", "B11"]},
            {"id": "T7", "name": "FRE-32007", "priority": TrainPriority.FREIGHT,  "route": ["B10", "B5", "B4", "B3", "B2", "B1"]},
            {"id": "T8", "name": "REG-22008", "priority": TrainPriority.REGIONAL, "route": ["B7", "B6", "B5", "B4", "B3", "B2", "B1"]},
        ]

        # Clear occupancy
        for b in self.blocks.values():
            b.occupied_by = None
            b.last_exit_time = None

        for cfg in train_configs:
            route = self._flatten_route(cfg["route"])
            if not route:
                raise ValueError(f"Train {cfg['id']} has empty/invalid route: {cfg['route']!r}")
            missing = [bid for bid in route if bid not in self.blocks]
            if missing:
                raise ValueError(f"Train {cfg['id']} route unknown blocks: {missing}. Known: {list(self.blocks.keys())}")

            # Try to deconflict starting placement: first free block along route
            start_index = 0
            for i, bid in enumerate(route[:3]):
                if self.blocks[bid].occupied_by is None:
                    start_index = i
                    break

            start_block = route[start_index]
            priority = cfg["priority"]
            speed = self._priority_speed(priority)
            t = Train(
                id=cfg["id"],
                name=cfg["name"],
                priority=priority,
                current_block=start_block,
                route=route,
                route_index=start_index,
                speed_kmh=speed,
            )

            # Stagger entry for variety
            enter_offset = random.randint(0, 40)
            t.entered_block_at = self.sim_time - timedelta(seconds=enter_offset)
            t.will_exit_at = self._compute_will_exit(t, start_block, t.entered_block_at)
            t.next_block = route[start_index + 1] if start_index < len(route) - 1 else None
            t.delay_minutes = random.randint(0, 2)

            self.trains[t.id] = t
            self.blocks[start_block].occupied_by = t.id

    # --------------- Step ---------------

    def step(self) -> List[EventMessage]:
        # If already completed, do not advance time or generate more moves
        if self.completed:
            return []

        # Advance simulation clock (scaled)
        self.tick_count += 1
        self.sim_time += timedelta(seconds=self.base_tick_sec * float(self.simulation_speed))
        events: List[EventMessage] = []

        for train in self.trains.values():
            events.extend(self._process_train(train))

        # Completion check and one-shot event
        if not self.completed and self._is_completed():
            self.completed = True
            if not self._completion_emitted:
                events.append(self._create_event(
                    EventKind.SIMULATION_COMPLETED,
                    note="All trains reached their final blocks"
                ))
                self._completion_emitted = True

        return events

    def _process_train(self, train: Train) -> List[EventMessage]:
        events: List[EventMessage] = []
        cur_block = self.blocks[train.current_block]

        # Update dwell remaining if at station and still before will_exit_at
        if cur_block.station_id:
            if train.will_exit_at and self.sim_time < train.will_exit_at:
                train.dwell_remaining = int((train.will_exit_at - self.sim_time).total_seconds())
            else:
                train.dwell_remaining = 0

        # Traversing or dwelling: not ready to move
        if train.will_exit_at and self.sim_time < train.will_exit_at:
            return events

        # End of route
        if train.route_index >= len(train.route) - 1:
            return events

        next_block_id = train.route[train.route_index + 1]
        if not self._can_enter_next_block(next_block_id):
            # Accumulate waiting; convert to delay minutes periodically
            train.waiting_sec += self.base_tick_sec * float(self.simulation_speed)
            if train.waiting_sec >= 60.0:
                inc = int(train.waiting_sec // 60.0)
                train.delay_minutes += inc
                train.waiting_sec -= 60.0 * inc
            return events

        # Depart current block
        cur_block.occupied_by = None
        cur_block.last_exit_time = self.sim_time
        events.append(self._create_event(
            EventKind.TRAIN_DEPARTED,
            train_id=train.id,
            block_id=cur_block.id,
            note=f"{train.name} departed {cur_block.name}"
        ))

        # Enter next block
        nxt = self.blocks[next_block_id]
        nxt.occupied_by = train.id
        train.current_block = next_block_id
        train.route_index += 1
        train.entered_block_at = self.sim_time
        train.will_exit_at = self._compute_will_exit(train, next_block_id, self.sim_time)
        train.next_block = train.route[train.route_index + 1] if train.route_index < len(train.route) - 1 else None
        train.waiting_sec = 0.0

        if nxt.station_id:
            events.append(self._create_event(
                EventKind.TRAIN_ARRIVED,
                train_id=train.id,
                block_id=nxt.id,
                note=f"{train.name} arrived at {nxt.name}"
            ))
        return events

    def _can_enter_next_block(self, block_id: str) -> bool:
        nxt = self.blocks[block_id]
        if nxt.occupied_by is not None or nxt.issue is not None:
            return False
        if nxt.last_exit_time is not None:
            gap = (self.sim_time - nxt.last_exit_time).total_seconds()
            if gap < self.headway_sec:
                return False
        return True

    # --------------- Events & State ---------------

    def _create_event(self, event_kind: EventKind, train_id: Optional[str] = None,
                      block_id: Optional[str] = None, note: str = "") -> EventMessage:
        self.event_counter += 1
        return EventMessage(
            type="event",
            event_id=f"E{self.tick_count}-{self.event_counter}",
            event_kind=event_kind,
            train_id=train_id,
            block_id=block_id,
            timestamp=iso(self.sim_time),
            note=note,
        )

    def get_state_message(self) -> StateMessage:
        # Blocks
        block_states: List[BlockState] = []
        for b in self.blocks.values():
            issue = None
            if b.issue:
                issue = {
                    "type": b.issue.get("type", "BLOCKED"),
                    "since": iso(b.issue_since or self.sim_time)
                }
            block_states.append(BlockState(id=b.id, occupied_by=b.occupied_by, issue=issue))

        # Trains (include timing for client interpolation)
        train_states: List[TrainState] = []
        for t in self.trains.values():
            eta_next = iso(t.will_exit_at) if t.will_exit_at else None
            train_states.append(TrainState(
                id=t.id,
                name=t.name,
                priority=t.priority,
                at_block=t.current_block,
                next_block=t.next_block,
                eta_next=eta_next,
                entered_block_at=iso(t.entered_block_at) if t.entered_block_at else None,
                will_exit_at=iso(t.will_exit_at) if t.will_exit_at else None,
                delay_min=t.delay_minutes,
                dwell_sec_remaining=t.dwell_remaining,
                speed_kmh=t.speed_kmh,
            ))

        # KPIs
        n = max(1, len(self.trains))
        avg_delay = sum(t.delay_minutes for t in self.trains.values()) / n
        trains_on_line = len(self.trains)
        kpis = KPIMetrics(avg_delay_min=round(avg_delay, 1), trains_on_line=trains_on_line)

        # Status flag for lifecycle end
        status = "COMPLETED" if self.completed else "RUNNING"

        return StateMessage(
            type="state",
            sim_time=iso(self.sim_time),
            blocks=block_states,
            trains=train_states,
            kpis=kpis,
            status=status,
        )

    # --------------- Controls ---------------

    def update_parameters(self, control: ControlPayload):
        if control.headway_sec is not None:
            self.headway_sec = max(0, int(control.headway_sec))
        if control.dwell_sec is not None:
            self.dwell_sec = max(0, int(control.dwell_sec))
        if control.energy_stop_penalty is not None:
            self.energy_stop_penalty = max(0.0, float(control.energy_stop_penalty))
        if control.simulation_speed is not None:
            self.simulation_speed = float(max(0.1, min(10.0, control.simulation_speed)))
