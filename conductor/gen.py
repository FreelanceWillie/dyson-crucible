"""
gen.py - turn a brief into N candidate images.

Two engines are supported (selected by cfg['engine']):

  comfyui   : build the API-format workflow graph from the template, inject the
              brief's prompt/negative/seed/size/steps/cfg/checkpoint and (when
              the brief asks for it and references exist) an IP-Adapter branch;
              submit to the local ComfyUI server, wait, and copy the results out
              as cand_1.png .. cand_n.png. Self-heals (ensure_up) and retries.

  diffusers : a lazy, in-process HuggingFace `diffusers` fallback tuned for a
              4GB GPU (fp16 + attention/vae slicing). No queue, no ComfyUI.

Public contract (server.py + jobs.py call this):

    generate(brief: dict, n: int, out_dir: str, cfg: dict) -> list[str]

Heavy imports (torch/diffusers) are done lazily inside the diffusers path so
merely importing this module is cheap and never fails on a machine without them.
"""

from __future__ import annotations

import copy
import glob
import json
import os
import random
import shutil
import time
from typing import Any, Dict, List, Optional

# Import the shared config helpers and the ComfyUI client. Support both
# "run as package" (from conductor.cfg import ...) and "run as flat scripts"
# (from cfg import ...), which is how the rest of the repo is wired.
try:  # flat layout: modules importable by bare name (matches brief.py's contract)
    import cfg as _cfg
    import comfyui as _comfyui
except ImportError:  # pragma: no cover - package layout fallback
    from conductor import cfg as _cfg  # type: ignore
    from conductor import comfyui as _comfyui  # type: ignore


# Node IDs in workflows/sd15_ipadapter.json that we overwrite. Kept in one place
# so a workflow edit only needs updating here (see the JSON's _doc block too).
_NODE_CHECKPOINT = "4"
_NODE_LATENT = "5"
_NODE_POS = "6"
_NODE_NEG = "7"
_NODE_KSAMPLER = "3"
_NODE_IP_LOADER = "10"
_NODE_IP_ADVANCED = "11"
_NODE_IP_LOADIMAGE = "12"

# Node IDs in workflows/sd15_multi_ipadapter.json (two stacked IP-Adapter
# branches). STYLE branch = 10/11/12 (shared with the single workflow), SUBJECT
# branch = 20/21/22. See that JSON's _doc block for the full wiring/bypass spec.
_MNODE_STYLE_LOADER = "10"
_MNODE_STYLE_ADVANCED = "11"
_MNODE_STYLE_LOADIMAGE = "12"
_MNODE_SUBJECT_LOADER = "20"
_MNODE_SUBJECT_ADVANCED = "21"
_MNODE_SUBJECT_LOADIMAGE = "22"

# Default multi-reference IP-Adapter weights (overridable per-brief).
_DEFAULT_STYLE_WEIGHT = 0.7
_DEFAULT_SUBJECT_WEIGHT = 0.8

# Nodes that consume the checkpoint's CLIP output (positive/negative encoders).
# LoRAs patch CLIP too, so these get repointed to the last LoraLoader's clip.
_CLIP_CONSUMERS = (_NODE_POS, _NODE_NEG)

# A light style suffix appended to every positive prompt. Kept gentle so it
# steers toward clean game-art without overriding the brief.
_STYLE_SUFFIX = "clean game art, crisp, high quality"

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------
def _find_references(reference_set: str) -> List[str]:
    """Return image paths in references/<set>/, falling back to references/default/."""
    refs_root = _cfg.path("references")
    candidates = []
    for name in (reference_set or "default", "default"):
        folder = os.path.join(refs_root, name)
        if os.path.isdir(folder):
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith(_IMAGE_EXTS):
                    candidates.append(os.path.join(folder, f))
            if candidates:
                break
    return candidates


