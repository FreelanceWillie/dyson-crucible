"""
brief.py - per-asset "brief" storage.

A brief is the single source of truth for one piece of art. It captures the
prompt, the negative prompt, how hard to lean on the reference images
(ip_adapter_weight), which reference set to steer with, the running chat log
with the conductor, and a version history so you can undo/compare.

On disk each asset lives at:  briefs/<name>/brief.yaml

This module is intentionally dependency-light: only PyYAML plus the stdlib.
It knows nothing about generation, ranking, or LLMs. Path resolution is done
against a base directory that callers pass in (usually cfg['paths']['briefs']);
if omitted we fall back to a "briefs" folder next to the repo.
"""

from __future__ import annotations

import copy
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover - guard for a friendly message
    raise ImportError(
        "PyYAML is required for brief storage. Install it with: pip install pyyaml"
    ) from exc


# Default briefs root, used when a caller does not pass one explicitly.
# We anchor it to the repo root (one directory above this file's package).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BRIEFS_DIR = os.path.join(_REPO_ROOT, "briefs")


def _brief_dir(name: str, briefs_dir: Optional[str] = None) -> str:
    """Return the folder that holds a given asset's brief.yaml."""
    base = briefs_dir or DEFAULT_BRIEFS_DIR
    return os.path.join(base, name)


def _brief_path(name: str, briefs_dir: Optional[str] = None) -> str:
    """Return the full path to a given asset's brief.yaml."""
    return os.path.join(_brief_dir(name, briefs_dir), "brief.yaml")


def new(name: str, prompt: str) -> Dict[str, Any]:
    """Create a fresh in-memory brief seeded with sensible defaults.

    The caller is responsible for save()-ing it. We keep the shape stable so
    every other module can rely on these keys existing.
    """
    return {
        "name": name,
        "prompt": prompt,
        "negative": "",
        "ip_adapter_weight": 0.6,
        "reference_set": "default",
        "chat": [],       # list of {role, text, time} exchanges
        "versions": [],    # snapshots for undo/history (see snapshot())
        # Lightweight run bookkeeping that the conductor fills in over time.
        "chosen": None,     # path to the picked winner, once picked
        "created": datetime.utcnow().isoformat() + "Z",
    }


def exists(name: str, briefs_dir: Optional[str] = None) -> bool:
    """True if a brief.yaml already exists for this asset."""
    return os.path.isfile(_brief_path(name, briefs_dir))


def load(name: str, briefs_dir: Optional[str] = None) -> Dict[str, Any]:
    """Load a brief from disk. Raises FileNotFoundError if it is missing."""
    path = _brief_path(name, briefs_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "No brief for '{0}'. Create it first with: new {0} \"...\"".format(name)
        )
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # Backfill any keys that older briefs might be missing so callers are safe.
    seed = new(name, data.get("prompt", ""))
    for key, default in seed.items():
        data.setdefault(key, default)
    return data


def save(name: str, brief: Dict[str, Any], briefs_dir: Optional[str] = None) -> str:
    """Write a brief to disk, creating briefs/<name>/ if needed.

    Returns the path written. The 'name' argument wins over any stale name in
    the dict so a brief is always saved under the folder you asked for.
    """
    brief = dict(brief)  # shallow copy so we do not mutate the caller's dict
    brief["name"] = name
    directory = _brief_dir(name, briefs_dir)
    os.makedirs(directory, exist_ok=True)
    path = _brief_path(name, briefs_dir)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(brief, fh, sort_keys=False, allow_unicode=True)
    return path


def append_chat(brief: Dict[str, Any], role: str, text: str) -> Dict[str, Any]:
    """Append a chat turn to the brief in place and return it.

    role is a short tag like 'user', 'assistant', or 'system'. We timestamp
    each turn so the log doubles as a lightweight audit trail.
    """
    brief.setdefault("chat", []).append(
        {
            "role": role,
            "text": text,
            "time": datetime.utcnow().isoformat() + "Z",
        }
    )
    return brief


def snapshot(brief: Dict[str, Any]) -> Dict[str, Any]:
    """Push a copy of the current prompt/negative/weight into 'versions'.

    Each snapshot gets a monotonically increasing index so the conductor can
    show history and (later) restore an earlier take. We copy the values so a
    subsequent edit to the live brief does not mutate the stored version.
    """
    versions: List[Dict[str, Any]] = brief.setdefault("versions", [])
    entry = {
        "index": len(versions),
        "prompt": copy.deepcopy(brief.get("prompt", "")),
        "negative": copy.deepcopy(brief.get("negative", "")),
        "ip_adapter_weight": brief.get("ip_adapter_weight", 0.6),
        "reference_set": brief.get("reference_set", "default"),
        "candidates": copy.deepcopy(brief.get("last_candidates", [])),
        "time": datetime.utcnow().isoformat() + "Z",
    }
    versions.append(entry)
    return brief


def list_assets(briefs_dir: Optional[str] = None) -> List[str]:
    """Return the names of all assets that have a brief on disk (sorted)."""
    base = briefs_dir or DEFAULT_BRIEFS_DIR
    if not os.path.isdir(base):
        return []
    names = []
    for entry in os.listdir(base):
        if os.path.isfile(os.path.join(base, entry, "brief.yaml")):
            names.append(entry)
    return sorted(names)
