# Animation & character continuity

Plan + honest hardware budget for animating a picked hero on a low-VRAM laptop
(the target rig: RTX 3050 Ti, **4GB VRAM**, 16GB RAM).

## The core problem

Plain SD1.5 has **weak character continuity**: change the pose or prompt and the
character drifts (different face, armor, colors). So "same hero, new frame" is not
reliable from text alone. Everything below is about *pinning identity* while
*varying pose/motion*.

`nano banana` (Gemini 2.5 Flash Image) is genuinely excellent at this, but it is
**cloud + paid + account-gated**, which breaks the whole local/free reason this
tool exists. It is intentionally out of scope as a default. (A cloud path could be
added later as an opt-in for users who don't care about staying local.)

## What fits 4GB (measured / estimated)

All figures assume 512px, ComfyUI `--lowvram` (system RAM absorbs overflow), SD1.5.

| Backend | Extra VRAM | Fits 4GB? | Speed (4GB) | Continuity | Use it for |
|---|---|---|---|---|---|
| Base SD1.5 txt2img | baseline (~2-3GB active) | yes | ~10-20s / frame | poor | one-offs |
| + **IP-Adapter** (hero ref) | +0.5-1GB | yes | ~+3s | medium (identity) | keep the same character |
| + **ControlNet OpenPose** | +1-1.5GB / CN | yes (one CN, tight) | ~+5-10s | good (pose control) | **sprite frames / poses** |
| **LayerDiffuse** (transparent) | +~0.4GB, 2x compute | yes | ~15s warm / ~230s cold | n/a | transparent frames |
| **AnimateDiff** (motion module) | +1.7GB + N frames resident | **risky** | 8 frames ~1-3 min, 16 likely OOM | smooth within a clip | short idle loops |
| **Character LoRA** (train) | training needs ~6-8GB | **no on 4GB** | n/a locally | best | not practical locally |

**Verdict for the 4GB rig:**
- **Recommended core:** IP-Adapter (identity) + ControlNet OpenPose (pose) + fixed
  seed. This is the sweet spot: same hero, arbitrary poses, generates real sprite
  frames, comfortably within 4GB.
- **Optional / caveated:** AnimateDiff for short *ambient* loops (breathing, cape
  flutter, floating) at low frame counts (<=8). Warn the user it may OOM and fall
  back to fewer frames.
- **Not local on 4GB:** Character-LoRA training. Offer as a "train in the cloud
  (Colab), drop the .safetensors in models/loras" path instead, not an in-app step.

## The animation editor (planned architecture)

Goal: pick a hero, describe/pose keyframes, get a sprite sheet or GIF, entirely local.

```
hero ref image(s)
      |
   IP-Adapter  ---> identity lock
      |
  per keyframe:  text (action) + pose (OpenPose skeleton or a reference image)
      |
  ControlNet OpenPose + fixed seed  ---> keyframe N (consistent character, new pose)
      |
  [optional] frame interpolation (RIFE/FILM node)  ---> tween in-betweens
      |
  export: sprite sheet PNG  /  animated GIF  /  numbered frames  (+ transparent via LayerDiffuse)
```

**Inputs per keyframe:** an action prompt (text) and/or a pose (a stick-figure
skeleton the user drags, or an image the tool runs a pose-estimator on). Identity
comes from the hero ref, not re-described each frame.

**Tweens:** two options, both local:
1. *Generated* in-betweens: interpolate the pose skeletons, gen each (most control,
   slower).
2. *Interpolated* in-betweens: RIFE/FILM frame interpolation between two rendered
   keyframes (fast, smooth for small motion; can smear on big pose changes).

## Build phases

- **P1 (core):** install ControlNet + an SD1.5 OpenPose model; a keyframe workflow
  (IP-Adapter hero + OpenPose + seed lock); verify "same hero, two different poses."
- **P2 (tween):** RIFE/FILM interpolation node between keyframes; GIF/sheet export.
- **P3 (editor UI):** timeline of keyframes, per-keyframe prompt + pose, live
  preview, export controls; wire transparent (LayerDiffuse) as a per-export toggle.
- **P-opt:** AnimateDiff idle-loop mode (low frame count, OOM-guarded); cloud-LoRA
  import path.

Each phase is installed + verified live before the next, same as the LayerDiffuse
work (see `tools/patch_layerdiffuse.py` for the pattern of vendoring + patching a
custom node).