# ---------------------------------------------------------------------------
# ComfyUI workflow assembly
# ---------------------------------------------------------------------------
def _load_workflow_template(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Load the API-format workflow JSON named in cfg['comfyui']['workflow']."""
    rel = (cfg.get("comfyui", {}) or {}).get("workflow", "workflows/sd15_ipadapter.json")
    wf_path = _cfg.resolve(rel)
    with open(wf_path, "r", encoding="utf-8") as fh:
        wf = json.load(fh)
    # Strip the documentation block; it is not a node and ComfyUI would reject it.
    wf.pop("_doc", None)
    return wf


def _transparent_mode(brief: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """Resolve the transparent-background mode: '' (off), 'cut' (generate then
    rembg the background off), or 'native' (LayerDiffuse true-alpha generation).
    Per-brief 'transparent' overrides gen.transparent. Accepts bool or string."""
    gen_cfg = cfg.get("gen", {}) or {}
    val = brief.get("transparent", gen_cfg.get("transparent", False))
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("native", "layerdiffuse", "layer_diffuse"):
            return "native"
        if v in ("cut", "rembg", "true", "1", "yes", "on"):
            return "cut"
        return ""
    return "cut" if val else ""


def _load_layerdiffuse_template(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Load the SD1.5 LayerDiffuse (native transparent) API workflow."""
    rel = (cfg.get("comfyui", {}) or {}).get(
        "workflow_layerdiffuse", "workflows/sd15_layerdiffuse.json"
    )
    wf_path = _cfg.resolve(rel)
    with open(wf_path, "r", encoding="utf-8") as fh:
        wf = json.load(fh)
    wf.pop("_doc", None)
    return wf


def _build_workflow_layerdiffuse(
    brief: Dict[str, Any], cfg: Dict[str, Any], template: Dict[str, Any], seed: int
) -> Dict[str, Any]:
    """Inject prompt/checkpoint/size/seed into the LayerDiffuse workflow. The graph
    is fixed (KSampler already reads the LayeredDiffusionApply model, SaveImage
    reads the RGBA decode), so this only fills the leaf fields, no IP-Adapter
    rewiring."""
    wf = copy.deepcopy(template)
    comfy = cfg.get("comfyui", {}) or {}
    gen = cfg.get("gen", {}) or {}
    prompt = (brief.get("prompt") or "").strip()
    positive = f"{prompt}, {_STYLE_SUFFIX}" if prompt else _STYLE_SUFFIX
    negative = (brief.get("negative") or "").strip()

    wf[_NODE_CHECKPOINT]["inputs"]["ckpt_name"] = comfy.get(
        "checkpoint", "v1-5-pruned-emaonly.safetensors"
    )
    wf[_NODE_POS]["inputs"]["text"] = positive
    wf[_NODE_NEG]["inputs"]["text"] = negative
    # LayerDiffuse's transparent VAE decoder requires 64-aligned dimensions.
    wf[_NODE_LATENT]["inputs"]["width"] = (int(gen.get("width", 512)) // 64) * 64
    wf[_NODE_LATENT]["inputs"]["height"] = (int(gen.get("height", 512)) // 64) * 64
    ks = wf[_NODE_KSAMPLER]["inputs"]
    ks["seed"] = seed
    ks["steps"] = int(gen.get("steps", 28))
    ks["cfg"] = float(gen.get("cfg", 7.0))
    return wf


def _load_multi_workflow_template(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Load the multi-IP-Adapter API-format workflow (cfg['comfyui']['workflow_multi'])."""
    rel = (cfg.get("comfyui", {}) or {}).get(
        "workflow_multi", "workflows/sd15_multi_ipadapter.json"
    )
    wf_path = _cfg.resolve(rel)
    with open(wf_path, "r", encoding="utf-8") as fh:
        wf = json.load(fh)
    wf.pop("_doc", None)
    return wf


def _parse_refs(brief: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize brief['refs'] into {'styles': [path,...], 'subject': path|None}.

    Returns None when there are no usable refs (caller then falls back to the
    single-ref / plain path). Tolerant of malformed entries: anything that is not
    a {role, path} dict with an existing file is skipped. Only the first subject
    ref is honored; extra subjects are ignored (with a note).
    """
    raw = brief.get("refs")
    if not raw or not isinstance(raw, (list, tuple)):
        return None

    styles: List[str] = []
    subject: Optional[str] = None
    extra_subjects = 0
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "").strip().lower()
        path = entry.get("path")
        if not path or not isinstance(path, str) or not os.path.isfile(path):
            continue
        if role == "style":
            styles.append(path)
        elif role == "subject":
            if subject is None:
                subject = path
            else:
                extra_subjects += 1
    if extra_subjects:
        print(f"[gen] {extra_subjects} extra subject ref(s) ignored (only the first is used)")

    if not styles and subject is None:
        return None
    return {"styles": styles, "subject": subject}


def _inject_loras(wf: Dict[str, Any], loras: Any) -> None:
    """Insert a LoraLoader chain between the checkpoint and its consumers.

    A LoRA patches BOTH the model and the CLIP, so it must sit right after the
    CheckpointLoaderSimple (node _NODE_CHECKPOINT) and before anything that reads
    model or clip from it. For each valid lora we add a LoraLoader node ('lora_0',
    'lora_1', ...) wired in sequence: the first reads model+clip from the
    checkpoint, each subsequent one from the previous LoraLoader. Then we repoint:

      * every CLIP consumer (the two CLIPTextEncode nodes) -> [last_lora, 1]
      * the model consumer (KSampler, or the first IP-Adapter loader if present)
        that currently reads model from the checkpoint -> [last_lora, 0]

    No-op (leaves `wf` untouched) when `loras` is empty or contains no usable
    entries, so the no-lora path is byte-identical to before. Tolerant of a
    malformed list: bad entries are skipped, not fatal.
    """
    if not loras or not isinstance(loras, (list, tuple)):
        return

    # Build the sanitized list of (name, weight) first; bail if none survive.
    clean: List[Dict[str, Any]] = []
    for entry in loras:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name or not isinstance(name, str):
            continue
        try:
            weight = float(entry.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        clean.append({"name": name, "weight": weight})
    if not clean:
        return

    if _NODE_CHECKPOINT not in wf:
        return

    # Chain the LoraLoader nodes. model+clip source starts at the checkpoint.
    src_model = [_NODE_CHECKPOINT, 0]
    src_clip = [_NODE_CHECKPOINT, 1]
    last_id = _NODE_CHECKPOINT
    for i, lora in enumerate(clean):
        nid = f"lora_{i}"
        wf[nid] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": lora["name"],
                "strength_model": lora["weight"],
                "strength_clip": lora["weight"],
                "model": src_model,
                "clip": src_clip,
            },
        }
        src_model = [nid, 0]
        src_clip = [nid, 1]
        last_id = nid

    # Repoint CLIP consumers (positive/negative encoders) onto the last LoRA.
    for nid in _CLIP_CONSUMERS:
        node = wf.get(nid)
        if isinstance(node, dict) and node.get("inputs", {}).get("clip") == [_NODE_CHECKPOINT, 1]:
            node["inputs"]["clip"] = [last_id, 1]

    # Repoint the model consumer: prefer the first IP-Adapter loader still in the
    # graph, else the KSampler. Whichever currently reads model from the
    # checkpoint gets pointed at the last LoRA's patched model output instead.
    model_consumers = (
        _NODE_IP_LOADER, _NODE_IP_ADVANCED,
        _MNODE_STYLE_LOADER, _MNODE_STYLE_ADVANCED,
        _MNODE_SUBJECT_LOADER, _MNODE_SUBJECT_ADVANCED,
        _NODE_KSAMPLER,
    )
    for nid in model_consumers:
        node = wf.get(nid)
        if isinstance(node, dict) and node.get("inputs", {}).get("model") == [_NODE_CHECKPOINT, 0]:
            node["inputs"]["model"] = [last_id, 0]


def _inject_common(
    wf: Dict[str, Any], brief: Dict[str, Any], cfg: Dict[str, Any], seed: int
) -> None:
    """Inject checkpoint/prompts/size/sampler shared by every workflow variant."""
    gen = cfg.get("gen", {}) or {}
    comfy = cfg.get("comfyui", {}) or {}

    prompt = (brief.get("prompt") or "").strip()
    positive = f"{prompt}, {_STYLE_SUFFIX}" if prompt else _STYLE_SUFFIX
    negative = (brief.get("negative") or "").strip()

    wf[_NODE_CHECKPOINT]["inputs"]["ckpt_name"] = comfy.get(
        "checkpoint", "v1-5-pruned-emaonly.safetensors"
    )
    wf[_NODE_POS]["inputs"]["text"] = positive
    wf[_NODE_NEG]["inputs"]["text"] = negative
    wf[_NODE_LATENT]["inputs"]["width"] = int(gen.get("width", 512))
    wf[_NODE_LATENT]["inputs"]["height"] = int(gen.get("height", 512))

    ks = wf[_NODE_KSAMPLER]["inputs"]
    ks["seed"] = int(seed)
    ks["steps"] = int(gen.get("steps", 28))
    ks["cfg"] = float(gen.get("cfg", 7.0))


def _build_multi_workflow(
    brief: Dict[str, Any],
    cfg: Dict[str, Any],
    template: Dict[str, Any],
    seed: int,
    style_filename: Optional[str],
    subject_filename: Optional[str],
) -> Dict[str, Any]:
    """Deep-copy the multi template and wire the STYLE and/or SUBJECT branches.

    Missing roles are bypassed exactly as the workflow _doc describes: the unused
    LoadImage/loader/adapter nodes are removed and the model chain is repointed so
    the graph stays valid. See sd15_multi_ipadapter.json '_doc'.'branch_bypass'.
    """
    wf = copy.deepcopy(template)
    comfy = cfg.get("comfyui", {}) or {}
    _inject_common(wf, brief, cfg, seed)

    have_style = bool(style_filename)
    have_subject = bool(subject_filename)

    style_weight = float(brief.get("style_weight", _DEFAULT_STYLE_WEIGHT) or _DEFAULT_STYLE_WEIGHT)
    subject_weight = float(
        brief.get("subject_weight", _DEFAULT_SUBJECT_WEIGHT) or _DEFAULT_SUBJECT_WEIGHT
    )

    ks = wf[_NODE_KSAMPLER]["inputs"]

    if have_style:
        wf[_MNODE_STYLE_LOADIMAGE]["inputs"]["image"] = style_filename
        wf[_MNODE_STYLE_ADVANCED]["inputs"]["weight"] = style_weight
        wf[_MNODE_STYLE_LOADER]["inputs"]["preset"] = comfy.get(
            "ip_adapter_style_preset", comfy.get("ip_adapter_preset", "PLUS")
        )
    else:
        for nid in (_MNODE_STYLE_LOADER, _MNODE_STYLE_ADVANCED, _MNODE_STYLE_LOADIMAGE):
            wf.pop(nid, None)

    if have_subject:
        wf[_MNODE_SUBJECT_LOADIMAGE]["inputs"]["image"] = subject_filename
        wf[_MNODE_SUBJECT_ADVANCED]["inputs"]["weight"] = subject_weight
        wf[_MNODE_SUBJECT_LOADER]["inputs"]["preset"] = comfy.get(
            "ip_adapter_subject_preset", comfy.get("ip_adapter_preset", "PLUS")
        )
        # If style is absent, stack the subject adapter directly on the checkpoint.
        if not have_style:
            wf[_MNODE_SUBJECT_LOADER]["inputs"]["model"] = [_NODE_CHECKPOINT, 0]
            wf[_MNODE_SUBJECT_ADVANCED]["inputs"]["model"] = [_NODE_CHECKPOINT, 0]
        ks["model"] = [_MNODE_SUBJECT_ADVANCED, 0]
    else:
        for nid in (_MNODE_SUBJECT_LOADER, _MNODE_SUBJECT_ADVANCED, _MNODE_SUBJECT_LOADIMAGE):
            wf.pop(nid, None)
        # No subject: KSampler reads from the style adapter, or raw checkpoint.
        ks["model"] = [_MNODE_STYLE_ADVANCED, 0] if have_style else [_NODE_CHECKPOINT, 0]

    loras = brief.get("loras")
    if loras:
        _inject_loras(wf, loras)

    return wf


def _build_workflow(
    brief: Dict[str, Any],
    cfg: Dict[str, Any],
    template: Dict[str, Any],
    seed: int,
    ref_filename: Optional[str],
) -> Dict[str, Any]:
    """Deep-copy the template and inject all per-candidate values by node id."""
    wf = copy.deepcopy(template)
    gen = cfg.get("gen", {}) or {}
    comfy = cfg.get("comfyui", {}) or {}

    prompt = (brief.get("prompt") or "").strip()
    positive = f"{prompt}, {_STYLE_SUFFIX}" if prompt else _STYLE_SUFFIX
    negative = (brief.get("negative") or "").strip()

    # Checkpoint.
    wf[_NODE_CHECKPOINT]["inputs"]["ckpt_name"] = comfy.get(
        "checkpoint", "v1-5-pruned-emaonly.safetensors"
    )

    # Prompts.
    wf[_NODE_POS]["inputs"]["text"] = positive
    wf[_NODE_NEG]["inputs"]["text"] = negative

    # Size.
    wf[_NODE_LATENT]["inputs"]["width"] = int(gen.get("width", 512))
    wf[_NODE_LATENT]["inputs"]["height"] = int(gen.get("height", 512))

    # Sampler.
    ks = wf[_NODE_KSAMPLER]["inputs"]
    ks["seed"] = int(seed)
    ks["steps"] = int(gen.get("steps", 28))
    ks["cfg"] = float(gen.get("cfg", 7.0))

    weight = float(brief.get("ip_adapter_weight", 0.0) or 0.0)
    use_ip = (
        weight > 0
        and ref_filename is not None
        and _NODE_IP_ADVANCED in wf
        and _NODE_IP_LOADER in wf
        and _NODE_IP_LOADIMAGE in wf
    )

    if use_ip:
        # Wire the reference + weight + preset, and route KSampler through the
        # IP-Adapter model output.
        wf[_NODE_IP_LOADIMAGE]["inputs"]["image"] = ref_filename
        wf[_NODE_IP_ADVANCED]["inputs"]["weight"] = weight
        wf[_NODE_IP_LOADER]["inputs"]["preset"] = comfy.get("ip_adapter_preset", "PLUS")
        ks["model"] = [_NODE_IP_ADVANCED, 0]
    else:
        # Bypass the IP-Adapter branch: point KSampler at the raw checkpoint
        # model and drop the now-unused nodes so ComfyUI does not try to run them.
        ks["model"] = [_NODE_CHECKPOINT, 0]
        for nid in (_NODE_IP_ADVANCED, _NODE_IP_LOADER, _NODE_IP_LOADIMAGE):
            wf.pop(nid, None)

    loras = brief.get("loras")
    if loras:
        _inject_loras(wf, loras)

    return wf


def _seed_for(index: int) -> int:
    """A fresh, well-varied seed per candidate.

    We seed python's RNG from (time + index) so candidates in the same run
    differ and reruns differ too. This runs on the dev's own machine, so a bit
    of wall-clock entropy is exactly what we want.
    """
    rng = random.Random()
    rng.seed(int(time.time() * 1000) + index * 7919)
    return rng.randint(0, 2**31 - 1)


def _generate_comfyui(
    brief: Dict[str, Any], n: int, out_dir: str, cfg: Dict[str, Any]
) -> List[str]:
    """Generate n candidates via the local ComfyUI server."""
    comfy = cfg.get("comfyui", {}) or {}
    url = comfy.get("url", "http://127.0.0.1:8188")
    queue_cfg = cfg.get("queue", {}) or {}
    max_retries = int(queue_cfg.get("max_retries", 3))

    if not _comfyui.ensure_up(cfg):
        raise RuntimeError(
            "ComfyUI is not available (see log above). Set comfyui.exe to enable "
            "auto-launch, or start ComfyUI manually."
        )

    # NATIVE transparent path (LayerDiffuse): a fixed graph that outputs true RGBA.
    # No IP-Adapter / multi / LoRA rewiring; just fill the leaf fields per candidate.
    if _transparent_mode(brief, cfg) == "native":
        try:
            ld_template = _load_layerdiffuse_template(cfg)
        except Exception as exc:  # noqa: BLE001 - fall back to normal gen if missing
            print(f"[gen] LayerDiffuse workflow unavailable ({exc}); using normal gen")
        else:
            os.makedirs(out_dir, exist_ok=True)
            results: List[str] = []
            for i in range(1, n + 1):
                last_err: Optional[Exception] = None
                for attempt in range(max_retries + 1):
                    try:
                        wf = _build_workflow_layerdiffuse(
                            brief, cfg, ld_template, _seed_for(i + attempt)
                        )
                        prompt_id = _comfyui.submit(url, wf, _comfyui.new_client_id())
                        images = _comfyui.wait(url, prompt_id)
                        if not images:
                            raise RuntimeError("ComfyUI returned no images")
                        dest = os.path.join(out_dir, f"cand_{i}.png")
                        shutil.copyfile(images[0], dest)
                        results.append(dest)
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc
                        print(f"[gen] transparent candidate {i} attempt {attempt + 1} failed: {exc}")
                        if not _comfyui.is_up(url):
                            _comfyui.ensure_up(cfg)
                else:
                    raise RuntimeError(
                        f"transparent candidate {i} failed after {max_retries + 1} attempts: {last_err}"
                    )
            return results

    # Decide single vs multi. Multi is used only when the brief carries a
    # non-empty, well-formed refs list (guarded so a malformed one degrades to
    # the single-ref path rather than crashing).
    parsed_refs: Optional[Dict[str, Any]] = None
    try:
        parsed_refs = _parse_refs(brief)
    except Exception as exc:  # noqa: BLE001
        print(f"[gen] refs parse failed ({exc}); falling back to single-ref path")
        parsed_refs = None

    use_multi = parsed_refs is not None
    if use_multi:
        try:
            template = _load_multi_workflow_template(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[gen] multi workflow load failed ({exc}); falling back to single-ref path")
            use_multi = False
            parsed_refs = None

    style_filename: Optional[str] = None
    subject_filename: Optional[str] = None
    ref_filename: Optional[str] = None

    if use_multi and parsed_refs is not None:
        # Upload the (first) style ref and the subject ref once, shared across
        # candidates. Multi-style averaging (>1 style) is a follow-up.
        styles = parsed_refs.get("styles") or []
        subject = parsed_refs.get("subject")
        if len(styles) > 1:
            print(f"[gen] {len(styles) - 1} extra style ref(s) noted; only the first is wired (multi-style averaging TODO)")
        if styles:
            try:
                style_filename = _comfyui.upload_image(url, styles[0])
            except Exception as exc:  # noqa: BLE001
                print(f"[gen] style reference upload failed ({exc}); continuing without style branch")
                style_filename = None
        if subject:
            try:
                subject_filename = _comfyui.upload_image(url, subject)
            except Exception as exc:  # noqa: BLE001
                print(f"[gen] subject reference upload failed ({exc}); continuing without subject branch")
                subject_filename = None
        # If both uploads ended up empty, drop back to the single-ref path.
        if not style_filename and not subject_filename:
            print("[gen] no multi refs usable after upload; falling back to single-ref path")
            use_multi = False

    if not use_multi:
        template = _load_workflow_template(cfg)
        # Resolve + upload the reference once (shared across candidates).
        weight = float(brief.get("ip_adapter_weight", 0.0) or 0.0)
        if weight > 0:
            refs = _find_references(brief.get("reference_set", "default"))
            if refs:
                try:
                    ref_filename = _comfyui.upload_image(url, refs[0])
                except Exception as exc:  # noqa: BLE001 - degrade to no-IP rather than fail
                    print(f"[gen] reference upload failed ({exc}); continuing without IP-Adapter")
                    ref_filename = None

    os.makedirs(out_dir, exist_ok=True)
    results: List[str] = []

    for i in range(1, n + 1):
        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                seed = _seed_for(i + attempt)
                if use_multi:
                    wf = _build_multi_workflow(
                        brief, cfg, template, seed, style_filename, subject_filename
                    )
                else:
                    wf = _build_workflow(brief, cfg, template, seed, ref_filename)
                client_id = _comfyui.new_client_id()
                prompt_id = _comfyui.submit(url, wf, client_id)
                images = _comfyui.wait(url, prompt_id)
                if not images:
                    raise RuntimeError("ComfyUI returned no images")
                dest = os.path.join(out_dir, f"cand_{i}.png")
                shutil.copyfile(images[0], dest)
                results.append(dest)
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                print(f"[gen] candidate {i} attempt {attempt + 1} failed: {exc}")
                # If ComfyUI fell over mid-run, try to bring it back before retry.
                if not _comfyui.is_up(url):
                    _comfyui.ensure_up(cfg)
        else:
            # Exhausted retries for this candidate.
            raise RuntimeError(
                f"candidate {i} failed after {max_retries + 1} attempts: {last_err}"
            )

    return results


# ---------------------------------------------------------------------------
# diffusers fallback (lazy, 4GB-friendly)
# ---------------------------------------------------------------------------
def _generate_diffusers(
    brief: Dict[str, Any], n: int, out_dir: str, cfg: Dict[str, Any]
) -> List[str]:
    """In-process Stable Diffusion via HuggingFace diffusers. Heavy imports lazy."""
    try:
        import torch  # noqa: F401
        from diffusers import StableDiffusionPipeline
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "The diffusers engine needs torch + diffusers. Install with: "
            "pip install torch diffusers transformers accelerate"
        ) from exc

    import torch  # re-import in local scope for clarity

    gen = cfg.get("gen", {}) or {}
    model_id = gen.get("base_model", "runwayml/stable-diffusion-v1-5")

    cuda = torch.cuda.is_available()
    dtype = torch.float16 if cuda else torch.float32

    pipe = StableDiffusionPipeline.from_pretrained(model_id, torch_dtype=dtype)
    if cuda:
        pipe = pipe.to("cuda")
        # 4GB-friendly: slice attention + VAE, and offload if very tight.
        try:
            pipe.enable_attention_slicing()
            pipe.enable_vae_slicing()
        except Exception:  # noqa: BLE001
            pass
        try:
            pipe.enable_model_cpu_offload()
        except Exception:  # noqa: BLE001
            pass

    prompt = (brief.get("prompt") or "").strip()
    positive = f"{prompt}, {_STYLE_SUFFIX}" if prompt else _STYLE_SUFFIX
    negative = (brief.get("negative") or "").strip() or None

    os.makedirs(out_dir, exist_ok=True)
    results: List[str] = []
    for i in range(1, n + 1):
        seed = _seed_for(i)
        generator = torch.Generator(device="cuda" if cuda else "cpu").manual_seed(seed)
        image = pipe(
            prompt=positive,
            negative_prompt=negative,
            num_inference_steps=int(gen.get("steps", 28)),
            guidance_scale=float(gen.get("cfg", 7.0)),
            width=int(gen.get("width", 512)),
            height=int(gen.get("height", 512)),
            generator=generator,
        ).images[0]
        dest = os.path.join(out_dir, f"cand_{i}.png")
        image.save(dest)
        results.append(dest)

    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def generate(brief: Dict[str, Any], n: int, out_dir: str, cfg: Dict[str, Any]) -> List[str]:
    """Generate `n` candidate images for `brief` into `out_dir`; return paths.

    Dispatches on cfg['engine'] ('comfyui' default, or 'diffusers' fallback).
    Returned files are named cand_1.png .. cand_n.png.
    """
    if cfg is None:
        cfg = _cfg.load_config()
    engine = (cfg.get("engine") or "comfyui").lower()
    n = max(1, int(n))

    if engine == "diffusers":
        results = _generate_diffusers(brief, n, out_dir, cfg)
    else:
        results = _generate_comfyui(brief, n, out_dir, cfg)

    # Transparent-background candidates. Two modes (see _transparent_mode):
    #   'native' -> LayerDiffuse already produced true RGBA in _generate_comfyui;
    #               nothing to do here.
    #   'cut'    -> SD has no alpha, so auto-cut the background off each candidate
    #               with rembg (the same bg_remove post step). Graceful: if rembg
    #               is missing or a cut fails, the original candidate stands.
    if _transparent_mode(brief, cfg) == "cut" and results:
        try:
            try:
                import postprocess as _pp
            except ImportError:  # package layout
                from conductor import postprocess as _pp  # type: ignore
            for p in results:
                try:
                    _pp.step_bg_remove(p, {}, p)  # cut in place -> RGBA PNG
                except Exception:
                    pass  # keep the opaque original on any single failure
        except Exception:
            pass  # postprocess unavailable -> leave candidates opaque
    return results
