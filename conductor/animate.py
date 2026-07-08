"""animate.py - the Animation art path.

Two modes, both local, built on the verified workflows:

  keyframes : same hero in chosen poses. IP-Adapter locks the hero's identity
              from a reference image, ControlNet OpenPose sets each pose, a fixed
              seed keeps takes consistent. One image per pose -> sprite frames.
              (feature pack: "pose")

  idle      : short looping ambient animation (breathing, cape flutter) via
              AnimateDiff -> a GIF + its frames. (feature pack: "animate")

Both auto-install their feature pack on first use (capabilities.install), so the
user never hand-installs a node. Heavy engine imports stay lazy.

Public API (jobs.py + server.py call these):
    list_poses()                                  -> [{"id","label","path"}]
    keyframes(hero, poses, prompt, cfg, out_dir, ...) -> [frame paths]
    idle(prompt, cfg, out_dir, frames, size, ...)     -> {"gif","frames"}
"""
from __future__ import annotations

import glob
import json
import os
import shutil
from typing import Any, Callable, Dict, List, Optional

try:
    import cfg as _cfg
    import comfyui as _comfyui
    import capabilities as _caps
except ImportError:  # pragma: no cover - package layout
    from conductor import cfg as _cfg  # type: ignore
    from conductor import comfyui as _comfyui  # type: ignore
    from conductor import capabilities as _caps  # type: ignore


_STYLE_SUFFIX = "full body character, clean crisp edges, high detail"
_DEFAULT_NEG = ("blurry, lowres, text, watermark, signature, deformed, bad anatomy, "
                "extra limbs, extra fingers, cropped, multiple characters")


def _poses_dir() -> str:
    return _cfg.resolve("app/poses")


def list_poses() -> List[Dict[str, str]]:
    """Preset OpenPose skeletons shipped in poses/. id = filename stem."""
    d = _poses_dir()
    out = []
    for p in sorted(glob.glob(os.path.join(d, "*.png"))):
        stem = os.path.splitext(os.path.basename(p))[0]
        out.append({"id": stem, "label": stem.replace("_", " ").title(), "path": p})
    return out


def _pose_path(pose_id: str) -> Optional[str]:
    p = os.path.join(_poses_dir(), pose_id + ".png")
    return p if os.path.isfile(p) else None


def _load_workflow(rel: str) -> Dict[str, Any]:
    with open(_cfg.resolve(rel), "r", encoding="utf-8") as fh:
        wf = json.load(fh)
    wf.pop("_doc", None)
    return wf


def ensure_pack(group: str, cfg: Dict[str, Any], log: Callable[[str], None]) -> bool:
    """Install a feature pack on demand if it isn't already present."""
    st = _caps.status(cfg).get(group, {})
    if st.get("installed"):
        return True
    log("Unlocking the '%s' feature pack (first-time download)..." % group)
    return _caps.install(group, cfg, log)


# --- keyframes (pose control) ----------------------------------------------

def keyframes(hero: str, poses: List[str], prompt: str, cfg: Dict[str, Any],
              out_dir: str, negative: str = "", identity: float = 0.7,
              pose_strength: float = 1.0, seed: int = 42,
              log: Optional[Callable[[str], None]] = None) -> List[str]:
    """Render `hero` (a reference image) into each pose in `poses` (preset ids).
    Returns the frame paths (frame_00.png ...). Same seed across poses for a
    consistent character."""
    log = log or (lambda s: None)
    cfg = cfg or _cfg.load_config()
    if not _comfyui.ensure_up(cfg):
        raise RuntimeError("ComfyUI is not running.")
    if not ensure_pack("pose", cfg, log):
        raise RuntimeError("Could not install the 'pose' feature pack.")

    url = (cfg.get("comfyui") or {}).get("url", "http://127.0.0.1:8188")
    comfy = cfg.get("comfyui") or {}
    gen = cfg.get("gen") or {}
    tmpl = _load_workflow("workflows/sd15_pose_keyframe.json")
    os.makedirs(out_dir, exist_ok=True)

    hero_name = _comfyui.upload_image(url, hero)
    pos = (prompt.strip() + ", " + _STYLE_SUFFIX) if prompt.strip() else _STYLE_SUFFIX
    neg = (negative or "").strip() or _DEFAULT_NEG

    frames: List[str] = []
    for i, pid in enumerate(poses):
        ppath = _pose_path(pid)
        if not ppath:
            log("skip unknown pose: " + pid); continue
        pose_name = _comfyui.upload_image(url, ppath)
        wf = json.loads(json.dumps(tmpl))  # deep copy
        wf["4"]["inputs"]["ckpt_name"] = comfy.get("checkpoint", "DreamShaper_8_pruned.safetensors")
        wf["42"]["inputs"]["image"] = hero_name
        wf["44"]["inputs"]["image"] = pose_name
        wf["6"]["inputs"]["text"] = pos
        wf["7"]["inputs"]["text"] = neg
        wf["41"]["inputs"]["weight"] = float(identity)
        wf["45"]["inputs"]["strength"] = float(pose_strength)
        wf["5"]["inputs"]["width"] = int(gen.get("width", 512))
        wf["5"]["inputs"]["height"] = int(gen.get("height", 768))
        wf["3"]["inputs"]["seed"] = int(seed)       # SAME seed -> consistent hero
        wf["3"]["inputs"]["steps"] = int(gen.get("steps", 28))
        wf["3"]["inputs"]["cfg"] = float(gen.get("cfg", 7.0))
        log("posing frame %d/%d (%s)..." % (i + 1, len(poses), pid))
        pid_out = _comfyui.submit(url, wf, _comfyui.new_client_id())
        images = _comfyui.wait(url, pid_out, timeout=600)
        if not images:
            raise RuntimeError("no image for pose " + pid)
        dst = os.path.join(out_dir, "frame_%02d.png" % i)
        shutil.copyfile(images[0], dst)
        frames.append(dst)
    return frames


