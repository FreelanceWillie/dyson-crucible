"""
models.py - Model Manager for the art-conductor pipeline.

Search + download LoRAs and ControlNet models from Civitai and install them
into the local ComfyUI models folder. The built-in brain can suggest "get me a
chibi LoRA"; the server exposes these functions so the user can one-click the
actual download. Downloads are ALWAYS user-approved: the server only calls
`download()` on an explicit action - this module merely provides the capability.

Design notes:
  - Dependency-light + defensive. `cfg` is imported lazily (with a config.yaml
    fallback) so this module can be reasoned about in isolation, and `requests`
    is guarded with a clear install message rather than an ImportError at import
    time.
  - Nothing here touches the network or filesystem at import time.

ControlNet caveat: downloading a ControlNet .safetensors is only half the job.
Actually *using* it in a generation requires the matching ComfyUI ControlNet
nodes (e.g. the ControlNet Aux / Advanced-ControlNet custom nodes) plus a
workflow wired to load and apply them. That workflow wiring is a separate
concern from this installer.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Config access (lazy) - never fatal if cfg.py or config.yaml is absent.
# ---------------------------------------------------------------------------

# Built-in fallback defaults for just the keys THIS module reads. Kept in sync
# with config.yaml / cfg.DEFAULTS but self-contained so we never hard-crash.
_MODEL_DEFAULTS: Dict[str, Any] = {
    "models": {
        "civitai_api": "https://civitai.com/api/v1",
        "civitai_token_env": "CIVITAI_TOKEN",
    },
    "comfyui": {
        "root": "",
        "exe": "",
    },
}

# Common on-disk model file extensions we recognise when listing installs.
_MODEL_EXTS = (".safetensors", ".pt", ".ckpt", ".bin")


def _load_cfg() -> Dict[str, Any]:
    """Return the merged config dict, importing cfg lazily.

    Falls back to reading config.yaml directly (then to _MODEL_DEFAULTS) if the
    cfg helper cannot be imported - e.g. when this file is used stand-alone.
    """
    # Primary path: the repo's shared cfg helper.
    try:
        from cfg import load_config  # type: ignore
        return load_config()
    except Exception:
        pass

    # Fallback: read config.yaml from the repo root (parent of this package).
    try:
        import yaml  # type: ignore
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg_path = os.path.join(repo_root, "config.yaml")
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh)
            if isinstance(loaded, dict):
                return loaded
    except Exception:
        pass

    # Last resort: our own minimal defaults.
    return dict(_MODEL_DEFAULTS)


def _cfg_or(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the passed cfg or lazily load one."""
    return cfg if isinstance(cfg, dict) else _load_cfg()


def _models_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    m = cfg.get("models") if isinstance(cfg.get("models"), dict) else {}
    out = dict(_MODEL_DEFAULTS["models"])
    out.update(m or {})
    return out


def _comfy_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    c = cfg.get("comfyui") if isinstance(cfg.get("comfyui"), dict) else {}
    return c or {}


def _api_base(cfg: Dict[str, Any]) -> str:
    return str(_models_cfg(cfg).get("civitai_api") or _MODEL_DEFAULTS["models"]["civitai_api"]).rstrip("/")


def _token(cfg: Dict[str, Any]) -> str:
    """Read the optional Civitai token from the configured env var."""
    env_name = str(_models_cfg(cfg).get("civitai_token_env") or "").strip()
    if not env_name:
        return ""
    return (os.environ.get(env_name) or "").strip()


