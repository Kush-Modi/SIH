from __future__ import annotations

from typing import Dict, Any, List
from datetime import datetime, timezone

from simulation.simulator import RailwaySimulator


def _iso_utc(dt: datetime) -> str:
    """
    Return ISO-8601 string in UTC with 'Z' suffix.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    # keep milliseconds if provided; normalize +00:00 -> Z
    s = dt.isoformat()
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s


def _to_str(x: Any) -> str:
    if x is None:
        return ""
    try:
        # enums or objects with .name/.value
        if hasattr(x, "name"):
            return str(getattr(x, "name"))
        if hasattr(x, "value"):
            return str(getattr(x, "value"))
    except Exception:
        pass
    return str(x)


def build_optimizer_input(sim: RailwaySimulator) -> Dict[str, Any]:
    """
    Assemble optimizer input directly from live simulator state and loaded topology.
    Shapes match OptimizerSnapshot for validation downstream.
    """
    if sim.topology is None:
        raise ValueError("Simulator topology not initialized")

    # Blocks (from topology)
    blocks: List[Dict[str, Any]] = []
    # Build a quick set of station block ids from runtime blocks if topology lacks flags
    station_block_ids = set()
    for bid, b in sim.blocks.items():
        if getattr(b, "station_id", None):
            station_block_ids.add(bid)

    for b in sim.topology.blocks:
        bid = _to_str(getattr(b, "id"))
        name = _to_str(getattr(b, "name") or bid)
        length_km = float(getattr(b, "length_km", 1.0))
        max_speed_kmh = float(getattr(b, "max_speed_kmh", 80.0))
        # Derive station flag from topology or runtime blocks
        topo_station = getattr(b, "station_id", None) is not None
        station = bool(topo_station or (bid in station_block_ids))
        blocks.append({
            "id": bid,
            "name": name,
            "length_km": length_km,
            "max_speed_kmh": max_speed_kmh,
            "station": station,
        })

    # Trains (from runtime)
    trains: List[Dict[str, Any]] = []
    for t in sim.trains.values():
        tid = _to_str(getattr(t, "id"))
        name = _to_str(getattr(t, "name") or tid)
        priority = _to_str(getattr(t, "priority"))
        # Route can contain strings or objects; normalize to string ids
        raw_route = list(getattr(t, "route", []))
        route_ids = []
        for r in raw_route:
            if hasattr(r, "id"):
                route_ids.append(_to_str(getattr(r, "id")))
            else:
                route_ids.append(_to_str(r))
        at_block = _to_str(getattr(t, "current_block", None)) or route_ids if route_ids else ""
        route_index = int(getattr(t, "route_index", 0))
        trains.append({
            "id": tid,
            "name": name,
            "priority": priority,
            "route": route_ids,
            "at_block": at_block,
            "route_index": route_index,
        })

    # Issues (from runtime)
    issues: List[Dict[str, Any]] = []
    for b in sim.blocks.values():
        issue = getattr(b, "issue", None)
        if issue:
            since = getattr(b, "issue_since", None) or sim.sim_time
            issues.append({
                "block_id": _to_str(getattr(b, "id")),
                "type": _to_str(issue.get("type", "BLOCKED")),
                "since_iso": _iso_utc(since),
            })

    # Parameters (bounded defaults; avoid missing attrs breaking the adapter)
    default_speed_kmh = float(getattr(sim.topology, "default_speed_kmh", 80.0))
    headway_sec = int(getattr(sim, "headway_sec", 120))
    dwell_sec = int(getattr(sim, "dwell_sec", 60))
    max_time_sec = int(getattr(sim, "max_time_sec", 3600))
    time_limit_sec = float(getattr(sim, "time_limit_sec", 1.5))

    params = {
        "headway_sec": max(0, headway_sec),
        "dwell_sec": max(0, dwell_sec),
        "default_speed_kmh": max(1.0, default_speed_kmh),
        "max_time_sec": max(60, max_time_sec),
        "time_limit_sec": max(0.01, time_limit_sec),
    }

    # Derived per-train route block details for export convenience
    # Build quick lookup for blocks and station flag
    blocks_by_id: Dict[str, Dict[str, Any]] = {str(b["id"]): b for b in blocks}
    train_route_blocks: List[Dict[str, Any]] = []
    dwell_sec_default = int(params["dwell_sec"])  # used at stations only

    for t in trains:
        train_id = str(t.get("id"))
        priority = str(t.get("priority", "REGIONAL"))
        route_list: List[str] = [str(x) for x in t.get("route", [])]
        route_index = int(t.get("route_index", 0))
        if not route_list:
            continue
        # Clamp starting index
        start_idx = max(0, min(route_index, len(route_list) - 1))
        for idx in range(start_idx, len(route_list)):
            block_id = str(route_list[idx])
            b = blocks_by_id.get(block_id, {})
            is_station = bool(b.get("station", False))
            length_km = float(b.get("length_km", 1.0) or 1.0)
            speed_kmh = float(b.get("max_speed_kmh", 80.0) or 80.0)
            # Travel time for track blocks; at least 1s if not station, 0s at station
            travel_sec = 0 if is_station else int(max(1, (length_km / max(speed_kmh, 1.0)) * 3600.0))
            # Dwell time only at stations
            dwell_this = int(max(0, dwell_sec_default if is_station else 0))

            train_route_blocks.append({
                "train_id": train_id,
                "block_id": block_id,
                "is_station": is_station,
                "travel_sec": travel_sec,
                "dwell_sec": dwell_this,
                "priority": priority,
            })

    return {
        "sim_time_iso": _iso_utc(sim.sim_time),
        "params": params,
        "blocks": blocks,
        "trains": trains,
        "issues": issues,
        "train_route_blocks": train_route_blocks,
    }
