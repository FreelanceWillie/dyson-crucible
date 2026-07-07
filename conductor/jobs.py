"""
jobs.py - a persistent, self-healing generation queue.

State lives on disk at outputs/_jobs.json so the queue survives restarts. Each
job is a dict:

    {
      "id":      "<hex>",
      "asset":   "<brief name>",
      "kind":    "gen" | "vector",
      "params":  { ... kind-specific ... },
      "status":  "queued" | "running" | "done" | "failed",
      "tries":   <int>,
      "result":  [<paths>] | None,
      "error":   "<str>" | None,
      "created": "<iso8601>",
      "updated": "<iso8601>"
    }

The worker NEVER dies: worker_loop catches and logs every exception. Heavy deps
(gen -> ComfyUI/diffusers, rank -> CLIP, vectorize -> potrace/PIL) are imported
LAZILY inside run_next, so importing this module is always safe even on a box
missing those packages.

Public contract (server.py + conductor.py call these):

    enqueue(asset, kind, params) -> job_id
    list_jobs() -> list[dict]
    get(job_id) -> dict | None
    run_next(cfg) -> job | None
    worker_loop(cfg, stop_flag=None)
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

try:  # match brief.py's flat-import contract, with a package fallback
    import cfg as _cfg
except ImportError:  # pragma: no cover
    from conductor import cfg as _cfg  # type: ignore


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _jobs_path() -> str:
    """Absolute path to the queue file: outputs/_jobs.json."""
    return os.path.join(_cfg.path("outputs"), "_jobs.json")


# ---------------------------------------------------------------------------
# Persistence (atomic)
# ---------------------------------------------------------------------------
def _load() -> List[Dict[str, Any]]:
    """Load the job list from disk; return [] if missing or unreadable."""
    p = _jobs_path()
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except Exception as exc:  # noqa: BLE001 - never let a corrupt file kill the queue
        print(f"[jobs] WARNING: could not read {p} ({exc}); starting empty")
    return []


def _save(jobs: List[Dict[str, Any]]) -> None:
    """Atomically write the job list to outputs/_jobs.json (temp + replace)."""
    p = _jobs_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix="_jobs_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(jobs, fh, indent=2)
        os.replace(tmp, p)  # atomic on the same filesystem
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def enqueue(asset: str, kind: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Append a new queued job and return its id."""
    if kind not in ("gen", "vector", "explore", "post"):
        raise ValueError(f"unknown job kind: {kind!r} (expected gen, vector, explore, or post)")
    jobs = _load()
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "asset": asset,
        "kind": kind,
        "params": params or {},
        "status": "queued",
        "tries": 0,
        "result": None,
        "error": None,
        "created": _now(),
        "updated": _now(),
    }
    jobs.append(job)
    _save(jobs)
    return job_id


def list_jobs() -> List[Dict[str, Any]]:
    """Return all jobs (as stored on disk)."""
    return _load()


def get(job_id: str) -> Optional[Dict[str, Any]]:
    """Return one job by id, or None."""
    for job in _load():
        if job.get("id") == job_id:
            return job
    return None


# ---------------------------------------------------------------------------
# Control: pause / resume the whole queue, cancel a job. This is how the user
# reclaims his machine. State is persisted next to the queue so it survives a
# server restart.
# ---------------------------------------------------------------------------
def _control_path() -> str:
    return os.path.join(_cfg.path("outputs"), "_control.json")


