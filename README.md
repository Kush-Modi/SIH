# SIH Railway Control System – Phase 1

A live, tick-based railway corridor simulator with a minimalist web UI, streaming updates via WebSockets. Phase 1 focuses on believable movement and live visualization (no optimizer yet).

## Contents
- Overview
- Architecture
- Features (Phase 1)
- Tech stack
- Quick start (Windows/macOS/Linux)
- Development workflows
- API Reference (REST + WebSocket)
- Topology format
- Frontend UI guide
- Troubleshooting
- Roadmap (Phase 2+)

---

## Overview
This repository contains a FastAPI backend that runs a discrete-time in‑memory simulation of trains moving block‑by‑block over a small corridor, and a React + TypeScript frontend that renders an SVG schematic of stations/blocks with live train markers, KPIs, and simple controls for headway/dwell and disruption injection.

- No database in Phase 1; all state is ephemeral in memory
- Live broadcast every 250 ms over WebSocket
- Adjustable dwell and headway; inject delays and block failures via REST

## Architecture
```
SIH/
├── backend/
│   ├── main.py                      # FastAPI app, WebSocket, REST, background loop
│   ├── requirements.txt             # Python dependencies
│   └── simulation/
│       ├── simulator.py             # Tick-based simulator and injectors
│       ├── schemas.py               # Pydantic models (state, events, control)
│       └── topology.json            # Stations, blocks, adjacency & defaults
├── frontend/
│   ├── index.html                   # Vite entry
│   ├── package.json                 # Frontend dependencies
│   ├── vite.config.ts               # Dev/build config
│   └── src/
│       ├── App.tsx                  # Layout, data flow, events panel
│       ├── App.css
│       ├── main.tsx                 # Bootstraps React + ErrorBoundary
│       ├── ws/client.ts             # WebSocket hook (auto-reconnect)
│       ├── types.ts                 # TS interfaces aligned with backend models
│       └── components/
│           ├── TrackView.tsx/.css   # SVG schematic + trains
│           ├── ControlPanel.tsx/.css# Controls for params and injectors
│           ├── KPIBar.tsx/.css      # KPIs and connection status
│           └── NarrativePanel.tsx/.css # Plain-English status of trains
├── start_backend.py                 # Helper script to create venv & run backend
├── start_frontend.bat/.sh           # Convenience scripts to run FE
└── README.md                        # This file
```

## Features (Phase 1)
- Discrete-time simulation with configurable tick → sim time ratio
- Headway and dwell logic; block occupancy and simple priority tie-breaker
- Delay injection and block failure/clear via REST
- Live state + events over WebSocket every 250 ms
- SVG schematic with trains, stations, blocks; clean labels and KPIs
- Restart simulation (clears delays and reseeds trains)

## Tech stack
- Backend: Python 3.10+, FastAPI, Uvicorn, Pydantic v2
- Frontend: React 18, TypeScript, Vite

## Quick start
### Prerequisites
- Python 3.10+
- Node.js 18+ (includes npm)
- Git

### 1) Backend (FastAPI)
```
cd backend
python -m venv venv
venv\Scripts\activate               # Windows
# source venv/bin/activate           # macOS/Linux
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
Backend will run at `http://localhost:8000` and WebSocket at `ws://localhost:8000/ws`.

### 2) Frontend (Vite + React)
```
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173` in a browser.

### 3) Environment variables (optional)
Create `frontend/.env` if you want to override defaults:
```
VITE_WS_URL=ws://localhost:8000/ws
VITE_API_URL=http://localhost:8000
```

## Development workflows
- Start both servers as above. On code edits, hot reload will update.
- To view live events: use the “Recent Events” panel in the UI.
- To restart the simulation: use “Restart Simulation” in the Control Panel. Delays are cleared and trains reseeded.

### Scripts
- `start_backend.py` creates a `venv`, installs dependencies, and starts Uvicorn.
- `start_frontend.bat` / `start_frontend.sh` install deps and run Vite.

## API Reference
### REST
- `GET /health` → `{ status, timestamp }`
- `GET /state` → State snapshot (see models below)
- `POST /control` → Update parameters
  - Payload: `{ headway_sec?, dwell_sec?, energy_stop_penalty?, simulation_speed? }`
- `POST /inject/delay` → Inject train delay
  - Payload: `{ train_id, delay_minutes }`
- `POST /inject/block-issue` → Set or clear an issue on a block
  - Payload: `{ block_id, blocked }`
- `POST /reset` → Reset to initial state (clears delays and issues)

### WebSocket `/ws`
Server broadcasts a state message roughly every 250 ms, and event messages as they occur.

#### State message (abridged)
```
{
  "type": "state",
  "sim_time": "2025-09-09T17:30:00Z",
  "blocks": [ { "id": "B3", "occupied_by": "T7" | null, "issue": null | { "type": "BLOCKED", "since": "..." } } ],
  "trains": [
    {
      "id": "T7", "name": "EXP-12045", "priority": "EXPRESS",
      "at_block": "B3", "next_block": "B4", "eta_next": "...",
      "delay_min": 0, "dwell_sec_remaining": 0, "speed_kmh": 80.0,
      "entered_block_at": "...", "will_exit_at": "..."   // interpolation hints
    }
  ],
  "kpis": { "avg_delay_min": 0.0, "trains_on_line": 8 }
}
```

#### Event message
```
{
  "type": "event",
  "event_id": "E123-5",          // unique (tick + counter)
  "event_kind": "TRAIN_ARRIVED|...",
  "block_id": "B4", "train_id": "T7",
  "timestamp": "...",
  "note": "..."
}
```

## Topology format
`backend/simulation/topology.json` defines stations and blocks with adjacency and defaults (`default_headway_sec`, `default_dwell_sec`, `default_speed_kmh`). Adjust this file to change the corridor.

## Frontend UI guide
- KPI Bar (top): simulation time, average delay, trains active, blocked blocks, connection status.
- Track View (left):
  - Mainline emphasized; loops and sidings are toggleable and faint by default.
  - Trains have clear direction arrows and readable labels. Delay badges appear when non‑zero.
  - “View Options” panel lets you toggle loops, sidings, and block labels.
- Control Panel (right): adjust headway/dwell/speed; inject delays; set/clear block issues; restart simulation.
- Narrative Panel (below map): plain‑English sentences describing each train’s current status.

## Troubleshooting
- WebSocket not connecting: ensure backend is running on port 8000 and CORS is enabled (it is, for localhost:5173 and :3000).
- No movement: verify state messages are received; check browser console; ensure `entered_block_at`/`will_exit_at` present.
- Styles/UI misaligned: `npm run dev` rebuilds Tailwind/Vite (we use plain CSS); hard refresh the browser.
- Windows PowerShell `&&` issue: use `;` or separate commands.

## Roadmap (Phase 2+)
- Optimization solver (CP-SAT) to schedule movements given constraints
- Persistence and multi‑instance (Redis/DB)
- Conflict resolution and advanced routing
- Energy‑aware speed profiles
- Rich operator tools & metrics

---

## License
MIT
