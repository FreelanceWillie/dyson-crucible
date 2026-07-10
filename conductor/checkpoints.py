"""Curated checkpoint catalog: the "art style engine" behind every generation.

The base checkpoint sets the whole aesthetic and is the single biggest quality
lever on a 4GB SD1.5 box. Rather than drop the non-technical user into Civitai's
firehose, we ship a SMALL hand-picked list of SD1.5 checkpoints, each labelled by
what subject it is good at, all pruned fp16 (~2 GB) and safe on a 4GB card at 512.

One-click install downloads the file into ComfyUI's ``models/checkpoints`` (via
``models.download``, reusing its atomic ``.part`` streaming + progress), and
selecting one writes ``comfyui.checkpoint`` in config.yaml. gen.py injects that
name into the workflow, so the switch is immediate on the next generation.

URLs are keyless Hugging Face ``resolve`` links (verified reachable). If a link
ever rots, the entry still shows in the picker but install reports a clear error;
the user can also install any checkpoint by hand into models/checkpoints and it
appears in the installed list automatically.
"""

import json
import os
from typing import Any, Dict, List, Optional

try:
    from . import models as modelsmod  # type: ignore
except Exception:  # loose-script path
    import models as modelsmod  # type: ignore

try:
    from . import cfg as _cfg  # type: ignore
except Exception:
    import cfg as _cfg  # type: ignore


# Each entry: id (stable slug), name (friendly), best_for (one-line plain copy),
# tags (short chips), filename (as saved + injected into ComfyUI), url, size_mb.
CATALOG: List[Dict[str, Any]] = [
    {
        "id": "dreamshaper8",
        "name": "DreamShaper 8",
        "best_for": "Versatile all-rounder. Great first pick for characters, creatures, and concepts.",
        "tags": ["Characters", "Fantasy", "Versatile"],
        "filename": "DreamShaper_8_pruned.safetensors",
        "url": "https://huggingface.co/Lykon/DreamShaper/resolve/main/DreamShaper_8_pruned.safetensors",
        "size_mb": 2034,
    },
    {
        "id": "revanimated",
        "name": "ReV Animated",
        "best_for": "Fantasy and RPG heroes, semi-stylized. Strong for game character art.",
        "tags": ["Fantasy", "RPG", "Game art"],
        "filename": "revAnimated_v122.safetensors",
        "url": "https://huggingface.co/hanafuusen2001/ReVAnimated/resolve/main/revAnimated_v122.safetensors",
        "size_mb": 2034,
    },
    {
        "id": "toonyou",
        "name": "ToonYou",
        "best_for": "Toon and 2D game art, clean stylized characters and mascots.",
        "tags": ["Toon", "2D", "Stylized"],
        "filename": "toonyou_beta6.safetensors",
        "url": "https://huggingface.co/frankjoshua/toonyou_beta6/resolve/main/toonyou_beta6.safetensors",
        "size_mb": 2193,
    },
    {
        "id": "realisticvision51",
        "name": "Realistic Vision 5.1",
        "best_for": "Photoreal people, creatures, and props. Grounded, detailed textures.",
        "tags": ["Realistic", "Photoreal", "Detail"],
        "filename": "Realistic_Vision_V5.1_fp16-no-ema.safetensors",
        "url": "https://huggingface.co/SG161222/Realistic_Vision_V5.1_noVAE/resolve/main/Realistic_Vision_V5.1_fp16-no-ema.safetensors",
        "size_mb": 2034,
    },
    {
        "id": "majicmix7",
        "name": "majicMIX Realistic",
        "best_for": "Portraits and faces with a real-meets-stylized look. Clean anatomy.",
        "tags": ["Portraits", "Faces", "Realistic"],
        "filename": "majicmixRealistic_v7.safetensors",
        "url": "https://huggingface.co/digiplay/majicMIX_realistic_v7/resolve/main/majicmixRealistic_v7.safetensors",
        "size_mb": 2034,
    },
    {
        "id": "gameicons3d",
        "name": "Game Icons 3D",
        "best_for": "Items, props, weapons, potions, and icons on clean backgrounds. Objects, not people.",
        "tags": ["Items", "Props", "Icons", "Objects", "Weapons"],
        "filename": "GameIcons3D.safetensors",
        "url": "https://huggingface.co/Yntec/GameIcons3D/resolve/main/GameIcons3D.safetensors",
        "size_mb": 4067,  # full precision (~4GB); runs on a 4GB card via --lowvram
    },
    {
        "id": "counterfeit25",
        "name": "Counterfeit 2.5",
        "best_for": "Anime and manga style characters and scenes, clean line art.",
        "tags": ["Anime", "Manga", "Stylized"],
        "filename": "Counterfeit-V2.5_fp16.safetensors",
        "url": "https://huggingface.co/gsdf/Counterfeit-V2.5/resolve/main/Counterfeit-V2.5_fp16.safetensors",
        "size_mb": 2033,
    },
    {
        "id": "pixelkicks",
        "name": "PixelKicks",
        "best_for": "Native pixel-art sprites and scenes, retro 8/16-bit game look.",
        "tags": ["Pixel", "Retro", "Sprites", "2D"],
        "filename": "pixelkicks_01.safetensors",
        "url": "https://huggingface.co/Yntec/PixelKicks/resolve/main/pixelkicks_01.safetensors",
        "size_mb": 2033,
    },
    {
        "id": "dreamworks3d",
        "name": "DreamWorks 3D",
        "best_for": "3D animated movie look (Pixar / DreamWorks style) characters and creatures.",
        "tags": ["3D", "Cartoon", "Stylized", "Characters"],
        "filename": "DreamWorks.safetensors",
        "url": "https://huggingface.co/Yntec/DreamWorks/resolve/main/DreamWorks.safetensors",
        "size_mb": 2448,
    },
]

