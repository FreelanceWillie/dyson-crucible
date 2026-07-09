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

import os
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


# Stable psutil.Process objects across snapshots so per-process cpu_percent() has a
# baseline to diff against (the first call on a fresh object always returns 0).
_PROC_CACHE: Dict[int, Any] = {}


def _dc_pids(ps) -> set:
    """PIDs that belong to Dyson Crucible: this server + its children, the ComfyUI
    engine (launched detached, so matched by command line, not parentage), and the
    Ollama brain daemon. Used to show what share of the machine the tool itself is
    using vs everything else."""
    pids = set()
    try:
        me = ps.Process(os.getpid())
        pids.add(me.pid)
        for c in me.children(recursive=True):
            pids.add(c.pid)
    except Exception:
        pass
    try:
        for p in ps.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                cmd = " ".join(p.info.get("cmdline") or []).lower()
            except Exception:
                continue
            if "ollama" in name:
                pids.add(p.info["pid"])
            elif "comfyui" in cmd and "main.py" in cmd:
                pids.add(p.info["pid"])
            elif "conductor" in cmd and "server.py" in cmd:
                pids.add(p.info["pid"])
    except Exception:
        pass
    return pids


def _vram_by_pid() -> Dict[int, float]:
    """pid -> VRAM MiB, from nvidia-smi's compute-apps view. {} if unavailable."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        m: Dict[int, float] = {}
        for line in (out.stdout or "").strip().splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2 and parts[0].isdigit():
                try:
                    m[int(parts[0])] = float(parts[1])
                except ValueError:
                    pass
        return m
    except Exception:
        return {}


def _dc_share(ps) -> Optional[Dict[str, Any]]:
    """CPU% / RAM MiB / VRAM MiB attributed to Dyson Crucible's own processes."""
    if ps is None:
        return None
    pids = _dc_pids(ps)
    ram_mb = 0.0
    cpu = 0.0
    live: Dict[int, Any] = {}
    for pid in pids:
        try:
            p = _PROC_CACHE.get(pid) or ps.Process(pid)
            live[pid] = p
            ram_mb += p.memory_info().rss / 1048576.0
            cpu += p.cpu_percent(None)  # % since this object's last call (per-core sum)
        except Exception:
            continue
    _PROC_CACHE.clear()
    _PROC_CACHE.update(live)  # keep only still-live procs so the cache cannot grow forever
    vram = _vram_by_pid()
    vram_mb = sum(v for pid, v in vram.items() if pid in pids)
    ncpu = 1
    try:
        ncpu = ps.cpu_count() or 1
    except Exception:
        ncpu = 1
    return {
        "ram_mb": round(ram_mb),
        "cpu_pct": round(cpu / ncpu, 1),   # normalize per-core sum to a system-wide %
        "vram_mb": round(vram_mb),
        "nproc": len(live),
    }


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
    try:
        data["dc"] = _dc_share(ps)
    except Exception:
        data["dc"] = None
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
