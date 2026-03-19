"""
phase/config.py — Configuration loader.

Reads model_config.yaml and exposes a typed Config object.
All other modules import from here — never read YAML directly.

Usage:
    from config import cfg
    model = cfg.flux_model("coder")
    cap   = cfg.budget_cap("researcher")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "model_config.yaml"


# ── Typed config dataclass ────────────────────────────────────────────────────

@dataclass
class SolidConfig:
    validator_1: str = "google/gemini-flash-1.5"
    validator_2: str = "anthropic/claude-haiku-4-5-20251001"
    validator_3: str = "meta-llama/llama-3.1-8b-instruct"

    def all_models(self) -> list[str]:
        return [self.validator_1, self.validator_2, self.validator_3]


@dataclass
class PlasmaConfig:
    strategic:    str = "anthropic/claude-opus-4-6"
    coordination: str = "anthropic/claude-sonnet-4-6"
    light:        str = "google/gemini-flash-1.5"


@dataclass
class BudgetConfig:
    per_task_caps:     dict = field(default_factory=lambda: {
        "coder": 0.50, "researcher": 0.10,
        "reviewer": 0.08, "architect": 1.00, "default": 0.25,
    })
    solid_reserve_pct: float = 5.0
    plasma_bg_pct:     float = 10.0


@dataclass
class MaxRoundsConfig:
    plasma_strategic:    int = 200
    plasma_coordination: int = 60
    coder:               int = 80
    researcher:          int = 20
    reviewer:            int = 15
    architect:           int = 200
    solid_vote:          int = 5


@dataclass
class TimeoutsConfig:
    node_task_seconds:     int = 300
    solid_vote_seconds:    int = 30
    plasma_evolve_seconds: int = 600


@dataclass
class Config:
    plasma:     PlasmaConfig     = field(default_factory=PlasmaConfig)
    solid:      SolidConfig      = field(default_factory=SolidConfig)
    flux_models: dict            = field(default_factory=lambda: {
        "coder":      "anthropic/claude-sonnet-4-6",
        "researcher": "google/gemini-flash-1.5",
        "reviewer":   "google/gemini-flash-1.5",
        "architect":  "anthropic/claude-opus-4-6",
    })
    fallback:   list             = field(default_factory=lambda: [
        "anthropic/claude-sonnet-4-6",
        "google/gemini-2.5-pro-preview",
    ])
    budget:     BudgetConfig     = field(default_factory=BudgetConfig)
    max_rounds: MaxRoundsConfig  = field(default_factory=MaxRoundsConfig)
    timeouts:   TimeoutsConfig   = field(default_factory=TimeoutsConfig)

    # ── convenience accessors ─────────────────────────────────────────────────

    def flux_model(self, node_type: str) -> str:
        """Get model for a FLUX node type. Falls back to coder model."""
        return self.flux_models.get(node_type, self.flux_models.get("coder", "anthropic/claude-sonnet-4-6"))

    def budget_cap(self, node_type: str) -> float:
        """Get per-task budget cap for a node type."""
        return self.budget.per_task_caps.get(node_type, self.budget.per_task_caps["default"])

    def max_rounds_for(self, role: str) -> int:
        """Get max rounds for a role."""
        return getattr(self.max_rounds, role, 60)


# ── Loader ────────────────────────────────────────────────────────────────────

def load_config(path: Optional[Path] = None) -> Config:
    """
    Load model_config.yaml. Falls back to defaults if file missing or
    yaml not installed (pure-stdlib fallback keeps bootstrap.py zero-dep).
    """
    config_path = path or Path(os.environ.get("PHASE_CONFIG", str(_DEFAULT_CONFIG_PATH)))

    if not config_path.exists() or not _YAML_OK:
        return Config()   # all defaults

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return Config()

    c = Config()

    # plasma
    if pm := raw.get("plasma"):
        c.plasma = PlasmaConfig(
            strategic    = pm.get("strategic",    c.plasma.strategic),
            coordination = pm.get("coordination", c.plasma.coordination),
            light        = pm.get("light",        c.plasma.light),
        )

    # solid
    if sm := raw.get("solid"):
        c.solid = SolidConfig(
            validator_1 = sm.get("validator_1", c.solid.validator_1),
            validator_2 = sm.get("validator_2", c.solid.validator_2),
            validator_3 = sm.get("validator_3", c.solid.validator_3),
        )

    # flux
    if fm := raw.get("flux"):
        c.flux_models.update(fm)

    # fallback
    if fl := raw.get("fallback"):
        c.fallback = fl

    # budget
    if bm := raw.get("budget"):
        if caps := bm.get("per_task_caps"):
            c.budget.per_task_caps.update(caps)
        c.budget.solid_reserve_pct = bm.get("solid_reserve_pct", c.budget.solid_reserve_pct)
        c.budget.plasma_bg_pct     = bm.get("plasma_bg_pct",     c.budget.plasma_bg_pct)

    # max_rounds
    if mr := raw.get("max_rounds"):
        for k, v in mr.items():
            if hasattr(c.max_rounds, k):
                setattr(c.max_rounds, k, int(v))

    # timeouts
    if to := raw.get("timeouts"):
        for k, v in to.items():
            if hasattr(c.timeouts, k):
                setattr(c.timeouts, k, int(v))

    return c


# ── Singleton ─────────────────────────────────────────────────────────────────
cfg = load_config()


def reload_config(path: Optional[Path] = None) -> Config:
    """Reload config (useful after model_config.yaml is edited at runtime)."""
    global cfg
    cfg = load_config(path)
    return cfg
