from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Any


def _ensure_aware_utc(dt: datetime) -> datetime:
    """
    Return a timezone-aware UTC datetime.
    Accepts naive datetimes and assumes they are UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class HoldDirective:
    """
    Hold a specific train from entering a specific block until
    sim_time + not_before_offset_sec (computed when applying the plan).

    Fields:
      - train_id: ID string of the train to hold
      - block_id: ID string of the block where the hold applies (next target block)
      - not_before_offset_sec: integer seconds offset relative to the snapshot sim_time
    """
    train_id: str
    block_id: str
    not_before_offset_sec: int  # offset (seconds) relative to snapshot sim_time

    def __post_init__(self) -> None:
        if not self.train_id or not isinstance(self.train_id, str):
            raise ValueError("HoldDirective.train_id must be a non-empty string")
        if not self.block_id or not isinstance(self.block_id, str):
            raise ValueError("HoldDirective.block_id must be a non-empty string")
        if not isinstance(self.not_before_offset_sec, int):
            raise ValueError("HoldDirective.not_before_offset_sec must be an int")
        if self.not_before_offset_sec < 0:
            raise ValueError("HoldDirective.not_before_offset_sec must be >= 0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "train_id": self.train_id,
            "block_id": self.block_id,
            "not_before_offset_sec": self.not_before_offset_sec,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> HoldDirective:
        return HoldDirective(
            train_id=str(d.get("train_id", "")),
            block_id=str(d.get("block_id", "")),
            not_before_offset_sec=int(d.get("not_before_offset_sec", 0)),
        )


@dataclass(slots=True)
class Plan:
    """
    A collection of hold directives produced by the optimizer.
    Holds are stored as offsets relative to the snapshot sim_time so they can
    be anchored to the simulator's current sim_time at apply time.

    Typical usage:
      idx = plan.to_index(sim.sim_time)  # {(train_id, block_id): absolute_dt}
      # store idx in simulator and gate movement against it
    """
    holds: List[HoldDirective] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.holds

    def to_index(self, sim_time: datetime) -> Dict[Tuple[str, str], datetime]:
        """
        Convert relative offsets to absolute datetimes using the provided sim_time
        (must be simulation time for the run where the plan is applied).
        Returns a fast-lookup index: {(train_id, block_id): absolute_datetime}
        """
        base = _ensure_aware_utc(sim_time)
        index: Dict[Tuple[str, str], datetime] = {}
        for h in self.holds:
            when = base + timedelta(seconds=h.not_before_offset_sec)
            index[(h.train_id, h.block_id)] = when
        return index

    def to_absolute_holds(self, sim_time: datetime) -> List[Dict[str, str]]:
        """
        Produce a JSON-friendly absolute representation (useful for logs/inspection):
        [{train_id, block_id, not_before_iso}]
        """
        base = _ensure_aware_utc(sim_time)
        out: List[Dict[str, str]] = []
        for h in self.holds:
            when = base + timedelta(seconds=h.not_before_offset_sec)
            not_before_iso = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            out.append(
                {
                    "train_id": h.train_id,
                    "block_id": h.block_id,
                    "not_before_iso": not_before_iso,
                }
            )
        return out

    def merged(self) -> Plan:
        """
        Deduplicate holds by (train_id, block_id), keeping the latest (max offset)
        to avoid contradictory instructions. Returns a new Plan.
        """
        best: Dict[Tuple[str, str], HoldDirective] = {}
        for h in self.holds:
            k = (h.train_id, h.block_id)
            if k not in best or h.not_before_offset_sec > best[k].not_before_offset_sec:
                best[k] = h
        return Plan(holds=list(best.values()))

    def to_dict(self) -> Dict[str, Any]:
        return {"holds": [h.to_dict() for h in self.holds]}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> Plan:
        holds_raw = d.get("holds", []) or []
        holds = [HoldDirective.from_dict(hr) for hr in holds_raw]
        return Plan(holds=holds)