# --- idle loop (AnimateDiff) -----------------------------------------------

def idle(prompt: str, cfg: Dict[str, Any], out_dir: str, frames: int = 16,
         size: int = 512, seed: int = 7, negative: str = "", fps: int = 8,
         log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Generate a looping idle animation. Returns {"gif": path, "frames": [paths]}.
    On a 4GB card this pages to RAM (slow); keep frames low and size <= 384."""
    log = log or (lambda s: None)
    cfg = cfg or _cfg.load_config()
    if not _comfyui.ensure_up(cfg):
        raise RuntimeError("ComfyUI is not running.")
    if not ensure_pack("animate", cfg, log):
        raise RuntimeError("Could not install the 'animate' feature pack.")

    url = (cfg.get("comfyui") or {}).get("url", "http://127.0.0.1:8188")
    comfy = cfg.get("comfyui") or {}
    gen = cfg.get("gen") or {}
    size = max(256, min(768, int(size)))
    frames = max(8, min(32, int(frames)))
    wf = _load_workflow("workflows/sd15_animatediff.json")
    wf["4"]["inputs"]["ckpt_name"] = comfy.get("checkpoint", "DreamShaper_8_pruned.safetensors")
    wf["6"]["inputs"]["text"] = (prompt.strip() + ", " + _STYLE_SUFFIX) if prompt.strip() else _STYLE_SUFFIX
    wf["7"]["inputs"]["text"] = (negative or "").strip() or _DEFAULT_NEG
    wf["5"]["inputs"]["width"] = size
    wf["5"]["inputs"]["height"] = size
    wf["5"]["inputs"]["batch_size"] = frames
    wf["51"]["inputs"]["context_length"] = min(16, frames)
    wf["3"]["inputs"]["seed"] = int(seed)
    wf["3"]["inputs"]["steps"] = int(gen.get("steps", 20))
    wf["60"]["inputs"]["frame_rate"] = int(fps)
    os.makedirs(out_dir, exist_ok=True)

    log("rendering %d-frame idle loop @ %dpx (this is slow on 4GB)..." % (frames, size))
    pid = _comfyui.submit(url, wf, _comfyui.new_client_id())
    images = _comfyui.wait(url, pid, timeout=1800)  # AnimateDiff is slow
    frame_paths = []
    for i, img in enumerate(images or []):
        dst = os.path.join(out_dir, "frame_%02d.png" % i)
        shutil.copyfile(img, dst); frame_paths.append(dst)
    # find the GIF VHS wrote (comfy output dir), copy next to frames
    gif_dst = os.path.join(out_dir, "idle.gif")
    comfy_out = _comfy_output_dir(cfg)
    gifs = sorted(glob.glob(os.path.join(comfy_out, "dc_idle*.gif")), key=os.path.getmtime) if comfy_out else []
    if gifs:
        shutil.copyfile(gifs[-1], gif_dst)
    return {"gif": gif_dst if os.path.isfile(gif_dst) else None, "frames": frame_paths}


def _comfy_output_dir(cfg: Dict[str, Any]) -> Optional[str]:
    root = (cfg.get("comfyui") or {}).get("root") or ""
    for c in ([os.path.join(root, "output"), os.path.join(root, "ComfyUI", "output")] if root else []) + \
             ["E:/Tools/ComfyUI/ComfyUI/output", "E:/ComfyUI/output"]:
        if os.path.isdir(c):
            return c
    return None
