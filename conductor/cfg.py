"""
cfg.py - shared configuration helper for the whole art-conductor repo.

This is the SINGLE contract that every other module relies on:

    from cfg import load_config, resolve, path, REPO_ROOT

Everything is anchored to REPO_ROOT (the parent of this `conductor` package),
so paths work no matter what the current working directory is. The config is
read once from `config.yaml` at the repo root, deep-merged over a built-in
DEFAULTS dict (so a missing key is NEVER fatal), and then cached.

Dependency-light on purpose: only PyYAML + stdlib.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
from typing import Any, Dict, List

try:
    import yaml
except ImportError as exc:  # pragma: no cover - friendly guard
    raise ImportError(
        "PyYAML is required for config loading. Install it with: pip install pyyaml"
    ) from exc


# Repo root = one directory above this file's package (conductor/ -> repo/).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Path to the config file we read (kept as a module constant for clarity).
_CONFIG_PATH = os.path.join(REPO_ROOT, "config.yaml")
_CONFIG_EXAMPLE = os.path.join(REPO_ROOT, "config.example.yaml")


def _ensure_config() -> None:
    """Provision a per-machine config.yaml from the tracked config.example.yaml on
    first run. config.yaml is gitignored (it holds machine paths + the user's
    choices), so a fresh clone has only the example; copy it once so edits, the
    installer, and the settings UI have a real file to work with."""
    if os.path.isfile(_CONFIG_PATH):
        return
    try:
        if os.path.isfile(_CONFIG_EXAMPLE):
            import shutil
            shutil.copyfile(_CONFIG_EXAMPLE, _CONFIG_PATH)
    except Exception as exc:  # never fatal: DEFAULTS still apply
        print(f"[cfg] could not create config.yaml from example ({exc})")


# ---------------------------------------------------------------------------
# DEFAULTS - a complete, safe fallback for every key the pipeline reads. The
# on-disk config.yaml is merged OVER this, so any key the user omits (or an
# entirely absent config.yaml) still yields a usable config.
# ---------------------------------------------------------------------------
DEFAULTS: Dict[str, Any] = {
    "brain": "local",
    "ollama_model": "qwen2.5:3b-instruct",
    "ollama_url": "http://localhost:11434",
    "gemini_api_key_env": "GEMINI_API_KEY",
    "gemini_model": "gemini-2.0-flash",
    "claude_cmd": "claude",
    "engine": "comfyui",
    "paths": {
        "references": "references",
        "briefs": "briefs",
        "outputs": "outputs",
        "vectors": "vectors",
    },
    "comfyui": {
        "url": "http://127.0.0.1:8188",
        "exe": "",
        "workflow": "workflows/sd15_ipadapter.json",
        "checkpoint": "v1-5-pruned-emaonly.safetensors",
        "ip_adapter_preset": "PLUS",
        "warm_on_boot": True,  # start ComfyUI in the background at app launch so the
                               # first generation is instant (Reclaim frees it anytime)
    },
    "gen": {
        "base_model": "runwayml/stable-diffusion-v1-5",
        "steps": 28,
        "cfg": 7.0,
        "width": 512,
        "height": 512,
        "ip_adapter": True,
        "ip_adapter_weight": 0.6,
        "n_candidates": 4,
    },
    "queue": {
        "poll_seconds": 2,
        "max_retries": 3,
        "restart_engine_on_fail": True,
    },
    "rank": {
        "clip_model": "ViT-B-32",
    },
    "vector": {
        "colors": 12,
    },
}


# Module-level cache so we only read/merge config.yaml once per process.
_CACHE: Dict[str, Any] | None = None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge `override` into a copy of `base` and return it.

    Nested dicts are merged key-by-key; any non-dict value in `override`
    replaces the value in `base`. `base` is never mutated.
    """
    result = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(val, dict)
        ):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def load_config(force_reload: bool = False) -> Dict[str, Any]:
    """Read config.yaml from REPO_ROOT, deep-merge over DEFAULTS, cache, return.

    A missing or empty config.yaml simply yields the DEFAULTS. Set
    force_reload=True to bust the cache (useful in tests or after editing the
    file at runtime).
    """
    global _CACHE
    if _CACHE is not None and not force_reload:
        return _CACHE

    _ensure_config()  # provision config.yaml from the example on first run
    user_cfg: Dict[str, Any] = {}
    if os.path.isfile(_CONFIG_PATH):
        try:
            # utf-8-sig: transparently strips a BOM (PowerShell's Set-Content
            # -Encoding UTF8 writes one) so it never leaks into the first key.
            with open(_CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
                loaded = yaml.safe_load(fh)
            if isinstance(loaded, dict):
                user_cfg = loaded
        except Exception as exc:  # noqa: BLE001 - never let a bad file be fatal
            # Fall back to defaults but make the problem visible.
            print(f"[cfg] WARNING: could not parse config.yaml ({exc}); using defaults")

    _CACHE = _deep_merge(DEFAULTS, user_cfg)
    return _CACHE


def resolve(*parts: str) -> str:
    """Join the given parts under REPO_ROOT and return an absolute path."""
    return os.path.abspath(os.path.join(REPO_ROOT, *parts))


# ---------------------------------------------------------------------------
# Projects: the top layer above categories + heroes. Each project is a fully
# isolated data workspace under projects/<name>/ (its own briefs, outputs,
# references, vectors, and categories.yaml). Models/engine/gen settings in
# config.yaml stay GLOBAL and shared across projects.
# ---------------------------------------------------------------------------
PROJECTS_DIR = os.path.join(REPO_ROOT, "projects")
_ACTIVE_FILE = os.path.join(PROJECTS_DIR, "_active.txt")
_DATA_KEYS = {"references", "briefs", "outputs", "vectors"}  # per-project data


def _safe_project(name: str) -> str:
    return (re.sub(r"[^A-Za-z0-9 _-]", "", (name or "")).strip()) or "default"


def _ensure_projects() -> None:
    """Create projects/default and, on first run, migrate any legacy top-level
    data (briefs/outputs/references/vectors/categories.yaml) into it."""
    default = os.path.join(PROJECTS_DIR, "default")
    if os.path.isdir(default):
        return
    os.makedirs(default, exist_ok=True)
    for item in ("briefs", "outputs", "references", "vectors", "categories.yaml"):
        src = os.path.join(REPO_ROOT, item)
        dst = os.path.join(default, item)
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                shutil.move(src, dst)
            except Exception as exc:  # pragma: no cover - never fatal
                print(f"[cfg] project migration: could not move {item} ({exc})")
    for k in _DATA_KEYS:
        os.makedirs(os.path.join(default, k), exist_ok=True)


def active_project() -> str:
    try:
        with open(_ACTIVE_FILE, "r", encoding="utf-8") as fh:
            n = _safe_project(fh.read())
    except Exception:
        n = "default"
    # If the recorded project no longer exists, fall back to default.
    if not os.path.isdir(os.path.join(PROJECTS_DIR, n)):
        return "default"
    return n


def set_active_project(name: str) -> str:
    name = _safe_project(name)
    create_project(name)
    with open(_ACTIVE_FILE, "w", encoding="utf-8") as fh:
        fh.write(name)
    return name


def create_project(name: str) -> str:
    name = _safe_project(name)
    for k in _DATA_KEYS:
        os.makedirs(os.path.join(PROJECTS_DIR, name, k), exist_ok=True)
    return name


def delete_project(name: str) -> bool:
    name = _safe_project(name)
    if name == "default":
        return False  # keep at least the default workspace
    shutil.rmtree(os.path.join(PROJECTS_DIR, name), ignore_errors=True)
    if active_project() == name:
        set_active_project("default")
    return True


def list_projects() -> List[str]:
    _ensure_projects()
    try:
        names = [d for d in os.listdir(PROJECTS_DIR)
                 if os.path.isdir(os.path.join(PROJECTS_DIR, d)) and not d.startswith("_")]
    except Exception:
        names = []
    return sorted(names) or ["default"]


def project_root() -> str:
    """Absolute path of the active project's data workspace."""
    _ensure_projects()
    return os.path.join(PROJECTS_DIR, active_project())


def project_file(name: str) -> str:
    """A per-project file, e.g. project_file('categories.yaml')."""
    return os.path.join(project_root(), name)


def path(key: str) -> str:
    """Absolute path of cfg['paths'][key]. Per-project data keys (references,
    briefs, outputs, vectors) resolve under the ACTIVE project; anything else
    resolves under REPO_ROOT. Falls back to DEFAULTS['paths'] then the key name.
    """
    cfg = load_config()
    paths = cfg.get("paths", {}) or {}
    rel = paths.get(key) or DEFAULTS["paths"].get(key) or key
    if key in _DATA_KEYS:
        return os.path.abspath(os.path.join(project_root(), rel))
    return resolve(rel)
