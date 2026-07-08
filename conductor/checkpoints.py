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

from typing import Any, Dict, List, Optional

try:
    from . import models as modelsmod  # type: ignore
except Exception:  # loose-script path
    import models as modelsmod  # type: ignore


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
]

_BY_ID = {c["id"]: c for c in CATALOG}


def catalog() -> List[Dict[str, Any]]:
    """The raw curated list (copies, so callers can annotate freely)."""
    return [dict(c) for c in CATALOG]


def by_id(cid: str) -> Optional[Dict[str, Any]]:
    return _BY_ID.get((cid or "").strip())


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
