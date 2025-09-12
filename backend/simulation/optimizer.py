# optimizer.py
from __future__ import annotations

import math
import random
from typing import List, Dict, Tuple, Optional, Any
from collections import defaultdict

from ortools.sat.python import cp_model

from .plan import Plan, HoldDirective

# Priority rank used for soft tie-breaks if needed later.
# We'll convert these to integer weights (higher => more important)
PRIORITY_WEIGHTS = {"EXPRESS": 100, "REGIONAL": 50, "FREIGHT": 10}


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
          - Objective: minimize weighted completion times (by priority), with a small makespan tie-breaker.
        Returns {train_id: [(start_sec, end_sec), ...]}.
        """
        model = cp_model.CpModel()

        intervals: Dict[Tuple[str, int], cp_model.IntervalVar] = {}
        starts: Dict[Tuple[str, int], cp_model.IntVar] = {}
        ends: Dict[Tuple[str, int], cp_model.IntVar] = {}
        blocks_usage: Dict[str, List[Tuple[str, int]]] = {}
        weights: Dict[Tuple[str, int], int] = {}

        # Domain bounds
        LB = now_sec
        UB = now_sec + self.max_time_sec

        # Create intervals
        for idx, trb in enumerate(routes):
            key = (trb.train_id, idx)
            # Duration: travel or dwell. enforce a realistic minimum of 10s for travel, 5s for dwell
            if trb.is_station:
                duration = max(5, trb.dwell_sec)
            else:
                duration = max(10, trb.travel_sec)
            s = model.NewIntVar(LB, UB, f"s_{trb.train_id}_{idx}")
            e = model.NewIntVar(LB, UB, f"e_{trb.train_id}_{idx}")
            itv = model.NewIntervalVar(s, duration, e, f"itv_{trb.train_id}_{idx}")

            starts[key] = s
            ends[key] = e
            intervals[key] = itv
            blocks_usage.setdefault(trb.block_id, []).append(key)

            # priority weight
            weights[key] = PRIORITY_WEIGHTS.get(str(trb.priority).upper(), 20)

        # Group indices by train to add precedence in route order
        by_train = defaultdict(list)
        for idx, trb in enumerate(routes):
            by_train[trb.train_id].append(idx)

        for tid, idxs in by_train.items():
            for i in range(len(idxs) - 1):
                cur = idxs[i]
                nxt = idxs[i + 1]
                model.Add(starts[(tid, nxt)] >= ends[(tid, cur)])

        # Headway disjunctions per block (pairwise ordering)
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
                        # at least one ordering must hold
                        model.AddBoolOr([o12, o21])
                        model.Add(o12 + o21 == 1)

        # Objective: weighted sum of completion times (ends), where higher-priority trains have larger weight.
        # Build linear expression: sum(weight * end)
        obj_terms = []
        for key, end_var in ends.items():
            w = weights.get(key, 20)
            obj_terms.append((w, end_var))

        # We cannot pass a list of weighted vars directly: build an intermediate linear expression manually
        # CP-SAT Minimize accepts linearExpr. We'll create a helper IntVar for weighted sum (approx).
        # To keep integer bounds manageable, we normalize by dividing weights by gcd if necessary.
        # Simpler: create a single objective linear expression directly.
        weighted_ends = []
        for w, v in obj_terms:
            weighted_ends.append(v * w)  # OR-Tools supports multiplication of IntVar by int in linear expressions

        # As a secondary tie-breaker, minimize makespan
        makespan = model.NewIntVar(LB, UB, "makespan")
        model.AddMaxEquality(makespan, [ends[k] for k in ends.keys()])

        # Primary: weighted sum of ends; Secondary: makespan (small weight)
        # Use two-phase trick: minimize weighted_sum * big + makespan
        # Choose a big multiplier greater than possible makespan range
        BIG = (self.max_time_sec + 1000)
        # Create an IntVar for weighted_sum (sum will be within BIG * UB roughly; CP-SAT supports large ints)
        # Directly add weighted objective: model.Minimize(sum(weight * end) * BIG + makespan)
        linear_sum = sum([w * ends[k] for k, w in zip(list(ends.keys()), [weights[k] for k in ends.keys()])])
        # Note: the above builds the same mapping; if OR-Tools complains, alternatively build a list comprehension.

        # Because cp_model.Minimize accepts linear expressions, implement as:
        model.Minimize(linear_sum * BIG + makespan)

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
      - Build segments from each train’s next block (skip current block) to end.
      - Solve once and take the first segment’s computed start for each train as a hold offset.
      - Store holds as offset seconds (relative to current sim_time).
    """
    blocks = {b["id"]: b for b in data.get("blocks", [])}
    trains = list(data.get("trains", []))
    params = dict(data.get("params", {}))

    dwell_sec_default = int(max(0, params.get("dwell_sec", 60)))
    max_time_sec = int(max(60, params.get("max_time_sec", 3600)))
    headway_sec = int(max(0, params.get("headway_sec", 90)))
    time_limit_sec = float(max(0.5, params.get("time_limit_sec", 1.5)))

    # Build train-block segments from each train's next_block onward (do NOT re-schedule the block the train currently occupies)
    routes: List[TrainRouteBlock] = []
    train_to_first_segment_index: Dict[str, int] = {}  # map train -> index in routes of its first scheduled segment
    for t in trains:
        train_id = str(t["id"])
        priority = str(t.get("priority", "REGIONAL"))
        route_list: List[str] = list(t.get("route", []))
        route_index = int(t.get("route_index", 0))

        # start scheduling from the *next* block (if available)
        start_idx = route_index + 1
        if start_idx >= len(route_list):
            # nothing to schedule (train at final block)
            continue

        train_to_first_segment_index[train_id] = len(routes)
        for idx in range(start_idx, len(route_list)):
            block_id = route_list[idx]
            b = blocks.get(block_id, {})
            is_station = bool(b.get("station", False))
            length_km = float(b.get("length_km", 1.0) or 1.0)
            speed_kmh = float(b.get("max_speed_kmh", params.get("default_speed_kmh", 80.0)) or params.get("default_speed_kmh", 80.0))
            # realistic travel sec (enforce minimum)
            travel_sec = 0 if is_station else int(max(10, (length_km / max(speed_kmh, 1.0)) * 3600.0))
            dwell_sec = int(max(1, dwell_sec_default if is_station else 0))

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

    # Build holds for the immediate next block only if optimizer delays it (start_sec > 0)
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
            # optimizer left no future segments - nothing to hold
            continue

        # The optimizer schedules the *next* block as the first per_train entry.
        start_sec = int(per_train[0][0])
        # If scheduled start is > 0 it implies optimizer suggested delaying entry to next_block by `start_sec`
        # Create a hold directive anchored to current sim_time with that offset.
        if start_sec > 0:
            holds.append(
                HoldDirective(
                    train_id=tid,
                    block_id=next_block_id,
                    not_before_offset_sec=start_sec,
                )
            )

    return Plan(holds=holds)