# Optional local additions: a gitignored checkpoints_local.json next to the repo
# lets you add your OWN models to the picker without editing this file or having
# them tracked in git. Update never touches it (gitignored; the installer only
# downloads, never deletes). Format: {"models": [ {id, name, best_for, tags,
# filename, url, size_mb}, ... ]} (a bare list also works). See
# checkpoints_local.example.json.
_LOCAL_FILE = os.path.join(_cfg.REPO_ROOT, "checkpoints_local.json")


def _load_local() -> List[Dict[str, Any]]:
    if not os.path.isfile(_LOCAL_FILE):
        return []
    try:
        with open(_LOCAL_FILE, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception as exc:  # bad JSON must never break the picker
        print(f"[checkpoints] could not read checkpoints_local.json ({exc})")
        return []
    entries = data.get("models") if isinstance(data, dict) else data
    out = []
    for e in (entries or []):
        if not isinstance(e, dict) or not e.get("id") or not e.get("filename"):
            continue
        e = dict(e)
        e.setdefault("name", e["id"])
        e.setdefault("best_for", "")
        e.setdefault("tags", [])
        e.setdefault("size_mb", 0)
        e.setdefault("url", "")
        out.append(e)
    return out


def _all() -> List[Dict[str, Any]]:
    """Built-in catalog + any local additions (deduped by id, local wins)."""
    seen = {}
    for c in CATALOG + _load_local():
        seen[c["id"]] = c
    return list(seen.values())


def catalog() -> List[Dict[str, Any]]:
    """The full model list (built-in + local additions), as copies."""
    return [dict(c) for c in _all()]


def by_id(cid: str) -> Optional[Dict[str, Any]]:
    cid = (cid or "").strip()
    return next((c for c in _all() if c.get("id") == cid), None)


def _ckpt_dir(cfg: Dict[str, Any]) -> str:
    return modelsmod.dest_dir("checkpoint", cfg)


def installed(cfg: Dict[str, Any]) -> List[str]:
    """Filenames present in ComfyUI's models/checkpoints (catalog or hand-added)."""
    try:
        return modelsmod.list_installed(cfg).get("checkpoints", [])
    except Exception:
        return []


def active(cfg: Dict[str, Any]) -> str:
    """The checkpoint filename gen.py will inject (comfyui.checkpoint)."""
    return ((cfg.get("comfyui") or {}).get("checkpoint") or "").strip()


def status(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the picker needs: catalog annotated with installed/active state,
    the full installed list (including hand-added files), and the active name."""
    inst = set(installed(cfg))
    act = active(cfg)
    cat = catalog()
    for c in cat:
        c["installed"] = c["filename"] in inst
        c["active"] = c["filename"] == act
    return {"catalog": cat, "installed": sorted(inst), "active": act}


def install(cid: str, cfg: Dict[str, Any], progress=None) -> str:
    """Download a catalog checkpoint into models/checkpoints. Returns the final
    path. Raises RuntimeError on an unknown id or a download failure."""
    entry = by_id(cid)
    if not entry:
        raise RuntimeError("unknown checkpoint id: " + str(cid))
    dest = _ckpt_dir(cfg)
    if not dest:
        raise RuntimeError("ComfyUI models root unknown; is comfyui.root set?")
    return modelsmod.download(entry["url"], dest, entry["filename"], cfg, progress)
