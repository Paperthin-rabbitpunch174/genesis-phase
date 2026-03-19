"""
phase/state.py — Shared state for PHASE.

Single source of truth for:
  - Budget (total, spent per component)
  - Node registry (what FLUX nodes are active)
  - Event log (append-only history)
  - PLASMA version tracking

Persisted to data/phase_state.json after every write.
Thread-safe via asyncio.Lock.

Usage:
    from state import state
    await state.record_spend("coder", 0.005)
    remaining = state.budget_remaining()
    state.log_event("PLASMA", "Started evolution cycle")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger   = logging.getLogger(__name__)
DATA_DIR = Path(os.environ.get("PHASE_DATA_DIR", "data"))
STATE_FILE = DATA_DIR / "phase_state.json"

MAX_EVENTS = 500   # rolling window kept in memory and on disk


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class NodeInfo:
    node_id:      str
    node_type:    str       # coder / researcher / reviewer / architect / plugin:xxx
    model:        str
    status:       str       # idle / busy / error / stopped
    tasks_done:   int       = 0
    total_spend:  float     = 0.0
    spawned_at:   float     = field(default_factory=time.time)
    last_active:  float     = field(default_factory=time.time)


@dataclass
class PhaseEvent:
    timestamp:  float
    source:     str    # PLASMA / SOLID / FLUX:coder / etc.
    event_type: str    # task_assigned / task_done / vote_passed / evolution / error
    message:    str
    metadata:   dict   = field(default_factory=dict)


@dataclass
class PhaseState:
    total_budget:    float = 50.0
    spent_total:     float = 0.0
    spent_by_source: dict  = field(default_factory=dict)

    plasma_version:  str   = "1.0.0"
    plasma_status:   str   = "offline"   # offline / online / evolving

    nodes:   dict = field(default_factory=dict)  # node_id -> NodeInfo dict
    events:  list = field(default_factory=list)  # list of PhaseEvent dicts

    tasks_total:    int = 0
    tasks_done:     int = 0
    tasks_failed:   int = 0

    solid_votes_total:    int = 0
    solid_votes_approved: int = 0
    solid_votes_rejected: int = 0

    started_at: float = field(default_factory=time.time)

    def budget_remaining(self) -> float:
        return max(0.0, self.total_budget - self.spent_total)

    def budget_pct_used(self) -> float:
        if self.total_budget <= 0:
            return 0.0
        return self.spent_total / self.total_budget * 100

    def active_nodes(self) -> list[dict]:
        return [n for n in self.nodes.values()
                if n.get("status") not in ("stopped",)]


# ── Manager ───────────────────────────────────────────────────────────────────

class StateManager:
    """
    Thread-safe state manager. All mutations go through async methods.
    Reads can be done directly on self._s for performance.
    """

    def __init__(self):
        self._s    = PhaseState()
        self._lock = asyncio.Lock()

    # ── Budget ─────────────────────────────────────────────────────────────────

    async def init_budget(self, total: float) -> None:
        async with self._lock:
            self._s.total_budget = total
            await self._save()

    async def record_spend(self, source: str, amount: float) -> None:
        async with self._lock:
            self._s.spent_total += amount
            self._s.spent_by_source[source] = (
                self._s.spent_by_source.get(source, 0.0) + amount
            )
            if source.startswith("FLUX:"):
                node_id = source
                if node_id in self._s.nodes:
                    self._s.nodes[node_id]["total_spend"] = (
                        self._s.nodes[node_id].get("total_spend", 0.0) + amount
                    )
            await self._save()

    def budget_remaining(self) -> float:
        return self._s.budget_remaining()

    def total_budget(self) -> float:
        return self._s.total_budget

    # ── Nodes ──────────────────────────────────────────────────────────────────

    async def register_node(self, info: NodeInfo) -> None:
        async with self._lock:
            self._s.nodes[info.node_id] = asdict(info)
            await self._save()

    async def update_node_status(self, node_id: str, status: str) -> None:
        async with self._lock:
            if node_id in self._s.nodes:
                self._s.nodes[node_id]["status"]      = status
                self._s.nodes[node_id]["last_active"] = time.time()
            await self._save()

    async def remove_node(self, node_id: str) -> None:
        async with self._lock:
            self._s.nodes.pop(node_id, None)
            await self._save()

    def get_nodes(self) -> list[dict]:
        return list(self._s.nodes.values())

    def get_node(self, node_id: str) -> Optional[dict]:
        return self._s.nodes.get(node_id)

    # ── PLASMA ─────────────────────────────────────────────────────────────────

    async def set_plasma_status(self, status: str) -> None:
        async with self._lock:
            self._s.plasma_status = status
            await self._save()

    async def set_plasma_version(self, version: str) -> None:
        async with self._lock:
            self._s.plasma_version = version
            await self._save()

    # ── Tasks ──────────────────────────────────────────────────────────────────

    async def task_assigned(self) -> None:
        async with self._lock:
            self._s.tasks_total += 1
            await self._save()

    async def task_completed(self, node_id: str, success: bool) -> None:
        async with self._lock:
            if success:
                self._s.tasks_done += 1
            else:
                self._s.tasks_failed += 1
            if node_id in self._s.nodes:
                self._s.nodes[node_id]["tasks_done"] = (
                    self._s.nodes[node_id].get("tasks_done", 0) + 1
                )
            await self._save()

    # ── SOLID votes ────────────────────────────────────────────────────────────

    async def record_vote(self, approved: bool) -> None:
        async with self._lock:
            self._s.solid_votes_total += 1
            if approved:
                self._s.solid_votes_approved += 1
            else:
                self._s.solid_votes_rejected += 1
            await self._save()

    # ── Events ─────────────────────────────────────────────────────────────────

    async def log_event(
        self,
        source:     str,
        event_type: str,
        message:    str,
        metadata:   Optional[dict] = None,
    ) -> None:
        async with self._lock:
            evt = asdict(PhaseEvent(
                timestamp  = time.time(),
                source     = source,
                event_type = event_type,
                message    = message,
                metadata   = metadata or {},
            ))
            self._s.events.append(evt)
            if len(self._s.events) > MAX_EVENTS:
                self._s.events = self._s.events[-MAX_EVENTS:]
            await self._save()

    def recent_events(self, n: int = 20) -> list[dict]:
        return self._s.events[-n:]

    # ── Status summary ─────────────────────────────────────────────────────────

    def summary(self) -> dict:
        s = self._s
        return {
            "plasma_version": s.plasma_version,
            "plasma_status":  s.plasma_status,
            "budget": {
                "total":     s.total_budget,
                "spent":     round(s.spent_total, 4),
                "remaining": round(s.budget_remaining(), 4),
                "pct_used":  round(s.budget_pct_used(), 1),
            },
            "nodes": {
                "total":  len(s.nodes),
                "active": len(s.active_nodes()),
                "list":   s.active_nodes(),
            },
            "tasks": {
                "total":  s.tasks_total,
                "done":   s.tasks_done,
                "failed": s.tasks_failed,
            },
            "solid": {
                "total":    s.solid_votes_total,
                "approved": s.solid_votes_approved,
                "rejected": s.solid_votes_rejected,
                "approval_rate": (
                    round(s.solid_votes_approved / s.solid_votes_total * 100, 1)
                    if s.solid_votes_total > 0 else 0
                ),
            },
        }

    # ── Persistence ────────────────────────────────────────────────────────────

    async def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self._s), indent=2, default=str))
            tmp.replace(STATE_FILE)
        except Exception as exc:
            logger.warning("state: save failed: %s", exc)

    async def load(self) -> None:
        """Load persisted state on startup."""
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text())
            s    = self._s
            s.total_budget          = data.get("total_budget",    s.total_budget)
            s.spent_total           = data.get("spent_total",     s.spent_total)
            s.spent_by_source       = data.get("spent_by_source", s.spent_by_source)
            s.plasma_version        = data.get("plasma_version",  s.plasma_version)
            s.nodes                 = data.get("nodes",           s.nodes)
            s.events                = data.get("events",          s.events)[-MAX_EVENTS:]
            s.tasks_total           = data.get("tasks_total",     s.tasks_total)
            s.tasks_done            = data.get("tasks_done",      s.tasks_done)
            s.tasks_failed          = data.get("tasks_failed",    s.tasks_failed)
            s.solid_votes_total     = data.get("solid_votes_total",    s.solid_votes_total)
            s.solid_votes_approved  = data.get("solid_votes_approved", s.solid_votes_approved)
            s.solid_votes_rejected  = data.get("solid_votes_rejected", s.solid_votes_rejected)
            logger.info("state: loaded (budget spent=%.4f)", s.spent_total)
        except Exception as exc:
            logger.warning("state: load failed: %s", exc)


# ── Singleton ─────────────────────────────────────────────────────────────────
state = StateManager()