def _load_control() -> Dict[str, Any]:
    try:
        with open(_control_path(), "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _save_control(ctrl: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_control_path()), exist_ok=True)
        with open(_control_path(), "w", encoding="utf-8") as fh:
            json.dump(ctrl, fh)
    except Exception:
        pass


def is_paused() -> bool:
    return bool(_load_control().get("paused"))


def set_paused(paused: bool) -> bool:
    ctrl = _load_control()
    ctrl["paused"] = bool(paused)
    _save_control(ctrl)
    return bool(paused)


def cancel(job_id: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Cancel a job. Queued -> marked 'cancelled'. Running -> best-effort:
    interrupt the ComfyUI gen and mark it cancelled (it will stop at the next
    checkpoint). Returns True if the job was found."""
    jobs = _load()
    for job in jobs:
        if job.get("id") != job_id:
            continue
        if job.get("status") == "running":
            try:
                import comfyui as _comfyui  # lazy, best effort
                url = ((cfg or _cfg.load_config()).get("comfyui", {}) or {}).get("url", "")
                if url:
                    _comfyui.interrupt(url)
            except Exception:
                pass
        job["status"] = "cancelled"
        job["updated"] = _now()
        _save(jobs)
        return True
    return False


def clear_finished() -> int:
    """Drop done/failed/cancelled jobs from the list. Returns how many removed."""
    jobs = _load()
    keep = [j for j in jobs if j.get("status") in ("queued", "running")]
    removed = len(jobs) - len(keep)
    _save(keep)
    return removed


def _update(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Patch a job on disk with the given fields; return the updated job."""
    jobs = _load()
    updated: Optional[Dict[str, Any]] = None
    for job in jobs:
        if job.get("id") == job_id:
            job.update(fields)
            job["updated"] = _now()
            updated = job
            break
    if updated is not None:
        _save(jobs)
    return updated


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def _looks_like_engine_down(err: Exception) -> bool:
    """Heuristic: does this error look like ComfyUI being unreachable/down?"""
    s = str(err).lower()
    return any(
        tok in s
        for tok in (
            "comfyui is not available",
            "connection refused",
            "max retries exceeded",
            "failed to establish",
            "not running",
            "timed out waiting for comfyui",
        )
    )


def _dispatch(job: Dict[str, Any], cfg: Dict[str, Any]) -> List[str]:
    """Run one job by kind; return its result paths. Heavy imports are lazy."""
    kind = job.get("kind")
    asset = job.get("asset")
    params = job.get("params", {}) or {}

    if kind == "gen":
        import gen as _gen  # lazy: pulls in ComfyUI/diffusers only when needed

        # server enqueues 'n_candidates'; accept 'n' too for direct callers.
        n = int(params.get("n_candidates") or params.get("n")
                or (cfg.get("gen", {}) or {}).get("n_candidates", 4))
        out_dir = params.get("out_dir") or os.path.join(_cfg.path("outputs"), asset)
        brief = params.get("brief")
        if brief is None:
            # Load the brief from disk if only the name was queued.
            try:
                import brief as _briefmod  # lazy

                briefs_dir = _cfg.path("briefs")
                brief = _briefmod.load(asset, briefs_dir)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"no brief supplied and could not load '{asset}': {exc}")

        # Fold in the asset's CATEGORY "style DNA" (inherited prompt words,
        # reference set, defaults) before generating, so siblings stay cohesive.
        try:
            import categories as _cats  # lazy

            brief = _cats.effective_brief(brief)
        except Exception as exc:  # noqa: BLE001
            print(f"[jobs] category resolve skipped ({exc})")

        paths = _gen.generate(brief, n, out_dir, cfg)

        # Optional ranking pass: score candidates vs the brief's reference dir.
        # rank.rank(candidate_paths, reference_dir, clip_model) -> [(path, score)].
        try:
            import rank as _rank  # lazy: CLIP deps

            ref_set = brief.get("reference_set") or "default"
            ref_dir = os.path.join(_cfg.path("references"), *str(ref_set).split("/"))
            clip_model = (cfg.get("rank", {}) or {}).get("clip_model", "ViT-B-32")
            ranked = _rank.rank(paths, ref_dir, clip_model)  # type: ignore[attr-defined]
            if ranked:
                # unwrap (path, score) tuples into best-first path list
                paths = [r[0] if isinstance(r, (list, tuple)) else r for r in ranked]
        except Exception as exc:  # noqa: BLE001
            print(f"[jobs] ranking skipped ({exc})")
        return paths

    if kind == "vector":
        import vectorize as _vectorize  # lazy

        # server enqueues {png, out_svg, colors}; accept {src, out} for direct callers.
        src = params.get("png") or params.get("src")
        out = (params.get("out_svg") or params.get("out")
               or os.path.join(_cfg.path("vectors"), f"{asset}.svg"))
        colors = int(params.get("colors") or (cfg.get("vector", {}) or {}).get("colors", 12))
        if not src:
            raise RuntimeError("vector job requires params['png'] (a raster image path)")
        result = _vectorize.vectorize(src, out, colors)  # type: ignore[attr-defined]
        if isinstance(result, str):
            return [result]
        if isinstance(result, (list, tuple)):
            return list(result)
        return [out]

    if kind == "post":
        # run a composable post-processing chain on a picked image
        import postprocess as _pp  # lazy

        src = params.get("src") or params.get("png")
        out_dir = params.get("out_dir") or os.path.join(_cfg.path("outputs"), asset, "_post")
        chain = params.get("chain")
        name = params.get("chain_name")
        if not src:
            raise RuntimeError("post job requires params['src']")
        if chain is not None:
            res = _pp.run_chain(src, chain, out_dir, cfg)
        else:
            res = _pp.run_named(src, name or "default", out_dir, cfg)
        # return the final path (+ keep the step log on the job via result being a dict is fine)
        return res

    if kind == "explore":
        # "Surprise me": one image per DISTINCT direction (a mood board). Each
        # direction carries its own prompt/negative; base_brief supplies engine
        # params + reference set (usually a category's).
        import gen as _gen  # lazy

        directions = params.get("directions") or []
        out_dir = params.get("out_dir") or os.path.join(_cfg.path("outputs"), asset, "_explore")
        base = dict(params.get("base_brief") or {})
        results = []
        for i, d in enumerate(directions):
            b = dict(base)
            b["prompt"] = d.get("prompt", "")
            b["negative"] = d.get("negative", "")
            sub = os.path.join(out_dir, "take_{0}".format(i + 1))
            try:
                paths = _gen.generate(b, 1, sub, cfg)
                if paths:
                    results.append({"label": d.get("label", ""),
                                    "prompt": d.get("prompt", ""), "path": paths[0]})
            except Exception as exc:  # noqa: BLE001 - one bad take must not kill the board
                print(f"[jobs] explore take {i + 1} failed: {exc}")
        return results

    raise ValueError(f"unknown job kind: {kind!r}")


def run_next(cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Pop the oldest queued job, run it, and record the outcome.

    On success: status='done', result=<paths>.
    On failure: increment tries; requeue (status back to 'queued') until
    queue.max_retries, then mark 'failed'. If the failure looks like ComfyUI
    being down and queue.restart_engine_on_fail is set, try to bring it back.

    Returns the job dict (in its post-run state) or None if the queue was empty.
    """
    if cfg is None:
        cfg = _cfg.load_config()

    # Paused by the operator: leave the queue untouched (worker treats as idle).
    if is_paused():
        return None

    jobs = _load()
    target = None
    for job in jobs:  # oldest queued first (list is append-ordered)
        if job.get("status") == "queued":
            target = job
            break
    if target is None:
        return None

    job_id = target["id"]
    _update(job_id, status="running")

    queue_cfg = cfg.get("queue", {}) or {}
    max_retries = int(queue_cfg.get("max_retries", 3))
    restart_on_fail = bool(queue_cfg.get("restart_engine_on_fail", True))

    try:
        result = _dispatch(target, cfg)
        return _update(job_id, status="done", result=result, error=None)
    except Exception as exc:  # noqa: BLE001 - a job failure must not crash the worker
        print(f"[jobs] job {job_id} ({target.get('kind')}/{target.get('asset')}) failed: {exc}")

        # Self-heal the engine if this smells like ComfyUI being down.
        if restart_on_fail and target.get("kind") == "gen" and _looks_like_engine_down(exc):
            try:
                import comfyui as _comfyui  # lazy

                print("[jobs] failure looks like ComfyUI down; attempting restart...")
                _comfyui.ensure_up(cfg)
            except Exception as heal_exc:  # noqa: BLE001
                print(f"[jobs] self-heal attempt failed: {heal_exc}")

        tries = int(target.get("tries", 0)) + 1
        if tries > max_retries:
            return _update(job_id, status="failed", tries=tries, error=str(exc))
        # Requeue for another attempt.
        return _update(job_id, status="queued", tries=tries, error=str(exc))


def worker_loop(cfg: Optional[Dict[str, Any]] = None, stop_flag: Optional[Callable[[], bool]] = None) -> None:
    """Run jobs forever, self-healing. NEVER raises out of the loop.

    - Processes queued jobs back-to-back.
    - Sleeps queue.poll_seconds when the queue is idle.
    - Every exception (including a corrupt state file) is caught and logged so
      the worker keeps running.
    - Pass `stop_flag` (a zero-arg callable returning True to stop) for a clean
      shutdown; omit it to run until the process is killed.
    """
    if cfg is None:
        cfg = _cfg.load_config()
    poll = float((cfg.get("queue", {}) or {}).get("poll_seconds", 2))

    print("[jobs] worker loop started.")
    while True:
        if stop_flag is not None:
            try:
                if stop_flag():
                    print("[jobs] worker loop stopping (stop_flag).")
                    return
            except Exception:  # noqa: BLE001 - a bad flag must not kill us
                pass
        try:
            job = run_next(cfg)
            if job is None:
                time.sleep(poll)  # idle: nothing queued
        except Exception as exc:  # noqa: BLE001 - the loop must be unkillable
            print(f"[jobs] worker caught top-level error (continuing): {exc}")
            time.sleep(poll)
