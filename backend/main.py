from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import asyncio
import json
import uvicorn
from typing import List, Set, Dict, Any, Optional
import os
from datetime import datetime, timezone
import random

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

# Global simulator
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
app.state.bg_tasks: List[asyncio.Task] = []

@app.on_event("startup")
async def startup_event():
    await simulator.initialize()
    app.state.bg_tasks.append(asyncio.create_task(simulation_loop()))
    app.state.bg_tasks.append(asyncio.create_task(heartbeat_loop()))

@app.on_event("shutdown")
async def shutdown_event():
    for t in app.state.bg_tasks:
        t.cancel()
    await asyncio.gather(*app.state.bg_tasks, return_exceptions=True)
    app.state.bg_tasks.clear()

async def simulation_loop():
    """
    Advance simulation and broadcast state+events until completion,
    then push one final snapshot and stop.
    """
    while True:
        try:
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
            break
        except Exception as e:
            print(f"[simulation_loop] error: {e}")
            await asyncio.sleep(1.0)

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
        return {"status": "success", "message": "Parameters updated"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/inject/delay")
async def inject_delay(delay: DelayInjection):
    try:
        event = simulator.inject_delay(delay.train_id, delay.delay_minutes)
        return {"status": "success", "event": event.dict()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/inject/block-issue")
async def inject_block_issue(issue: BlockIssueInjection):
    try:
        event = simulator.set_block_issue(issue.block_id, issue.blocked)
        return {"status": "success", "event": event.dict()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/reset")
async def reset_simulation():
    try:
        simulator.reset()
        state_message = simulator.get_state_message()
        await manager.broadcast(state_message.dict())
        # If the sim loop already ended, restart it
        app.state.bg_tasks.append(asyncio.create_task(simulation_loop()))
        return {"status": "success", "message": "Simulation reset"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------- Batch optimization helpers ----------

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
        delta_duration_sec=delta_dur,
        trains=sorted(trains, key=lambda x: x.delta_delay_min, reverse=True),
        blocks=blocks,
    )

def seed_repro(seed: int = 42) -> None:
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:
        pass
    random.seed(seed)

async def run_to_completion(plan: Optional[Plan] = None, seed: int = 42) -> RerunMetrics:
    """
    Reset, optionally apply a plan (offset holds anchored to the new sim_time),
    then step until simulator.completed with a safety cap driven by the simulator.
    """
    seed_repro(seed)
    simulator.reset()
    start_iso = simulator.sim_time.replace(tzinfo=timezone.utc).isoformat()
    if plan:
        simulator.apply_plan(plan)
    # Step until completion
    ticks = 0
    max_ticks = 20000
    while not simulator.completed and ticks < max_ticks:
        simulator.step()
        ticks += 1
    return compute_metrics(simulator, start_iso)

# ---------- Batch optimization endpoints ----------

@app.post("/export_plan_input")
async def export_plan_input():
    if not simulator.completed:
        raise HTTPException(status_code=409, detail="Snapshot is only available after completion")
    data = build_optimizer_input(simulator)
    # Validate shape via Pydantic model for sanity (optional)
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
    if not simulator.completed:
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
        holds = [
            HoldDirective(
                train_id=h.train_id,
                block_id=h.block_id,
                not_before_offset_sec=int(h.not_before_offset_sec),
            ) for h in plan_in.holds
        ]
        simulator.apply_plan(Plan(holds=holds))
        return {"status": "success", "holds_applied": len(holds)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/rerun-optimized")
async def rerun_optimized(seed: int = 42, force: bool = False):
    # Allow forcing a clean A/B pair even before completion; default remains "after completion only"
    if not simulator.completed and not force:
        raise HTTPException(status_code=409, detail="Rerun is only available after completion")
    # Build snapshot and plan from current memory (completed or not)
    data = build_optimizer_input(simulator)
    plan = optimize_from_sim(data, seed=seed)
    plan_in = {
        "holds": [
            {"train_id": h.train_id, "block_id": h.block_id, "not_before_offset_sec": h.not_before_offset_sec}
            for h in plan.holds
        ]
    }
    # A/B pair with the same seed
    baseline = await run_to_completion(plan=None, seed=seed)
    optimized = await run_to_completion(plan=Plan(holds=[HoldDirective(**hd) for hd in plan_in["holds"]]), seed=seed)
    diff = diff_metrics(baseline, optimized)
    return RerunResponse(
        baseline=baseline,
        optimized=optimized,
        plan=PlanIn(**plan_in),
        diff=diff,
    )

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
