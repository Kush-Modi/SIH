import math
import random
from typing import List, Dict, Tuple, Optional, Any
from collections import defaultdict

from ortools.sat.python import cp_model

from .plan import Plan, HoldDirective

# Priority rank used for soft tie-breaks if needed later.
PRIORITY_ORDER = {"EXPRESS": 0, "REGIONAL": 1, "FREIGHT": 2}


class TrainRouteBlock:
    def __init__(
        self,
        train_id: str,
        block_id: str,
        is_station: bool,
        travel_sec: int,
        dwell_sec: int,
        priority: str,
    ):
        self.train_id = train_id
        self.block_id = block_id
        self.is_station = is_station
        self.travel_sec = int(travel_sec)
        self.dwell_sec = int(dwell_sec)
        self.priority = priority


class DispatchOptimizer:
    def __init__(
        self,
        max_time_sec: int = 3600,
        headway_sec: int = 90,
        time_limit_sec: float = 1.5,
        num_workers: int = 1,
    ):
        self.max_time_sec = int(max_time_sec)
        self.headway_sec = int(max(0, headway_sec))
        self.time_limit_sec = float(max(0.1, time_limit_sec))
        self.num_workers = max(1, num_workers)

    def optimize(
        self,
        now_sec: int,
        routes: List[TrainRouteBlock],
        seed: Optional[int] = None,
    ) -> Dict[str, List[Tuple[int, int]]]:
        """
        CP-SAT interval model:
          - Interval per train-block segment (integer durations).
          - Precedence within each train route.
          - Pairwise headway disjunctions on shared blocks.
          - Minimize makespan for speed/robustness.
        Returns {train_id: [(start_sec, end_sec), ...]}.
        """
        model = cp_model.CpModel()

        intervals: Dict[Tuple[str, int], cp_model.IntervalVar] = {}
        starts: Dict[Tuple[str, int], cp_model.IntVar] = {}
        ends: Dict[Tuple[str, int], cp_model.IntVar] = {}
        blocks_usage: Dict[str, List[Tuple[str, int]]] = {}
        priority_int: Dict[Tuple[str, int], int] = {}

        # Create intervals
        for idx, trb in enumerate(routes):
            key = (trb.train_id, idx)
            duration = int(max(1, trb.dwell_sec if trb.is_station else trb.travel_sec))
            priority_int[key] = PRIORITY_ORDER.get(str(trb.priority).upper(), 3)

            s = model.NewIntVar(now_sec, now_sec + self.max_time_sec, f"s_{trb.train_id}_{idx}")
            e = model.NewIntVar(now_sec, now_sec + self.max_time_sec, f"e_{trb.train_id}_{idx}")
            itv = model.NewIntervalVar(s, duration, e, f"itv_{trb.train_id}_{idx}")

            starts[key] = s
            ends[key] = e
            intervals[key] = itv
            blocks_usage.setdefault(trb.block_id, []).append(key)

        # Precedence within each train (fix: group by train)
        by_train = defaultdict(list)
        for idx, trb in enumerate(routes):
            by_train[trb.train_id].append(idx)

        for tid, idxs in by_train.items():
            for i in range(len(idxs) - 1):
                cur = idxs[i]
                nxt = idxs[i + 1]
                model.Add(starts[(tid, nxt)] >= ends[(tid, cur)])

        # Headway disjunctions per block
        if self.headway_sec > 0:
            for block_id, keys in blocks_usage.items():
                n = len(keys)
                for i in range(n):
                    for j in range(i + 1, n):
                        k1 = keys[i]
                        k2 = keys[j]
                        o12 = model.NewBoolVar(f"o_{k1}_before_{k2}")
                        o21 = model.NewBoolVar(f"o_{k2}_before_{k1}")
                        model.Add(starts[k2] >= ends[k1] + self.headway_sec).OnlyEnforceIf(o12)
                        model.Add(starts[k1] >= ends[k2] + self.headway_sec).OnlyEnforceIf(o21)
                        model.AddBoolOr([o12, o21])
                        model.Add(o12 + o21 == 1)

        # Makespan objective
        makespan = model.NewIntVar(now_sec, now_sec + self.max_time_sec, "makespan")
        model.AddMaxEquality(makespan, [ends[k] for k in intervals.keys()])
        model.Minimize(makespan)

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_sec
        solver.parameters.num_search_workers = self.num_workers
        if seed is not None:
            try:
                solver.parameters.random_seed = int(seed)
            except Exception:
                pass

        status = solver.Solve(model)

        results: Dict[str, List[Tuple[int, int]]] = {}
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for key in intervals.keys():
                train_id, _ = key
                s_val = int(solver.Value(starts[key]))
                e_val = int(solver.Value(ends[key]))
                results.setdefault(train_id, []).append((s_val, e_val))
            # Sort by start time for consistency
            for tid in results:
                results[tid].sort(key=lambda x: x[0])
        return results


