from __future__ import annotations

from typing import Dict, Any, List
from datetime import datetime, timezone

from simulation.simulator import RailwaySimulator


def _iso_utc(dt: datetime) -> str:
    """
    Return ISO-8601 string in UTC with 'Z' suffix.
    """
    if dt.tzinfo is None:
        # Explicitly assume UTC if naive datetime
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def build_optimizer_input(sim: RailwaySimulator) -> Dict[str, Any]:
    """
    Assemble optimizer input directly from live simulator state and loaded topology.
    Shapes match OptimizerSnapshot for validation downstream.
    """
    if sim.topology is None:
        raise ValueError("Simulator topology not initialized")

    # Blocks
    blocks: List[Dict[str, Any]] = []
    for b in sim.topology.blocks:
        blocks.append({
            "id": b.id,
            "name": getattr(b, "name", str(b.id)),
            "length_km": float(getattr(b, "length_km", 1.0)),
            "max_speed_kmh": float(getattr(b, "max_speed_kmh", 80.0)),
            "station": bool(getattr(b, "station_id", None) is not None),
        })

    # Trains
    trains: List[Dict[str, Any]] = []
    for t in sim.trains.values():
        priority = None
        if hasattr(t.priority, "name"):
            priority = t.priority.name
        elif hasattr(t.priority, "value"):
            priority = t.priority.value
        else:
            priority = str(t.priority)

        route_ids = [blk.id if hasattr(blk, "id") else blk for blk in t.route]

        trains.append({
            "id": str(t.id),
            "name": getattr(t, "name", str(t.id)),
            "priority": str(priority),
            "route": route_ids,
            "at_block": getattr(t, "current_block", None),
            "route_index": int(getattr(t, "route_index", 0)),
        })

    # Issues
    issues: List[Dict[str, Any]] = []
    for b in sim.blocks.values():
        if getattr(b, "issue", None):
            since = getattr(b, "issue_since", None) or sim.sim_time
            issues.append({
                "block_id": b.id,
                "type": b.issue.get("type", "BLOCKED"),
                "since_iso": _iso_utc(since),
            })

    # Parameters
    default_speed_kmh = float(getattr(sim.topology, "default_speed_kmh", 80.0))
    params = {
        "headway_sec": int(getattr(sim, "headway_sec", 90)),
        "dwell_sec": int(getattr(sim, "dwell_sec", 60)),
        "default_speed_kmh": default_speed_kmh,
        "max_time_sec": int(getattr(sim, "max_time_sec", 3600)),
        "time_limit_sec": float(getattr(sim, "time_limit_sec", 1.5)),
    }

    return {
        "sim_time_iso": _iso_utc(sim.sim_time),
        "params": params,
        "blocks": blocks,
        "trains": trains,
        "issues": issues,
    }