def _auth_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Bearer auth header if a token is present, else empty."""
    tok = _token(cfg)
    return {"Authorization": "Bearer " + tok} if tok else {}


def _require_requests():
    """Import requests lazily with a clear, actionable error message."""
    try:
        import requests  # type: ignore
        return requests
    except ImportError as exc:  # pragma: no cover - friendly guard
        raise RuntimeError(
            "The 'requests' package is required for the Model Manager "
            "(search/download from Civitai). Install it with: pip install requests"
        ) from exc


# ---------------------------------------------------------------------------
# Filesystem resolution: where do ComfyUI's model folders live?
# ---------------------------------------------------------------------------

def models_root(cfg: Dict[str, Any]) -> str:
    """Return the ComfyUI ``models`` directory, or "" if it can't be resolved.

    Resolution order:
      1. ``<comfyui.root>/models`` if comfyui.root is set.
      2. Else derive from the folder containing ``comfyui.exe``: search that
         folder and up to 2 levels up for a ``models`` subdirectory.
      3. Else "" (with a printed note).
    """
    comfy = _comfy_cfg(_cfg_or(cfg))

    # 1) Explicit install root wins.
    root = str(comfy.get("root") or "").strip()
    if root:
        return os.path.join(os.path.abspath(root), "models")

    # 2) Derive from the launcher's location.
    exe = str(comfy.get("exe") or "").strip()
    if exe:
        base = os.path.dirname(os.path.abspath(exe))
        # Walk the exe's folder + up to 2 parents looking for a `models` dir.
        cur = base
        for _ in range(3):
            candidate = os.path.join(cur, "models")
            if os.path.isdir(candidate):
                return candidate
            parent = os.path.dirname(cur)
            if parent == cur:  # reached filesystem root
                break
            cur = parent

    # 3) Unknown - let the caller / Doctor surface this.
    print("[models] NOTE: ComfyUI models root unknown - set comfyui.root or comfyui.exe in config.yaml")
    return ""


_KIND_SUBDIR = {"lora": "loras", "controlnet": "controlnet", "checkpoint": "checkpoints"}


def dest_dir(kind: str, cfg: Dict[str, Any]) -> str:
    """Return the install directory for ``kind`` ('lora', 'controlnet', 'checkpoint').

    Maps to ``<models_root>/{loras,controlnet,checkpoints}``. Creates the
    directory if the models root is known but the subfolder is missing. Returns
    "" if the models root couldn't be resolved.
    """
    root = models_root(_cfg_or(cfg))
    if not root:
        return ""

    sub = _KIND_SUBDIR.get(_norm_kind(kind), "loras")
    target = os.path.join(root, sub)
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        print(f"[models] WARNING: could not create {target} ({exc})")
    return target


def _norm_kind(kind: Optional[str]) -> str:
    """Normalise a kind string to 'lora', 'controlnet', or 'checkpoint' (default 'lora')."""
    k = (kind or "lora").strip().lower()
    if k in ("controlnet", "control_net", "control-net", "cn"):
        return "controlnet"
    if k in ("checkpoint", "ckpt", "model", "base", "base_model"):
        return "checkpoint"
    return "lora"


def _civitai_type(kind: str) -> str:
    """Civitai `types` query value for our kind."""
    nk = _norm_kind(kind)
    return {"controlnet": "Controlnet", "checkpoint": "Checkpoint"}.get(nk, "LORA")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(query: str, kind: str = "lora", cfg: Optional[Dict[str, Any]] = None,
           limit: int = 20) -> List[Dict[str, Any]]:
    """Search Civitai for models of ``kind`` matching ``query``.

    GETs ``{api}/models?query=&types=&limit=&sort=Highest Rated`` and returns a
    normalised list of dicts::

        {id, name, kind, creator, thumb, stats,
         files: [{name, downloadUrl, sizeKB, type, primary}]}

    Files + a preview thumbnail are pulled from the FIRST modelVersion. All
    field access is tolerant of missing/renamed keys.
    """
    requests = _require_requests()
    cfg = _cfg_or(cfg)

    params = {
        "query": query or "",
        "types": _civitai_type(kind),
        "limit": max(1, int(limit or 20)),
        "sort": "Highest Rated",
    }
    url = _api_base(cfg) + "/models"

    try:
        resp = requests.get(url, params=params, headers=_auth_headers(cfg), timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Civitai search failed for '{query}': {exc}") from exc

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    return [_normalize_model(it, kind) for it in items if isinstance(it, dict)]


def _normalize_model(item: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """Flatten a Civitai model item into our normalised shape (tolerant)."""
    creator = item.get("creator") or {}
    creator_name = creator.get("username") if isinstance(creator, dict) else None

    versions = item.get("modelVersions")
    first_ver = versions[0] if isinstance(versions, list) and versions else {}
    if not isinstance(first_ver, dict):
        first_ver = {}

    # Files from the first version.
    files_out: List[Dict[str, Any]] = []
    for f in (first_ver.get("files") or []):
        if not isinstance(f, dict):
            continue
        files_out.append({
            "name": f.get("name"),
            "downloadUrl": f.get("downloadUrl"),
            "sizeKB": f.get("sizeKB"),
            "type": f.get("type"),
            "primary": bool(f.get("primary", False)),
        })

    # Preview thumbnail = first image url from the first version.
    thumb = None
    for img in (first_ver.get("images") or []):
        if isinstance(img, dict) and img.get("url"):
            thumb = img.get("url")
            break

    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "kind": _norm_kind(kind),
        "creator": creator_name,
        "thumb": thumb,
        "stats": item.get("stats") or {},
        "files": files_out,
    }


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download(download_url: str, dest_dir: str, filename: str,
             cfg: Optional[Dict[str, Any]] = None,
             progress: Optional[Callable[[float], None]] = None) -> str:
    """Stream-download ``download_url`` to ``dest_dir/filename``.

    Writes to a ``.part`` temp file first, then atomically renames on success so
    a partial download never masquerades as a finished model. Sends the Civitai
    token header if configured. ``progress`` is an optional callable(pct) where
    pct is 0-100 (only called when the server reports a Content-Length).

    Returns the final absolute path. Raises RuntimeError on HTTP failure.
    """
    requests = _require_requests()
    cfg = _cfg_or(cfg)

    if not download_url:
        raise RuntimeError("download() requires a download_url")
    if not dest_dir:
        raise RuntimeError("download() requires a dest_dir (ComfyUI models root unknown?)")

    try:
        os.makedirs(dest_dir, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Could not create destination dir {dest_dir}: {exc}") from exc

    final_path = os.path.join(dest_dir, filename)
    part_path = final_path + ".part"

    try:
        with requests.get(download_url, headers=_auth_headers(cfg),
                          stream=True, timeout=60, allow_redirects=True) as resp:
            resp.raise_for_status()

            total = 0
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0

            done = 0
            with open(part_path, "wb") as out:
                for chunk in resp.iter_content(chunk_size=1024 * 256):  # 256 KB
                    if not chunk:
                        continue
                    out.write(chunk)
                    done += len(chunk)
                    if progress and total > 0:
                        try:
                            progress(min(100.0, done * 100.0 / total))
                        except Exception:
                            pass  # progress callbacks must never break a download
    except Exception as exc:  # noqa: BLE001
        # Clean up any partial file so a retry starts fresh.
        try:
            if os.path.exists(part_path):
                os.remove(part_path)
        except OSError:
            pass
        raise RuntimeError(f"Download failed from {download_url}: {exc}") from exc

    # Atomic-ish finalise: replace any stale file then rename the .part in.
    try:
        if os.path.exists(final_path):
            os.remove(final_path)
        os.replace(part_path, final_path)
    except OSError as exc:
        raise RuntimeError(f"Could not finalise download to {final_path}: {exc}") from exc

    if progress:
        try:
            progress(100.0)
        except Exception:
            pass

    return final_path


# ---------------------------------------------------------------------------
# Inventory + health
# ---------------------------------------------------------------------------

def list_installed(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    """Return installed model filenames grouped by kind.

    ``{"loras": [...], "controlnets": [...]}`` - only files with a recognised
    model extension are listed. Missing/unknown dirs yield empty lists.
    """
    cfg = _cfg_or(cfg)
    return {
        "loras": _list_dir(dest_dir("lora", cfg)),
        "controlnets": _list_dir(dest_dir("controlnet", cfg)),
        "checkpoints": _list_dir(dest_dir("checkpoint", cfg)),
    }


def _list_dir(path: str) -> List[str]:
    """List model files (by extension) in ``path``, sorted; [] if unusable."""
    if not path or not os.path.isdir(path):
        return []
    try:
        names = [
            n for n in os.listdir(path)
            if n.lower().endswith(_MODEL_EXTS) and os.path.isfile(os.path.join(path, n))
        ]
    except OSError:
        return []
    return sorted(names)


def available(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Report whether the models root is known AND writable (for Doctor/UI).

    Returns ``(ok, message)``. ``ok`` is True only when a models root resolves
    and we can create/write inside it.
    """
    cfg = _cfg_or(cfg)
    root = models_root(cfg)
    if not root:
        return (False, "ComfyUI models root unknown - set comfyui.root or comfyui.exe in config.yaml")

    # Probe writability by ensuring the loras dest can be created + written.
    target = dest_dir("lora", cfg)
    if not target:
        return (False, f"Could not resolve install dir under {root}")

    probe = os.path.join(target, ".conductor_write_test")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError as exc:
        return (False, f"Models root not writable: {target} ({exc})")

    return (True, f"Models root ready: {root}")
