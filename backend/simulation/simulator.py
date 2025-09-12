from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum
import random

from .schemas import (
    StateMessage, EventMessage, TrainState, BlockState, KPIMetrics,
    TrainPriority, EventKind, RailwayTopology, ControlPayload
)
from .plan import Plan

# ISO format: we will print milliseconds (3 digits) and a trailing 'Z'
ISO_BASE = "%Y-%m-%dT%H:%M:%S"


def iso(dt: datetime) -> str:
    """
    Always emit Z time with milliseconds for smooth client-side interpolation.
    Produces format like: 2025-09-11T12:34:56.123Z
    """
    if dt is None:
        raise ValueError("iso() requires a datetime, got None")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    # Build string with millisecond precision
    base = dt.strftime(ISO_BASE)
    ms = dt.microsecond // 1000
    return f"{base}.{ms:03d}Z"


class SimulationStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


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
    offset-based plan holds, explicit lifecycle states, and batch A/B helpers.
    """

    def __init__(self, seed: Optional[int] = None):
        # Deterministic per-instance RNG for reproducible demos / A/B runs
        self._seed: Optional[int] = seed
        self._rng: random.Random = random.Random(seed)

        self.topology: Optional[RailwayTopology] = None
        self.blocks: Dict[str, Block] = {}
        self.trains: Dict[str, Train] = {}
        self.sim_time = datetime.now(timezone.utc)
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
        self.status: SimulationStatus = SimulationStatus.IDLE
        self.completed: bool = False          # true once all trains finish
        self._completion_emitted: bool = False

        # Idle safety to prevent stalling forever
        self._moved_this_tick: bool = False
        self._idle_ticks: int = 0
        self._idle_limit: int = 200           # ticks with no movement before forced completion

        # Events
        self.event_counter = 0
        self.recent_events: List[EventMessage] = []

        # Optimization plan (offset holds)
        self.active_plan: Optional[Plan] = None
        # key "train_id|block_id" -> absolute datetime
        self.holds_index: Dict[str, datetime] = {}

    # --------------- Lifecycle ---------------

    async def initialize(self):
        # Initialize to a clean IDLE state; do not auto-start
        self.reset()

    def start(self):
        """
        Transition from IDLE to RUNNING; if already COMPLETED, require reset first.
        """
        if self.status == SimulationStatus.COMPLETED:
            # Reset required before starting a new live run
            return
        if self.status != SimulationStatus.RUNNING:
            self.status = SimulationStatus.RUNNING

    def reset(self):
        """
        Full reset to IDLE. Loads topology and trains, clears plans/events/counters.
        Does not start automatically.
        """
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        topology_path = os.path.join(current_dir, "topology.json")
        with open(topology_path, "r") as f:
            topology_data = json.load(f)
        # Construct typed topology (may raise if schema mismatches)
        self.topology = RailwayTopology(**topology_data)

        # Reset clocks/counters
        self.sim_time = datetime.now(timezone.utc)
        self.tick_count = 0
        self.event_counter = 0
        self.recent_events.clear()
        self.completed = False
        self._completion_emitted = False
        self._moved_this_tick = False
        self._idle_ticks = 0
        self.active_plan = None
        self.holds_index.clear()

        # Status to IDLE on reset
        self.status = SimulationStatus.IDLE

        # Load blocks
        self.blocks = {}
        for b in self.topology.blocks:
            if not isinstance(b.id, str):
                raise ValueError(f"Block.id must be string, got {type(b.id)}: {b.id!r}")
            self.blocks[b.id] = Block(
                id=b.id,
                name=getattr(b, "name", str(b.id)),
                length_km=float(getattr(b, "length_km", 1.0)),
                max_speed_kmh=float(getattr(b, "max_speed_kmh", 80.0)),
                adjacent_blocks=list(getattr(b, "adjacent_blocks", [])),
                station_id=getattr(b, "station_id", None),
                platform_id=getattr(b, "platform_id", None),
            )

        # Parameters from topology defaults (fall back to existing simulator defaults)
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
                # Accept objects that have an 'id' attribute or plain strings
                if hasattr(x, "id"):
                    s = str(getattr(x, "id"))
                else:
                    s = str(x).strip()
                if s:
                    flat.append(s)

        walk(route)
        return flat

    def _priority_speed(self, pr) -> float:
        # Accept TrainPriority enum or string
        name = None
        if hasattr(pr, "name"):
            name = pr.name
        else:
            name = str(pr)
        name = name.upper()
        if name == "EXPRESS":
            return 100.0
        if name == "REGIONAL":
            return 70.0
        return 60.0

    def _block_travel_seconds(self, train: Train, block_id: str) -> float:
        b = self.blocks[block_id]
        if b.station_id:
            # travel seconds don't govern station dwell (handled separately)
            return 0.0
        speed = min(train.speed_kmh, max(b.max_speed_kmh, 1.0))
        travel = (b.length_km / max(speed, 1e-6)) * 3600.0
        # keep motion visible in demos and don't allow zero
        return max(1.0, min(travel, float(self.max_block_travel_sec)))

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

    # --------------- Seeding / train init ---------------

    def _initialize_trains(self):
        # Example seeded train configs — keep these as your demo dataset
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

            # Try to deconflict starting placement: first free block along the entire route
            start_index = 0
            found_free = False
            for i, bid in enumerate(route):
                if self.blocks[bid].occupied_by is None:
                    start_index = i
                    found_free = True
                    break

            if not found_free:
                # No free block found — warn and fall back to index 0 (keeps demo runnable)
                print(f"Warning: no free start block for train {cfg['id']}; placing at route = {route} (may overlap)")

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

            # Stagger entry for variety (seeded per-instance for reproducibility)
            enter_offset = self._rng.randint(0, 40)
            t.entered_block_at = self.sim_time - timedelta(seconds=enter_offset)
            t.will_exit_at = self._compute_will_exit(t, start_block, t.entered_block_at)
            t.next_block = route[start_index + 1] if start_index < len(route) - 1 else None
            t.delay_minutes = self._rng.randint(0, 2)

            self.trains[t.id] = t
            # Mark block as occupied (even if this overwrites — we warned above)
            self.blocks[start_block].occupied_by = t.id

    # --------------- Step ---------------

    def step(self) -> List[EventMessage]:
        # Only advance when explicitly RUNNING
        if self.status != SimulationStatus.RUNNING:
            return []

        # If already completed, do not advance time or generate more moves
        if self.completed:
            return []

        # Advance simulation clock (scaled)
        self.tick_count += 1
        self.sim_time += timedelta(seconds=self.base_tick_sec * float(self.simulation_speed))
        events: List[EventMessage] = []

        # Movement tracking for idle detection
        self._moved_this_tick = False

        # iterate over snapshot copy since trains dict is mutated inside
        for train in list(self.trains.values()):
            events.extend(self._process_train(train))

        # Idle/Completion checks
        if self._moved_this_tick:
            self._idle_ticks = 0
        else:
            self._idle_ticks += 1
            # Safety fuse: if nothing moves for many ticks, end the run
            if self._idle_ticks >= self._idle_limit:
                self.completed = True

        if not self.completed and self._is_completed():
            self.completed = True

        # Emit one-shot completion event and transition to COMPLETED
        if self.completed and not self._completion_emitted:
            events.append(self._create_event(
                EventKind.SIMULATION_COMPLETED,
                note="All trains reached their final blocks"
            ))
            self._completion_emitted = True
            self.status = SimulationStatus.COMPLETED

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

        # Plan hold gating: key by train_id|block_id
        if self.active_plan:
            hold_key = f"{train.id}|{next_block_id}"
            hold_until = self.holds_index.get(hold_key)
            if hold_until is not None and self.sim_time < hold_until:
                # Treat like any wait (similar to headway)
                train.waiting_sec += self.base_tick_sec * float(self.simulation_speed)
                if train.waiting_sec >= 60.0:
                    inc = int(train.waiting_sec // 60.0)
                    train.delay_minutes += inc
                    train.waiting_sec -= 60.0 * inc
                return events

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

        # Mark movement for idle detection
        self._moved_this_tick = True

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

    def _create_event(
        self,
        event_kind: EventKind,
        train_id: Optional[str] = None,
        block_id: Optional[str] = None,
        note: str = ""
    ) -> EventMessage:
        self.event_counter += 1
        # Build a compact unique id: EYYYYMMDDHHMMSSmmm-ctr
        ts = self.sim_time.astimezone(timezone.utc)
        timestamp_str = ts.strftime("%Y%m%d%H%M%S%f")[:-3]  # trim to milliseconds
        return EventMessage(
            type="event",
            event_id=f"E{timestamp_str}-{self.event_counter}",
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
        status = self.status.value

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

    def inject_delay(self, train_id: str, delay_minutes: int) -> EventMessage:
        """Inject delay into a specific train"""
        if train_id not in self.trains:
            raise ValueError(f"Train {train_id} not found")
        train = self.trains[train_id]
        train.delay_minutes += max(0, int(delay_minutes))
        return self._create_event(
            EventKind.DELAY_INJECTED,
            train_id=train_id,
            note=f"Added {delay_minutes} min delay to {train.name}"
        )

    def set_block_issue(self, block_id: str, blocked: bool) -> EventMessage:
        """Set block issue (blocked/unblocked)"""
        if block_id not in self.blocks:
            raise ValueError(f"Block {block_id} not found")

        block = self.blocks[block_id]
        if blocked:
            block.issue = {"type": "BLOCKED", "since": iso(self.sim_time)}
            block.issue_since = self.sim_time
            event_kind = EventKind.BLOCK_FAILED
            note = f"Block {block_id} blocked"
        else:
            block.issue = None
            block.issue_since = None
            event_kind = EventKind.BLOCK_CLEARED
            note = f"Block {block_id} cleared"

        return self._create_event(
            event_kind,
            block_id=block_id,
            note=note
        )

    # --------------- Plans ---------------

    def apply_plan(self, plan: Plan):
        """
        Apply offset-based holds by anchoring them to the current sim_time.
        Use string key "train|block" for quick lookup at the gate.
        """
        self.active_plan = plan
        self.holds_index.clear()
        if plan and plan.holds:
            base = self.sim_time
            for h in plan.holds:
                try:
                    offset = int(h.not_before_offset_sec)
                except Exception:
                    # skip malformed holds
                    continue
                key = f"{h.train_id}|{h.block_id}"
                # anchor offsets as absolute datetimes
                self.holds_index[key] = base + timedelta(seconds=offset)

    def clear_plan(self):
        self.active_plan = None
        self.holds_index.clear()

    # --------------- Batch helpers (A/B) ---------------

    def run_to_completion(self, max_ticks: int = 100000) -> Dict[str, Any]:
        """
        Run this simulator instance from current state until completion or max_ticks.
        Returns a metrics dict for convenient API responses.
        """
        # Ensure running
        self.start()
        while not self.completed and self.tick_count < max_ticks:
            self.step()

        return self.collect_metrics()

    def collect_metrics(self) -> Dict[str, Any]:
        """
        Summarize KPIs and a few run details for reporting and diffs.
        """
        avg_delay = sum(t.delay_minutes for t in self.trains.values()) / max(1, len(self.trains))
        total_delay = sum(t.delay_minutes for t in self.trains.values())
        return {
            "avg_delay_min": round(avg_delay, 1),
            "total_delay_min": int(total_delay),
            "trains_on_line": len(self.trains),
            "ticks": self.tick_count,
            "sim_time": iso(self.sim_time),
            "completed": bool(self.completed),
            "status": self.status.value,
        }

    @classmethod
    def run_batch(cls, seed: Optional[int] = None, plan: Optional[Plan] = None, max_ticks: int = 100000) -> Dict[str, Any]:
        """
        Fire-and-forget batch run with optional plan using a fresh simulator,
        ensuring deterministic seeding and isolation from live runs.
        """
        sim = cls(seed=seed)
        sim.reset()
        if plan is not None:
            sim.apply_plan(plan)
        return sim.run_to_completion(max_ticks=max_ticks)

    @classmethod
    def ab_compare(cls, plan: Plan, seed: Optional[int] = None, max_ticks: int = 100000) -> Dict[str, Any]:
        """
        Run baseline (no plan) and optimized (with plan) with the same seed and
        return baseline, optimized, and diff metrics.
        """
        baseline = cls.run_batch(seed=seed, plan=None, max_ticks=max_ticks)
        optimized = cls.run_batch(seed=seed, plan=plan, max_ticks=max_ticks)
        diff = {
            "avg_delay_min_delta": round(baseline["avg_delay_min"] - optimized["avg_delay_min"], 1),
            "total_delay_min_delta": int(baseline["total_delay_min"] - optimized["total_delay_min"]),
            "ticks_delta": int(baseline["ticks"] - optimized["ticks"]),
        }
        return {
            "baseline": baseline,
            "optimized": optimized,
            "diff": diff,
        }
