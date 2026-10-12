"""Loader and lookup for early_action_registry.json.

Why a loader (vs just json.load in callers):
  - Single source of cached parsed JSON (avoids re-reading on every insight)
  - Provides typed lookup `find_action_key(signal, status)` so callers
    don't have to scan the actions dict
  - Frozen-set guard: a published registry version cannot be silently
    edited; modifications require bumping `_meta.version`
"""

from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path

_REGISTRY_PATH = Path(__file__).parent / "config" / "early_action_registry.json"


@lru_cache(maxsize=1)
def load_registry() -> dict:
    """Load and cache the action registry JSON. Subsequent calls reuse."""
    if not _REGISTRY_PATH.exists():
        raise FileNotFoundError(f"early_action_registry.json missing at {_REGISTRY_PATH}")
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def find_action_key(signal_name: str, status: str) -> str | None:
    """Find the action_key for a given (signal, status) pair.

    Returns None if no action is registered (e.g., signal=hook_score
    + status=over is intentionally not registered because "hook too
    strong" isn't a meaningful problem).
    """
    registry = load_registry()
    for key, action in registry.get("actions", {}).items():
        if action.get("signal") == signal_name and action.get("status") == status:
            return key
    return None


def get_action(action_key: str) -> dict | None:
    """Get the full action spec by key. None if unknown."""
    registry = load_registry()
    return registry.get("actions", {}).get(action_key)
