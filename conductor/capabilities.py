"""Optional feature-group installer ("capabilities").

Core install stays small. Heavier features (native transparent gen, pose/animation,
AI upscale) are grouped, and each group declares exactly what it needs: ComfyUI
custom nodes (git), model files (download), pip packages, and post-clone patches.

The app checks a group's status and installs it ON DEMAND -- either when the user
clicks "Unlock" in the Doctor, or the first time a feature that needs it is used.
Nothing here downloads at import time; status() is cheap (filesystem checks).

Public API:
    groups()                       -> list of group metadata
    status(cfg)                    -> {group_id: {installed, missing, ...}}
    install(group_id, cfg, log)    -> bool  (log is an optional callable(str))
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from typing import Any, Callable, Dict, List, Optional

try:
    import cfg as _cfg  # flat layout
except ImportError:  # pragma: no cover
    from conductor import cfg as _cfg  # type: ignore


# --- ComfyUI location -------------------------------------------------------

def _comfy_root(cfg: Dict[str, Any]) -> Optional[str]:
    """Resolve the ComfyUI install dir that holds models/ and custom_nodes/."""
    comfy = (cfg.get("comfyui") or {}) if isinstance(cfg, dict) else {}
    root = comfy.get("root") or ""
    cands = []
    if root:
        cands += [root, os.path.join(root, "ComfyUI")]
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "models")):
            return c
    # last resort: a sibling ComfyUI checkout
    for c in ("E:/Tools/ComfyUI/ComfyUI", "E:/ComfyUI"):
        if os.path.isdir(os.path.join(c, "models")):
            return c
    return None


def _comfy_python(root: str) -> str:
    """ComfyUI portable ships an embedded python; fall back to this interpreter."""
    emb = os.path.join(os.path.dirname(root.rstrip("/\\")), "python_embeded", "python.exe")
    emb2 = os.path.join(root, "..", "python_embeded", "python.exe")
    for p in (emb, emb2):
        if os.path.isfile(p):
            return p
    return sys.executable


# --- Feature-group manifest -------------------------------------------------
# Each group: nodes (git repos -> custom_nodes), models (url -> relative dest under
# the ComfyUI root), pips (packages), patches (repo-relative script + node dir).

_HF = "https://huggingface.co"
GROUPS: Dict[str, Dict[str, Any]] = {
    "transparent": {
        "title": "Transparent generation (LayerDiffuse)",
        "why": "True transparent-background generation with real alpha (gen.transparent: native).",
        "nodes": [("ComfyUI-layerdiffuse", "https://github.com/huchenlei/ComfyUI-layerdiffuse")],
        "models": [],  # LayerDiffuse auto-downloads its models on first use
        "pips": [],
        "patches": [("tools/patch_layerdiffuse.py", "ComfyUI-layerdiffuse")],
    },
    "pose": {
        "title": "Pose control + character continuity",
        "why": "Keep the same hero across poses (IP-Adapter identity + ControlNet OpenPose). Basis for animation keyframes.",
        "nodes": [("ComfyUI_IPAdapter_plus", "https://github.com/cubiq/ComfyUI_IPAdapter_plus")],
        "models": [
            (_HF + "/comfyanonymous/ControlNet-v1-1_fp16_safetensors/resolve/main/control_v11p_sd15_openpose_fp16.safetensors",
             "models/controlnet/control_v11p_sd15_openpose_fp16.safetensors"),
            (_HF + "/h94/IP-Adapter/resolve/main/models/ip-adapter_sd15.bin",
             "models/ipadapter/ip-adapter_sd15.bin"),
            (_HF + "/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors",
             "models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"),
        ],
        "pips": [],
        "patches": [],
    },
    "animate": {
        "title": "Idle-loop animation (AnimateDiff)",
        "why": "Short looping ambient motion (breathing, cape flutter). VRAM-tight on 4GB; keep frame counts low.",
        "nodes": [
            ("ComfyUI-AnimateDiff-Evolved", "https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved"),
            ("ComfyUI-VideoHelperSuite", "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite"),
        ],
        "models": [
            (_HF + "/guoyww/animatediff/resolve/main/mm_sd_v15_v2.ckpt",
             "models/animatediff_models/mm_sd_v15_v2.ckpt"),
        ],
        "pips": [],
        "patches": [],
    },
    "upscale": {
        "title": "AI upscaling (Real-ESRGAN)",
        "why": "Sharper enlargements than the built-in LANCZOS fallback in the 'upscale' look-lab step.",
        "nodes": [],
        "models": [],
        "pips": ["realesrgan", "basicsr"],
        "patches": [],
    },
}


def groups() -> List[Dict[str, Any]]:
    return [{"id": gid, "title": g["title"], "why": g["why"]} for gid, g in GROUPS.items()]


# --- Status -----------------------------------------------------------------

def _pip_installed(pkg: str) -> bool:
    try:
        __import__(pkg)
        return True
    except Exception:
        return False


def status(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    cfg = cfg or _cfg.load_config()
    root = _comfy_root(cfg)
    out: Dict[str, Dict[str, Any]] = {}
    for gid, g in GROUPS.items():
        missing: List[str] = []
        if root:
            nodes_dir = os.path.join(root, "custom_nodes")
            for name, _url in g["nodes"]:
                if not os.path.isdir(os.path.join(nodes_dir, name)):
                    missing.append("node:" + name)
            for _url, dest in g["models"]:
                if not os.path.isfile(os.path.join(root, dest)):
                    missing.append("model:" + os.path.basename(dest))
        elif g["nodes"] or g["models"]:
            missing.append("comfyui-root-unknown")
        for pkg in g["pips"]:
            if not _pip_installed(pkg):
                missing.append("pip:" + pkg)
        out[gid] = {
            "title": g["title"], "why": g["why"],
            "installed": len(missing) == 0, "missing": missing,
        }
    return out


# --- Install ----------------------------------------------------------------

def _remote_size(url: str) -> int:
    """HEAD-ish: return the server's Content-Length, or 0 if unknown."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def _download(url: str, dest: str, log: Callable[[str], None]) -> bool:
    """Download with size verification + resume. HF connections drop mid-stream and
    a partial file passes a naive 'exists' check but is corrupt (safetensors then
    fails to load). We compare against the remote Content-Length and RESUME with a
    Range request until the file is complete, retrying a few times."""
    name = os.path.basename(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    expected = _remote_size(url)
    if os.path.isfile(dest) and expected and os.path.getsize(dest) >= expected:
        log("  have " + name); return True

    for attempt in range(6):
        have = os.path.getsize(dest) if os.path.isfile(dest) else 0
        if expected and have >= expected:
            log("  done " + name); return True
        headers = {"Range": "bytes=%d-" % have} if have else {}
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req) as r, open(dest, "ab" if have else "wb") as f:
                # If the server ignored Range (200 not 206), restart from scratch.
                if have and r.status == 200:
                    f.close(); open(dest, "wb").close()
                    f = open(dest, "wb"); have = 0
                total = (int(r.headers.get("Content-Length") or 0) + have) or expected
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk); have += len(chunk)
                    if total:
                        log("  %s %d%%" % (name, min(100, have * 100 // total)))
        except Exception as e:
            log("  retry %d %s: %s" % (attempt + 1, name, e))
            continue
        if not expected:  # unknown remote size -> accept a non-empty file
            if os.path.getsize(dest) > 1024:
                log("  done " + name); return True

    ok = os.path.isfile(dest) and (not expected or os.path.getsize(dest) >= expected)
    log(("  done " if ok else "  FAILED (incomplete) ") + name)
    return ok


def _git_clone(url: str, dest: str, log: Callable[[str], None]) -> bool:
    if os.path.isdir(dest):
        log("  have node " + os.path.basename(dest)); return True
    log("  cloning " + os.path.basename(dest) + " ...")
    try:
        subprocess.run(["git", "clone", "--depth", "1", url, dest],
                       check=True, capture_output=True, text=True)
        return True
    except Exception as e:
        log("  clone FAILED: " + str(e)); return False


def install(group_id: str, cfg: Optional[Dict[str, Any]] = None,
            log: Optional[Callable[[str], None]] = None) -> bool:
    cfg = cfg or _cfg.load_config()
    log = log or (lambda s: None)
    g = GROUPS.get(group_id)
    if not g:
        log("unknown group: " + group_id); return False
    log("Installing '%s'..." % g["title"])
    ok = True
    root = _comfy_root(cfg)

    if (g["nodes"] or g["models"] or g["patches"]) and not root:
        log("ComfyUI install dir not found; set comfyui.root in config.yaml."); return False

    # nodes
    for name, url in g["nodes"]:
        dest = os.path.join(root, "custom_nodes", name)
        if not _git_clone(url, dest, log):
            ok = False
        else:
            reqs = os.path.join(dest, "requirements.txt")
            if os.path.isfile(reqs):
                try:
                    subprocess.run([_comfy_python(root), "-m", "pip", "install", "-r", reqs],
                                   check=False, capture_output=True, text=True)
                except Exception:
                    pass
    # models
    for url, dest in g["models"]:
        if not _download(url, os.path.join(root, dest), log):
            ok = False
    # pip packages (into ComfyUI's python if we have a root, else this interpreter)
    if g["pips"]:
        py = _comfy_python(root) if root else sys.executable
        for pkg in g["pips"]:
            log("  pip install " + pkg + " ...")
            try:
                subprocess.run([py, "-m", "pip", "install", pkg],
                               check=False, capture_output=True, text=True)
            except Exception as e:
                log("  pip FAILED " + pkg + ": " + str(e)); ok = False
    # patches (repo-relative script applied to a cloned node dir)
    for script_rel, node_name in g["patches"]:
        script = _cfg.resolve(script_rel)
        node_dir = os.path.join(root, "custom_nodes", node_name)
        if os.path.isfile(script) and os.path.isdir(node_dir):
            log("  patching " + node_name + " ...")
            try:
                subprocess.run([sys.executable, script, node_dir],
                               check=False, capture_output=True, text=True)
            except Exception as e:
                log("  patch FAILED: " + str(e)); ok = False

    log("Done." if ok else "Finished with some failures (see log).")
    return ok
