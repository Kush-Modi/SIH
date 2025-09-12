from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import asyncio
import json
import uvicorn
from typing import List, Set, Optional, Tuple
import os
from datetime import datetime, timezone
import random
import math

from simulation.simulator import RailwaySimulator
from simulation.schemas import (
    StateMessage,
    EventMessage,
    ControlPayload,
    DelayInjection,
    BlockIssueInjection,
    OptimizerSnapshot,
    SnapshotParams,
    BlockSnapshot,
    TrainSnapshot,
    IssueSnapshot,
    PlanIn,
    RerunMetrics,
    TrainDelayRow,
    BlockUseRow,
    RerunDiff,
    RerunDiffTrain,
    RerunDiffBlock,
    RerunResponse,
)
from simulation.optimizer import optimize_from_sim
from simulation.optimizer_adapter import build_optimizer_input
from simulation.plan import Plan, HoldDirective

app = FastAPI(title="Railway Control System", version="1.0.0")

# CORS for local dev (broad for simplicity in demos)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Adjustable tick pacing
TICK_SLEEP_SEC = float(os.getenv("TICK_SLEEP_SEC", "0.5"))

# Global simulator (live run)
simulator = RailwaySimulator()

class ConnectionManager:
    def __init__(self) -> None:
        self.active: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active.discard(websocket)

    async def send_json(self, websocket: WebSocket, message: dict) -> None:
        await websocket.send_text(json.dumps(message))

    async def broadcast(self, message: dict) -> None:
        targets = list(self.active)
        if not targets:
            return
        disconnected: List[WebSocket] = []
        for ws in targets:
            try:
                await self.send_json(ws, message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

manager = ConnectionManager()

# Background tasks
app.state.bg_tasks: List[asyncio.Task] = []        # heartbeat and misc
app.state.sim_task: Optional[asyncio.Task] = None  # single simulation loop

def is_sim_loop_running() -> bool:
    t: Optional[asyncio.Task] = app.state.sim_task
    return t is not None and not t.done()

def start_simulation_loop():
    if not is_sim_loop_running():
        app.state.sim_task = asyncio.create_task(simulation_loop())

async def stop_simulation_loop():
    t: Optional[asyncio.Task] = app.state.sim_task
    if t and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    app.state.sim_task = None

@app.on_event("startup")
async def startup_event():
    # Initialize into IDLE state; do not auto-start simulation loop
    await simulator.initialize()
    # Heartbeat loop only
    app.state.bg_tasks.append(asyncio.create_task(heartbeat_loop()))

@app.on_event("shutdown")
async def shutdown_event():
    # Stop heartbeat tasks
    for t in app.state.bg_tasks:
        t.cancel()
    await asyncio.gather(*app.state.bg_tasks, return_exceptions=True)
    app.state.bg_tasks.clear()
    # Stop simulation loop if running
    await stop_simulation_loop()

async def simulation_loop():
    """
    Advance simulation and broadcast state+events while RUNNING; when completed,
    push one final snapshot and stop.
    """
    try:
        while True:
            if simulator.completed:
                final_state = simulator.get_state_message()
                await manager.broadcast(final_state.dict())
                break
            events = simulator.step()
            state_message = simulator.get_state_message()
            await manager.broadcast(state_message.dict())
            for ev in events:
                await manager.broadcast(ev.dict())
            await asyncio.sleep(TICK_SLEEP_SEC)
    except asyncio.CancelledError:
        # Graceful stop on reset/shutdown
        pass
    except Exception as e:
        print(f"[simulation_loop] error: {e}")
    finally:
        # Mark loop as stopped
        app.state.sim_task = None

async def heartbeat_loop():
    """
    Periodic keepalive to prevent idle connections from timing out.
    """
    while True:
        try:
            msg = {"type": "heartbeat", "ts": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()}
            await manager.broadcast(msg)
            await asyncio.sleep(15.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[heartbeat_loop] error: {e}")
            await asyncio.sleep(5.0)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        initial_state = simulator.get_state_message()
        await websocket.send_text(json.dumps(initial_state.dict()))
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()}

@app.get("/state")
async def get_state():
    return simulator.get_state_message().dict()

@app.post("/control")
async def update_control(control: ControlPayload):
    try:
        simulator.update_parameters(control)
        # Broadcast immediately so UI reflects param changes even while IDLE/RUNNING
        await manager.broadcast(simulator.get_state_message().dict())
        return {"status": "success", "message": "Parameters updated"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/inject/delay")
async def inject_delay(delay: DelayInjection):
    try:
        event = simulator.inject_delay(delay.train_id, delay.delay_minutes)
        await manager.broadcast(event.dict())
        return {"status": "success", "event": event.dict()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/inject/block-issue")
async def inject_block_issue(issue: BlockIssueInjection):
    try:
        event = simulator.set_block_issue(issue.block_id, issue.blocked)
        await manager.broadcast(event.dict())
        return {"status": "success", "event": event.dict()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------- Lifecycle endpoints ----------

@app.post("/start")
async def start_simulation():
    """
    Explicitly start the live simulation; spawns the stepping loop if not running.
    """
    # Prevent 'start' after completion without reset to keep lifecycle explicit
    if simulator.get_state_message().status == "COMPLETED":
        raise HTTPException(status_code=409, detail="Simulation completed. Reset required before starting again.")
    simulator.start()
    start_simulation_loop()
    await manager.broadcast(simulator.get_state_message().dict())
    return {"status": "success", "message": "Simulation started (or already running)"}

@app.post("/reset")
async def reset_simulation():
    """
    Reset to IDLE, cancel any running loop, and broadcast the clean state.
    """
    try:
        await stop_simulation_loop()
        simulator.reset()
        state_message = simulator.get_state_message()
        await manager.broadcast(state_message.dict())
        return {"status": "success", "message": "Simulation reset to IDLE"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/restart")
async def restart_simulation():
    """
    Convenience: reset to IDLE and immediately start a new run.
    """
    await stop_simulation_loop()
    simulator.reset()
    simulator.start()
    start_simulation_loop()
    await manager.broadcast(simulator.get_state_message().dict())
    return {"status": "success", "message": "Simulation restarted"}

# ---------- Batch optimization helpers (A/B with isolation) ----------

def compute_metrics(sim: RailwaySimulator, start_iso: str) -> RerunMetrics:
    """
    Compute summary metrics for a completed run.
    Durations computed using sim_time delta relative to run start.
    """
    state = sim.get_state_message()
    # Duration
    try:
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(state.sim_time.replace("Z", "+00:00"))
        duration_sec = int(max(0, (end_dt - start_dt).total_seconds()))
    except Exception:
        duration_sec = 0

    # By-train delays
    by_train = [
        TrainDelayRow(train_id=t.id, name=t.name, delay_min=t.delay_minutes)
        for t in sim.trains.values()
    ]

    # By-block occupancy placeholder (0) — can be enhanced later
    by_block: List[BlockUseRow] = [
        BlockUseRow(block_id=b.id, occupancy_sec=0) for b in sim.blocks.values()
    ]

    return RerunMetrics(
        avg_delay_min=state.kpis.avg_delay_min,
        trains_on_line=state.kpis.trains_on_line,
        duration_sec=duration_sec,
        by_train=by_train,
        by_block=by_block,
    )

def seed_repro(seed: int = 42) -> None:
    """
    Optional: seed other RNGs used by optimizer; simulator uses per-instance RNG.
    """
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:
        pass
    random.seed(seed)

async def run_to_completion(plan: Optional[Plan] = None, seed: int = 42) -> RerunMetrics:
    """
    Fire-and-forget batch run using a fresh simulator instance (isolated from live),
    optionally applying a plan; returns RerunMetrics for A/B diffing.
    """
    seed_repro(seed)
    sim = RailwaySimulator(seed=seed)
    sim.reset()
    start_iso = sim.sim_time.replace(tzinfo=timezone.utc).isoformat()
    if plan:
        sim.apply_plan(plan)
    sim.start()
    ticks = 0
    max_ticks = 20000
    while not sim.completed and ticks < max_ticks:
        sim.step()
        ticks += 1
    return compute_metrics(sim, start_iso)

def diff_metrics(a: RerunMetrics, b: RerunMetrics) -> RerunDiff:
    # delta = a - b (improvement if positive)
    delta_avg = float(a.avg_delay_min) - float(b.avg_delay_min)
    delta_dur = float(a.duration_sec) - float(b.duration_sec)
    # Train-level deltas by matching ids
    map_a = {t.train_id: t for t in a.by_train}
    map_b = {t.train_id: t for t in b.by_train}
    trains: List[RerunDiffTrain] = []
    for tid, row_a in map_a.items():
        row_b = map_b.get(tid)
        if row_b is None:
            continue
        trains.append(RerunDiffTrain(train_id=tid, name=row_a.name, delta_delay_min=float(row_a.delay_min - row_b.delay_min)))
    # Blocks placeholder (0 deltas)
    blocks: List[RerunDiffBlock] = []
    return RerunDiff(
        delta_avg_delay_min=round(delta_avg, 2),
        delta_duration_sec=round(delta_dur, 2),
        trains=sorted(trains, key=lambda x: x.delta_delay_min, reverse=True),
        blocks=blocks,
    )

# Helpers for A/B plan conversion and bootstrap CIs

def to_plan_in_from_domain(holds_domain: List[HoldDirective]) -> PlanIn:
    """
    Convert domain HoldDirective -> PlanIn (HoldDirectiveIn) using dicts
    so Pydantic can validate the response and the /apply_plan endpoint input.
    """
    return PlanIn(
        holds=[
            {"train_id": h.train_id, "block_id": h.block_id, "not_before_offset_sec": int(h.not_before_offset_sec)}
            for h in holds_domain
        ]
    )

def to_domain_holds_from_plan_in(plan_in: PlanIn) -> List[HoldDirective]:
    return [
        HoldDirective(
            train_id=h.train_id,
            block_id=h.block_id,
            not_before_offset_sec=int(h.not_before_offset_sec),
        )
        for h in plan_in.holds
    ]

def paired_bootstrap_ci(deltas: List[float], alpha: float = 0.05, B: int = 1000) -> Tuple[float, float]:
    """
    Simple percentile bootstrap for paired deltas.
    Deterministic RNG for reproducible CI in demos.
    """
    if not deltas:
        return (0.0, 0.0)
    rng = random.Random(12345)
    n = len(deltas)
    samples = []
    for _ in range(B):
        resample = [deltas[rng.randrange(n)] for __ in range(n)]
        samples.append(sum(resample) / n)
    samples.sort()
    lo_idx = max(0, int(math.floor((alpha / 2) * B)))
    hi_idx = min(B - 1, int(math.ceil((1 - alpha / 2) * B)) - 1)
    return (samples[lo_idx], samples[hi_idx])

# ---------- Batch optimization endpoints ----------

@app.post("/export_plan_input")
async def export_plan_input():
    if simulator.get_state_message().status != "COMPLETED":
        raise HTTPException(status_code=409, detail="Snapshot is only available after completion")
    data = build_optimizer_input(simulator)
    _ = OptimizerSnapshot(
        sim_time_iso=data["sim_time_iso"],
        params=SnapshotParams(**data["params"]),
        blocks=[BlockSnapshot(**b) for b in data["blocks"]],
        trains=[TrainSnapshot(**t) for t in data["trains"]],
        issues=[IssueSnapshot(**i) for i in data["issues"]],
    )
    return data

@app.post("/optimize_plan")
async def optimize_plan_endpoint(seed: int = 42):
    if simulator.get_state_message().status != "COMPLETED":
        raise HTTPException(status_code=409, detail="Optimization is only available after completion")
    data = build_optimizer_input(simulator)
    plan = optimize_from_sim(data, seed=seed)
    return {
        "holds": [
            {"train_id": h.train_id, "block_id": h.block_id, "not_before_offset_sec": h.not_before_offset_sec}
            for h in plan.holds
        ]
    }

@app.post("/apply_plan")
async def apply_plan_endpoint(plan_in: PlanIn):
    try:
        holds = to_domain_holds_from_plan_in(plan_in)
        simulator.apply_plan(Plan(holds=holds))
        await manager.broadcast({"type": "plan_applied", "holds_applied": len(holds)})
        return {"status": "success", "holds_applied": len(holds)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/rerun-optimized")
async def rerun_optimized(seed: int = 42, force: bool = False, trials: int = 1):
    """
    Perform paired A/B runs with common random numbers.
    - baseline: no plan
    - optimized: generated plan from current snapshot
    Returns original RerunResponse plus 'meta' with statistical details.
    """
    if simulator.get_state_message().status != "COMPLETED" and not force:
        raise HTTPException(status_code=409, detail="Rerun is only available after completion")

    # Build snapshot and plan
    data = build_optimizer_input(simulator)
    plan_domain = optimize_from_sim(data, seed=seed)  # returns Plan(list[HoldDirective])
    plan_in = to_plan_in_from_domain(plan_domain.holds)  # for response/UI

    # Multi-trial paired runs using seeds [seed, seed+1, ...]
    N = max(1, int(trials))
    seeds_used: List[int] = [seed + i for i in range(N)]

    baselines: List[RerunMetrics] = []
    optimizeds: List[RerunMetrics] = []
    delta_avg_list: List[float] = []
    delta_dur_list: List[float] = []

    domain_holds_for_run = to_domain_holds_from_plan_in(plan_in)

    for s in seeds_used:
        baseline = await run_to_completion(plan=None, seed=s)
        optimized = await run_to_completion(plan=Plan(holds=domain_holds_for_run), seed=s)
        baselines.append(baseline)
        optimizeds.append(optimized)
        delta_avg_list.append(float(baseline.avg_delay_min) - float(optimized.avg_delay_min))
        delta_dur_list.append(float(baseline.duration_sec) - float(optimized.duration_sec))

    def mean(xs: List[float]) -> float:
        return float(sum(xs) / max(1, len(xs)))

    avg_delta_mean = mean(delta_avg_list)
    dur_delta_mean = mean(delta_dur_list)
    ci_avg_lo, ci_avg_hi = paired_bootstrap_ci(delta_avg_list, alpha=0.05, B=1000)
    ci_dur_lo, ci_dur_hi = paired_bootstrap_ci(delta_dur_list, alpha=0.05, B=1000)

    # Use first trial’s detailed tables for readability in UI
    baseline_first = baselines[0]           # keep as RerunMetrics object
    optimized_first = optimizeds[0]         # keep as RerunMetrics object
    diff = diff_metrics(baseline_first, optimized_first)

    # Original response model (for backward compatibility)
    core = RerunResponse(
        baseline=baseline_first,
        optimized=optimized_first,
        plan=plan_in,
        diff=diff,
    )

    # Return enriched payload (dump to dicts only at the boundary)
    return {
        "baseline": core.baseline.model_dump(),
        "optimized": core.optimized.model_dump(),
        "plan": core.plan.model_dump(),
        "diff": core.diff.model_dump(),
        "meta": {
            "trials": N,
            "seeds_used": seeds_used,
            "holds_applied": len(plan_in.holds),
            "avg_delay_min_delta_mean": round(avg_delta_mean, 3),
            "avg_delay_min_delta_ci95": [round(ci_avg_lo, 3), round(ci_avg_hi, 3)],
            "duration_sec_delta_mean": round(dur_delta_mean, 3),
            "duration_sec_delta_ci95": [round(ci_dur_lo, 3), round(ci_dur_hi, 3)],
        },
    }

# Serve production build if present
if os.path.exists("frontend/dist"):
    app.mount("/static", StaticFiles(directory="frontend/dist"), name="static")

    @app.get("/")
    async def serve_frontend():
        return FileResponse("frontend/dist/index.html")

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "true").lower() in ("1", "true", "yes"),
    )
