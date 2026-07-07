"""
comfyui.py - a small, self-healing HTTP client for a local ComfyUI server.

ComfyUI exposes a REST + WebSocket API (default http://127.0.0.1:8188). We only
need the REST side here:

    GET  /system_stats               -> liveness check
    POST /prompt                     -> queue a workflow, returns a prompt_id
    GET  /history/{prompt_id}        -> poll for completion + output metadata
    GET  /view?filename=..&...       -> download a produced image
    POST /upload/image               -> upload a reference image into ComfyUI/input
    POST /interrupt                  -> cancel the running job (best effort)

Self-healing: `ensure_up` will (optionally) launch ComfyUI from a configured
launcher exe/bat and wait for it to come alive.

Only `requests` + stdlib are used; the requests import is guarded with a clear
message so importing this module never hard-crashes a queue that might not need
the network right now.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError as exc:  # pragma: no cover - friendly guard
    requests = None  # type: ignore[assignment]
    _REQUESTS_IMPORT_ERROR = exc
else:
    _REQUESTS_IMPORT_ERROR = None


def _require_requests() -> None:
    """Raise a clear error if `requests` is not installed."""
    if requests is None:
        raise ImportError(
            "The 'requests' package is required to talk to ComfyUI. "
            "Install it with: pip install requests"
        ) from _REQUESTS_IMPORT_ERROR


def _base(url: str) -> str:
    """Normalise a base url (strip trailing slash)."""
    return (url or "").rstrip("/")


# ---------------------------------------------------------------------------
# Liveness + self-heal
# ---------------------------------------------------------------------------
def is_up(url: str) -> bool:
    """Return True if a ComfyUI server answers /system_stats with HTTP 200."""
    if requests is None:
        return False
    try:
        resp = requests.get(_base(url) + "/system_stats", timeout=3)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 - any network error means "not up"
        return False


def ensure_up(cfg: Dict[str, Any]) -> bool:
    """Make sure ComfyUI is reachable, launching it if configured.

    - If it is already up: return True immediately.
    - Else if `comfyui.exe` is set: launch it DETACHED (non-blocking) and poll
      is_up for up to ~60s. Return True once it answers.
    - Else (no exe): log a clear instruction and return False.
    """
    comfy = (cfg or {}).get("comfyui", {}) or {}
    url = comfy.get("url", "http://127.0.0.1:8188")

    if is_up(url):
        return True

    exe = (comfy.get("exe") or "").strip()
    if not exe:
        print(
            "[comfyui] ComfyUI is not running and no 'comfyui.exe' launcher is "
            f"configured. Please start ComfyUI so it listens at {url}."
        )
        return False

    if not os.path.isfile(exe):
        print(f"[comfyui] configured launcher does not exist: {exe}")
        return False

    print(f"[comfyui] ComfyUI is down; launching: {exe}")
    try:
        # Launch detached & non-blocking. On Windows, DETACHED_PROCESS +
        # a new process group keeps it alive independent of us and avoids
        # inheriting our console. cwd = the launcher's own folder so relative
        # paths inside a run_nvidia_gpu.bat resolve correctly.
        creationflags = 0
        if os.name == "nt":
            # 0x00000008 DETACHED_PROCESS | 0x00000200 CREATE_NEW_PROCESS_GROUP
            creationflags = 0x00000008 | 0x00000200
        subprocess.Popen(
            [exe],
            cwd=os.path.dirname(exe) or None,
            shell=(os.name == "nt"),  # allow launching .bat on Windows
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=(os.name != "nt"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[comfyui] failed to launch ComfyUI: {exc}")
        return False

    # Poll for liveness up to ~60s.
    deadline = time.time() + 60
    while time.time() < deadline:
        if is_up(url):
            print("[comfyui] ComfyUI is up.")
            return True
        time.sleep(2)

    print("[comfyui] timed out waiting for ComfyUI to come up (~60s).")
    return False


# ---------------------------------------------------------------------------
# Job submission + result collection
# ---------------------------------------------------------------------------
def submit(url: str, workflow: Dict[str, Any], client_id: str) -> str:
    """POST a workflow graph to /prompt and return the assigned prompt_id."""
    _require_requests()
    payload = {"prompt": workflow, "client_id": client_id}
    resp = requests.post(_base(url) + "/prompt", json=payload, timeout=30)
    if resp.status_code != 200:
        # ComfyUI returns a helpful JSON body describing bad nodes; surface it.
        raise RuntimeError(
            f"ComfyUI /prompt rejected the workflow (HTTP {resp.status_code}): "
            f"{resp.text[:2000]}"
        )
    data = resp.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt returned no prompt_id: {data}")
    return prompt_id


def upload_image(url: str, image_path: str, overwrite: bool = True) -> str:
    """Upload a local image into ComfyUI's input dir; return the stored filename.

    LoadImage nodes read from ComfyUI/input, so a reference on our side must be
    uploaded first. ComfyUI echoes back the (possibly renamed) filename.
    """
    _require_requests()
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"reference image not found: {image_path}")
    fname = os.path.basename(image_path)
    with open(image_path, "rb") as fh:
        files = {"image": (fname, fh, "application/octet-stream")}
        data = {"overwrite": "true" if overwrite else "false"}
        resp = requests.post(
            _base(url) + "/upload/image", files=files, data=data, timeout=60
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"ComfyUI /upload/image failed (HTTP {resp.status_code}): {resp.text[:500]}"
        )
    body = resp.json()
    # Response shape: {"name": "...", "subfolder": "", "type": "input"}
    return body.get("name", fname)


def _download_image(url: str, image: Dict[str, Any], out_dir: str) -> str:
    """Download one output image (via /view) to out_dir; return its path."""
    _require_requests()
    params = {
        "filename": image.get("filename", ""),
        "subfolder": image.get("subfolder", ""),
        "type": image.get("type", "output"),
    }
    resp = requests.get(_base(url) + "/view", params=params, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(
            f"ComfyUI /view failed for {params} (HTTP {resp.status_code})"
        )
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, os.path.basename(params["filename"]) or "image.png")
    with open(dest, "wb") as fh:
        fh.write(resp.content)
    return dest


def wait(url: str, prompt_id: str, timeout: int = 300, poll: int = 2) -> List[str]:
    """Poll /history/{prompt_id} until done; download outputs; return local paths.

    Raises TimeoutError if the job never completes within `timeout` seconds, and
    RuntimeError if ComfyUI reports the job errored/failed.
    """
    _require_requests()
    tmp_dir = tempfile.mkdtemp(prefix="comfyui_out_")
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            resp = requests.get(_base(url) + f"/history/{prompt_id}", timeout=10)
        except Exception:  # noqa: BLE001 - transient; keep polling
            time.sleep(poll)
            continue

        if resp.status_code == 200:
            history = resp.json() or {}
            entry = history.get(prompt_id)
            if entry:
                # Check for an explicit failure status.
                status = entry.get("status", {}) or {}
                status_str = str(status.get("status_str", "")).lower()
                if status_str in ("error", "failed"):
                    raise RuntimeError(
                        f"ComfyUI job {prompt_id} failed: {status.get('messages', status)}"
                    )
                # ComfyUI marks completed=True when done; also require outputs.
                outputs = entry.get("outputs", {}) or {}
                completed = status.get("completed", False)
                if outputs or completed:
                    images: List[str] = []
                    for _node_id, node_out in outputs.items():
                        for img in node_out.get("images", []) or []:
                            # Skip pure previews of type "temp" only if there
                            # is any real output; otherwise take what we have.
                            images.append(_download_image(url, img, tmp_dir))
                    if images:
                        return images
                    # Completed but produced no images -> treat as an error.
                    if completed:
                        raise RuntimeError(
                            f"ComfyUI job {prompt_id} completed but produced no images."
                        )
        time.sleep(poll)

    raise TimeoutError(
        f"Timed out after {timeout}s waiting for ComfyUI job {prompt_id}."
    )


def interrupt(url: str) -> None:
    """Best-effort cancel of the currently running job."""
    if requests is None:
        return
    try:
        requests.post(_base(url) + "/interrupt", timeout=5)
    except Exception:  # noqa: BLE001 - best effort, never raise
        pass


def free_vram(url: str) -> bool:
    """Ask ComfyUI to unload models + free memory (reclaim VRAM). Best effort."""
    if requests is None:
        return False
    try:
        requests.post(_base(url) + "/free", json={"unload_models": True, "free_memory": True}, timeout=8)
        return True
    except Exception:  # noqa: BLE001 - best effort
        return False


def new_client_id() -> str:
    """Convenience: a fresh client_id for /prompt submissions."""
    return uuid.uuid4().hex
