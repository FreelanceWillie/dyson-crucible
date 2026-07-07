"""
rank.py - a CLIP-based "taste proxy" that ranks generated candidates.

The conductor gens a handful of SD1.5 candidates per turn. Rather than make you
eyeball all of them, this module scores each candidate by how visually similar
it is to YOUR OWN reference images (the style you're chasing), using CLIP image
embeddings. It is a proxy for your taste: "which of these looks most like the
vibe I picked references for?"

How it works:
  1. Embed every reference image, L2-normalize, and MEAN-pool them into ONE
     reference vector (the centroid of your style).
  2. Embed each candidate, cosine-sim to that reference vector.
  3. Map cosine [-1, 1] -> [0, 1] and sort best-first.

Everything heavy (open_clip + torch) is imported LAZILY and the loaded model is
cached in a module global, so importing this file is instant and cheap; the cost
is only paid on the first rank() call.

Fully local, $0. If you have no references yet, ranking is a no-op that returns
the candidates in their original order.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple


# Common raster extensions we treat as images when scanning a reference folder.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}

# Pretrained weights that pair with the ViT-B-32 architecture. This is a strong,
# widely-available open_clip checkpoint and a sensible default for art matching.
_DEFAULT_PRETRAINED = "laion2b_s34b_b79k"

# Module-global cache: (clip_model_name) -> (model, preprocess, device, tokenizer?).
# open_clip model construction is slow, so we build once per process per model.
_MODEL_CACHE: dict = {}


def _load_model(clip_model: str):
    """Lazily build + cache an open_clip model and its preprocess transform.

    Returns (model, preprocess, device). Raises a clear RuntimeError if the
    dependencies are not installed, telling the user to run setup.
    """
    cached = _MODEL_CACHE.get(clip_model)
    if cached is not None:
        return cached

    try:
        import torch  # type: ignore
        import open_clip  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "CLIP ranking needs 'open_clip_torch' and 'torch'. They are not "
            "installed. Run the project setup (e.g. pip install open_clip_torch "
            "torch) and try again."
        ) from exc

    # Prefer CUDA when present; fall back to CPU (slower but works everywhere).
    device = "cuda" if getattr(torch, "cuda", None) and torch.cuda.is_available() else "cpu"

    # open_clip pairs an architecture name with a pretrained tag. We default the
    # pretrained tag but let the model name flow straight through from config.
    model, _, preprocess = open_clip.create_model_and_transforms(
        clip_model, pretrained=_DEFAULT_PRETRAINED
    )
    model = model.to(device)
    model.eval()

    _MODEL_CACHE[clip_model] = (model, preprocess, device)
    return _MODEL_CACHE[clip_model]


def _list_reference_images(reference_dir: str) -> List[str]:
    """Recursively collect image files under `reference_dir` (sorted, stable)."""
    if not reference_dir or not os.path.isdir(reference_dir):
        return []
    found: List[str] = []
    for root, _dirs, files in os.walk(reference_dir):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext in _IMAGE_EXTS:
                found.append(os.path.join(root, fn))
    return sorted(found)


def _embed_images(paths: List[str], model, preprocess, device):
    """Embed a list of image paths into an L2-normalized tensor [N, D].

    Silently skips images that fail to open/preprocess (a corrupt candidate
    should not crash the whole ranking). Returns the tensor plus the parallel
    list of paths that actually embedded, so callers can keep them aligned.
    """
    import torch  # type: ignore
    from PIL import Image  # type: ignore

    tensors = []
    ok_paths: List[str] = []
    for p in paths:
        try:
            img = Image.open(p).convert("RGB")
        except Exception:  # noqa: BLE001 - skip unreadable files
            continue
        try:
            tensors.append(preprocess(img))
            ok_paths.append(p)
        except Exception:  # noqa: BLE001
            continue

    if not tensors:
        return None, []

    batch = torch.stack(tensors).to(device)
    with torch.no_grad():
        feats = model.encode_image(batch)
        # L2-normalize so cosine similarity is just a dot product.
        feats = feats / feats.norm(dim=-1, keepdim=True).clamp_min(1e-8)
    return feats, ok_paths


def rank(
    candidate_paths: List[str],
    reference_dir: str,
    clip_model: str = "ViT-B-32",
) -> List[Tuple[str, float]]:
    """Rank `candidate_paths` by CLIP similarity to the images in `reference_dir`.

    Returns a list of (path, score) tuples, BEST FIRST, with score in [0, 1]
    (cosine similarity remapped from [-1, 1]).

    Special cases:
      - No reference images  -> returns candidates in INPUT order, all score 0.0,
        and prints a one-line note (no references means nothing to rank against).
      - A candidate that fails to embed is dropped from the results.
    """
    candidate_paths = list(candidate_paths or [])
    if not candidate_paths:
        return []

    ref_paths = _list_reference_images(reference_dir)
    if not ref_paths:
        # Nothing to compare against: pass candidates through untouched.
        print(
            "[rank] no reference images in {0!r}; returning candidates unranked "
            "(score 0.0).".format(reference_dir)
        )
        return [(p, 0.0) for p in candidate_paths]

    import torch  # type: ignore  # local import: only needed on the real path

    model, preprocess, device = _load_model(clip_model)

    # Build the single reference centroid: mean of normalized ref embeddings.
    ref_feats, ok_refs = _embed_images(ref_paths, model, preprocess, device)
    if ref_feats is None or not ok_refs:
        print(
            "[rank] reference folder had files but none could be embedded; "
            "returning candidates unranked (score 0.0)."
        )
        return [(p, 0.0) for p in candidate_paths]

    with torch.no_grad():
        ref_vec = ref_feats.mean(dim=0, keepdim=True)
        # Re-normalize the centroid so cosine stays well-defined.
        ref_vec = ref_vec / ref_vec.norm(dim=-1, keepdim=True).clamp_min(1e-8)

    # Embed candidates and score each against the centroid.
    cand_feats, ok_cands = _embed_images(candidate_paths, model, preprocess, device)
    if cand_feats is None or not ok_cands:
        print("[rank] no candidate could be embedded; nothing to rank.")
        return []

    with torch.no_grad():
        # Cosine sim in [-1, 1] because both sides are L2-normalized.
        sims = (cand_feats @ ref_vec.T).squeeze(-1)
        # Map to [0, 1] so downstream code / UI has a clean 0..1 score.
        scores = ((sims + 1.0) * 0.5).clamp(0.0, 1.0)
        score_list = scores.detach().cpu().tolist()

    ranked = list(zip(ok_cands, score_list))
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


def best(
    candidate_paths: List[str],
    reference_dir: str,
    clip_model: str = "ViT-B-32",
) -> Optional[str]:
    """Convenience: return just the single best-matching candidate path (or None)."""
    ranked = rank(candidate_paths, reference_dir, clip_model)
    return ranked[0][0] if ranked else None
