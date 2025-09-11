import json
import math
import random
from typing import List, Dict, Tuple
from ortools.sat.python import cp_model

PRIORITY_ORDER = {'EXPRESS': 0, 'REGIONAL': 1, 'FREIGHT': 2}

class TrainRouteBlock:
    def __init__(self, train_id: str, block_id: str, is_station: bool,
                 travel_sec: int, dwell_sec: int, priority: str):
        self.train_id = train_id
        self.block_id = block_id
        self.is_station = is_station
        self.travel_sec = travel_sec
        self.dwell_sec = dwell_sec
        self.priority = priority


class DispatchOptimizer:
    def __init__(self,
                 max_time_sec: int = 3600,
                 headway_sec: int = 90,
                 time_limit_sec: float = 1.5):
        self.max_time_sec = max_time_sec
        self.headway_sec = headway_sec
        self.time_limit_sec = time_limit_sec

    def optimize(self,
                 now_sec: int,
                 routes: List[TrainRouteBlock]) -> Dict[str, List[Tuple[int,int]]]:
        model = cp_model.CpModel()
        intervals = dict()
        starts = dict()
        ends = dict()
        blocks_usage: Dict[str, List[Tuple[str,int]]] = dict()
        durations = dict()
        priority_int = dict()

        for idx, trb in enumerate(routes):
            key = (trb.train_id, idx)
            duration = trb.dwell_sec if trb.is_station else trb.travel_sec
            durations[key] = duration
            priority_int[key] = PRIORITY_ORDER.get(trb.priority.upper(), 3)
            start_var = model.NewIntVar(now_sec, now_sec + self.max_time_sec, f'start_{trb.train_id}_{idx}')
            end_var = model.NewIntVar(now_sec, now_sec + self.max_time_sec, f'end_{trb.train_id}_{idx}')
            interval_var = model.NewIntervalVar(start_var, duration, end_var, f'interval_{trb.train_id}_{idx}')
            starts[key] = start_var
            ends[key] = end_var
            intervals[key] = interval_var
            blocks_usage.setdefault(trb.block_id, []).append(key)

        # Precedence in train routes
        for i in range(len(routes)-1):
            if routes[i].train_id == routes[i+1].train_id:
                model.Add(starts[(routes[i+1].train_id, i+1)] >= ends[(routes[i].train_id, i)])

        # No-overlap with headway on shared blocks
        for block_id, keys in blocks_usage.items():
            for i in range(len(keys)):
                for j in range(i+1, len(keys)):
                    key1 = keys[i]
                    key2 = keys[j]
                    order12 = model.NewBoolVar(f'order_{key1}_before_{key2}')
                    order21 = model.NewBoolVar(f'order_{key2}_before_{key1}')
                    model.Add(starts[key2] >= ends[key1] + self.headway_sec).OnlyEnforceIf(order12)
                    model.Add(starts[key1] >= ends[key2] + self.headway_sec).OnlyEnforceIf(order21)
                    model.AddBoolOr([order12, order21])
                    model.Add(order12 + order21 == 1)
                    pr1 = priority_int[key1]
                    pr2 = priority_int[key2]
                    if pr1 != pr2:
                        penalty_var = model.NewBoolVar(f'penalty_{key1}_{key2}')
                        if pr1 < pr2:
                            model.Add(order21 == 1).OnlyEnforceIf(penalty_var)
                            model.Add(order21 == 0).OnlyEnforceIf(penalty_var.Not())
                        else:
                            model.Add(order12 == 1).OnlyEnforceIf(penalty_var)
                            model.Add(order12 == 0).OnlyEnforceIf(penalty_var.Not())

        makespan = model.NewIntVar(now_sec, now_sec + self.max_time_sec, 'makespan')
        model.AddMaxEquality(makespan, [ends[key] for key in intervals.keys()])
        model.Minimize(makespan)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_sec
        status = solver.Solve(model)

        results = dict()
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for key in intervals.keys():
                train_id, idx = key
                start_t = solver.Value(starts[key])
                end_t = solver.Value(ends[key])
                results.setdefault(train_id, []).append((start_t, end_t))
        else:
            print("No feasible solution found")

        return results

def load_blocks(blocks_file: str):
    with open(blocks_file, 'r') as f:
        blocks = json.load(f)
    return {b['id']: b for b in blocks}

def load_train_routes(routes_file: str):
    with open(routes_file, 'r') as f:
        return json.load(f)

def generate_train_route_blocks(blocks_dict, train_route, train_id, train_priority='REGIONAL', default_speed_kmh=80, default_dwell_sec=60):
    tr_blocks = []
    for idx, block_id in enumerate(train_route):
        block = blocks_dict[block_id]
        is_station = block.get('station_id') is not None

        # Handle length_km
        length_km = block.get('length_km', None)
        if length_km is None or not isinstance(length_km, (int, float)) or math.isnan(length_km) or length_km <= 0:
            length_km = random.randint(1, 5)  # random sensible length between 1 and 5 km

        # Handle speed_kmh
        speed_kmh = block.get('max_speed_kmh', None)
        if speed_kmh is None or not isinstance(speed_kmh, (int, float)) or math.isnan(speed_kmh) or speed_kmh <= 0:
            speed_kmh = random.choice([60, 80, 100, 120])  # random sensible speed

        # Calculate travel and dwell times
        travel_sec = int((length_km / speed_kmh) * 3600) if not is_station else 0
        dwell_sec = default_dwell_sec if is_station else 0

        trb = TrainRouteBlock(
            train_id=train_id,
            block_id=block_id,
            is_station=is_station,
            travel_sec=travel_sec,
            dwell_sec=dwell_sec,
            priority=train_priority
        )
        tr_blocks.append(trb)
    return tr_blocks

if __name__ == '__main__':
    blocks_dict = load_blocks('blocks.json')
    train_routes = load_train_routes('train_routes.json')
    priorities = ['EXPRESS', 'REGIONAL', 'FREIGHT']
    all_train_route_blocks = []
    for train in train_routes:
        train_id = train['id']
        priority = random.choice(priorities)  # replace with real priority mapping if available
        trbs = generate_train_route_blocks(blocks_dict, train['route'], train_id, train_priority=priority)
        all_train_route_blocks.extend(trbs)
    optimizer = DispatchOptimizer(max_time_sec=3600, headway_sec=90, time_limit_sec=1.5)
    now_sec = 0  # simulation start time or current sim time
    plan = optimizer.optimize(now_sec, all_train_route_blocks)
    for train_id, schedule in plan.items():
        print(f"Train {train_id} schedule:")
        for idx, (start, end) in enumerate(schedule):
            print(f"  Block {idx+1}: entry at {start}s, exit at {end}s")