def optimize_from_sim(data: Dict[str, Any], seed: Optional[int] = None) -> Plan:
    """
    Build a minimal hold plan from a snapshot:
      - Build segments from each train’s current route index to end.
      - Solve once and take the first segment’s start for each train as a hold offset.
      - Store holds as offset seconds (relative to current sim_time).
    """
    blocks = {b["id"]: b for b in data.get("blocks", [])}
    trains = list(data.get("trains", []))
    params = dict(data.get("params", {}))

    dwell_sec_default = int(max(0, params.get("dwell_sec", 60)))
    max_time_sec = int(max(60, params.get("max_time_sec", 3600)))
    headway_sec = int(max(0, params.get("headway_sec", 90)))
    time_limit_sec = float(max(0.5, params.get("time_limit_sec", 1.5)))

    # Build train-block segments from each train's current position
    routes: List[TrainRouteBlock] = []
    for t in trains:
        train_id = str(t["id"])
        priority = str(t.get("priority", "REGIONAL"))
        route_list: List[str] = list(t.get("route", []))
        route_index = int(t.get("route_index", 0))
        if not route_list:
            continue

        start_idx = max(0, min(route_index, len(route_list) - 1))
        for idx in range(start_idx, len(route_list)):
            block_id = route_list[idx]
            b = blocks.get(block_id, {})
            is_station = bool(b.get("station", False))
            length_km = float(b.get("length_km", 1.0) or 1.0)
            speed_kmh = float(b.get("max_speed_kmh", 80.0) or 80.0)
            travel_sec = 0 if is_station else int(max(1, (length_km / max(speed_kmh, 1.0)) * 3600.0))
            dwell_sec = int(max(0, dwell_sec_default if is_station else 0))

            routes.append(
                TrainRouteBlock(
                    train_id=train_id,
                    block_id=block_id,
                    is_station=is_station,
                    travel_sec=travel_sec,
                    dwell_sec=dwell_sec,
                    priority=priority,
                )
            )

    # Solve once
    optimizer = DispatchOptimizer(
        max_time_sec=max_time_sec, headway_sec=headway_sec, time_limit_sec=time_limit_sec
    )
    schedule = optimizer.optimize(0, routes, seed=seed)

    # Build holds for the immediate next block only if delayed
    holds: List[HoldDirective] = []
    for t in trains:
        tid = str(t["id"])
        route = list(t.get("route", []))
        ridx = int(t.get("route_index", 0))
        if not route or ridx >= len(route) - 1:
            continue

        next_block_id = route[ridx + 1]
        per_train: List[Tuple[int, int]] = schedule.get(tid) or []
        if not per_train:
            continue

        start_sec = int(per_train[0][0])
        if start_sec > 0:  # only add a hold if optimizer delayed it
            holds.append(
                HoldDirective(
                    train_id=tid,
                    block_id=next_block_id,
                    not_before_offset_sec=start_sec,
                )
            )

    return Plan(holds=holds)
