from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import asyncio
import json
import uvicorn
from typing import List, Set
import os
from datetime import datetime

from simulation.simulator import RailwaySimulator
from simulation.schemas import StateMessage, EventMessage, ControlPayload, DelayInjection, BlockIssueInjection

app = FastAPI(title="Railway Control System", version="1.0.0")

# CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://localhost:5173",
        "http://127.0.0.1:3000", "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Adjustable tick pacing (seconds between loop iterations)
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

# Background tasks
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
    Background task: advance simulation and broadcast state+events until completion.
    Slowed via TICK_SLEEP_SEC so runs are easier to follow during demos.
    """
    while True:
        try:
            # If simulator exposes 'completed', honor it; else, infer from state.status
            if getattr(simulator, "completed", False):
                # Final snapshot
                final_state = simulator.get_state_message()
                await manager.broadcast(final_state.dict())
                break

            events = simulator.step()
            state_message = simulator.get_state_message()

            # If schema exposes status, check for completion and stop
            status = getattr(state_message, "status", "RUNNING")
            await manager.broadcast(state_message.dict())
            for ev in events:
                await manager.broadcast(ev.dict())

            if status == "COMPLETED":
                break

            await asyncio.sleep(TICK_SLEEP_SEC)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[simulation_loop] error: {e}")
            await asyncio.sleep(1.0)

async def heartbeat_loop():
    """
    Periodic keepalive so proxies/sockets don’t time out after completion.
    """
    while True:
        try:
            msg = {"type": "heartbeat", "ts": datetime.utcnow().isoformat()}
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
        # Initial snapshot on connect
        initial_state = simulator.get_state_message()
        await websocket.send_text(json.dumps(initial_state.dict()))
        # Passive receive loop with timeout to detect dead peers
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
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

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
        # On reset, if the loop stopped earlier, start it again
        if all(t.done() for t in app.state.bg_tasks if t is not None):
            app.state.bg_tasks.append(asyncio.create_task(simulation_loop()))
        # Push a fresh snapshot immediately
        state_message = simulator.get_state_message()
        await manager.broadcast(state_message.dict())
        return {"status": "success", "message": "Simulation reset"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Serve production build if present
if os.path.exists("frontend/dist"):
    app.mount("/static", StaticFiles(directory="frontend/dist"), name="static")
    @app.get("/")
    async def serve_frontend():
        return FileResponse("frontend/dist/index.html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
