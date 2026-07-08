"""
resources.py - live machine resource readout for the dashboard.

This is his ONLY computer, so the dashboard must never black-box what the tool
is doing to it. This module reports CPU / RAM / GPU / VRAM / disk so the UI can
show honest meters, and pairs with the queue's pause/stop controls so he can
always reclaim his machine.

Everything degrades gracefully: psutil is optional (falls back to stdlib where it
can), and GPU stats come from `nvidia-smi` if present (no hard NVIDIA dependency).
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, Optional


def _psutil():
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:
        return None


def _gpu() -> Optional[Dict[str, Any]]:
    """VRAM + GPU utilization via nvidia-smi. None if no NVIDIA GPU / tool."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=utilization.gpu,memory.used,memory.total,name,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        line = (out.stdout or "").strip().splitlines()
        if not line:
            return None
        parts = [p.strip() for p in line[0].split(",")]
        util = float(parts[0]); used = float(parts[1]); total = float(parts[2])
        name = parts[3] if len(parts) > 3 else "GPU"
        temp = float(parts[4]) if len(parts) > 4 else None
        return {
            "name": name,
            "util_pct": round(util, 1),
            "vram_used_mb": round(used), "vram_total_mb": round(total),
            "vram_pct": round(100.0 * used / total, 1) if total else 0.0,
            "temp_c": temp,
        }
    except Exception:
        return None


_SNAP_CACHE = {"t": 0.0, "data": None}
_SNAP_TTL = 2.5  # seconds; multiple pollers/tabs reuse one reading


def snapshot() -> Dict[str, Any]:
    """One reading of the machine's resource state, cached ~2.5s. The GPU reading
    spawns nvidia-smi, so without this cache every 2s poll (x every open tab) would
    fork a process -- soaking the CPU the tool is meant to leave for generation."""
    import time as _t
    now = _t.time()
    if _SNAP_CACHE["data"] is not None and (now - _SNAP_CACHE["t"]) < _SNAP_TTL:
        return _SNAP_CACHE["data"]
    data = _snapshot_raw()
    _SNAP_CACHE["t"] = now
    _SNAP_CACHE["data"] = data
    return data


def _snapshot_raw() -> Dict[str, Any]:
    ps = _psutil()
    data: Dict[str, Any] = {"gpu": _gpu()}
    if ps is not None:
        try:
            data["cpu_pct"] = ps.cpu_percent(interval=None)
        except Exception:
            data["cpu_pct"] = None
        try:
            vm = ps.virtual_memory()
            data["ram"] = {"used_mb": round(vm.used / 1048576), "total_mb": round(vm.total / 1048576),
                           "pct": vm.percent}
        except Exception:
            data["ram"] = None
        try:
            du = ps.disk_usage(".")
            data["disk"] = {"used_gb": round(du.used / 1073741824, 1),
                            "total_gb": round(du.total / 1073741824, 1), "pct": du.percent}
        except Exception:
            data["disk"] = None
    else:
        data["cpu_pct"] = None
        data["ram"] = None
        data["disk"] = None
        data["_note"] = "install psutil for CPU/RAM/disk meters (pip install psutil)"
    return data
