#!/usr/bin/env python3
"""Patch ComfyUI-layerdiffuse for compatibility with current ComfyUI.

The upstream node (huchenlei/ComfyUI-layerdiffuse) is unmaintained and its SD1.5
"attn sharing" transparent path breaks on recent ComfyUI. This applies three
small, verified fixes so `gen.transparent: native` works:

  1. AttentionSharingUnit.forward: accept the new `transformer_options` kwarg
     ComfyUI now passes into attention forward (was a hard TypeError).
  2. AttentionSharingUnit.forward: lazily move the injected unit's submodules
     (temporal Linear/LayerNorm, LoRA ModuleLists) onto the activation's device,
     and cast the LoRA weights too -- otherwise they stay on CPU and mismatch cuda.
  3. LayeredDiffusionDecodeRGBA.decode: build the RGBA tensor directly. ComfyUI
     moved JoinImageWithAlpha to a new schema, so its old .join_image_with_alpha()
     method no longer exists.

Idempotent: running it again after it has patched is a no-op. Usage:

    python tools/patch_layerdiffuse.py <path-to-ComfyUI-layerdiffuse>
"""
import os
import sys

MARK = "# [dyson-crucible patch]"


def patch_file(path, replacements):
    if not os.path.isfile(path):
        print(f"  skip (missing): {path}")
        return False
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    if MARK in src:
        print(f"  already patched: {os.path.basename(path)}")
        return True
    changed = 0
    for old, new in replacements:
        if old in src:
            src = src.replace(old, new, 1)
            changed += 1
        else:
            print(f"  WARN: expected snippet not found in {os.path.basename(path)} "
                  f"(upstream may have changed); skipping one replacement")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print(f"  patched {os.path.basename(path)} ({changed} edit(s))")
    return changed > 0


def main(root):
    attn = os.path.join(root, "lib_layerdiffusion", "attention_sharing.py")
    ld = os.path.join(root, "layered_diffusion.py")

    patch_file(attn, [
        (
            "    def forward(self, h, context=None, value=None):\n"
            "        transformer_options = self.transformer_options\n",
            "    def forward(self, h, context=None, value=None, transformer_options=None):\n"
            "        " + MARK + " accept new ComfyUI kwarg; move unit onto activation device\n"
            "        transformer_options = self.transformer_options\n"
            "        if self.temporal_n.weight.device != h.device or self.temporal_n.weight.dtype != h.dtype:\n"
            "            self.to(device=h.device, dtype=h.dtype)\n",
        ),
        (
            "        down_weight = self.down.weight\n"
            "        up_weight = self.up.weight\n",
            "        down_weight = self.down.weight.to(h)  " + MARK + "\n"
            "        up_weight = self.up.weight.to(h)\n",
        ),
    ])

    patch_file(ld, [
        (
            "        alpha = 1.0 - mask\n"
            "        return JoinImageWithAlpha().join_image_with_alpha(image, alpha)\n",
            "        " + MARK + " JoinImageWithAlpha moved to new schema; build RGBA directly\n"
            "        rgba = torch.cat((image[..., :3], mask.unsqueeze(-1)), dim=-1)\n"
            "        return (rgba,)\n",
        ),
    ])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python tools/patch_layerdiffuse.py <path-to-ComfyUI-layerdiffuse>")
        sys.exit(2)
    target = sys.argv[1]
    if not os.path.isdir(target):
        print(f"error: not a directory: {target}")
        sys.exit(1)
    print(f"Patching ComfyUI-layerdiffuse at {target}")
    main(target)
    print("Done.")
