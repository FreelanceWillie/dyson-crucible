"""
vectorize.py - turn a raster candidate (PNG) into a clean, art-looking SVG.

Once you've picked a winner, you often want it as vector art (scalable, crisp,
editable). Naively tracing a raw SD1.5 PNG is a disaster: the image is full of
subtle gradients and noise, so the tracer emits THOUSANDS of tiny junk paths and
the SVG looks muddy and is enormous.

The trick this module implements: FLATTEN FIRST, then trace.
  1. Composite any transparency onto white.
  2. Posterize/quantize down to a small fixed palette (default 12 colors).
     This collapses noisy gradients into flat, poster-like color regions.
  3. Trace THAT flattened image -> a handful of clean, art-looking color paths.
  4. Optionally run svgo to shrink the result.

Tracing prefers the in-process `vtracer` Python binding, falls back to the
`vtracer` CLI, and only then raises with an install hint. Everything heavy is
imported lazily so this file imports even on a bare machine.

Fully local, $0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional


def _flatten_and_posterize(png_path: str, colors: int) -> str:
    """Composite on white + quantize to `colors`, write a temp PNG, return its path.

    WHY this matters (the whole point of the module):
    A raw diffusion PNG has smooth gradients and per-pixel noise. A vector tracer
    turns every little color wobble into its own path, so tracing the raw image
    yields thousands of overlapping junk paths -> a bloated, muddy SVG.

    Quantizing to a small ADAPTIVE palette first snaps all that noise into a few
    flat color regions. Tracing those regions gives clean, poster-style shapes
    that actually look like intentional vector art.
    """
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "vectorize needs Pillow (PIL). Install it with: pip install pillow"
        ) from exc

    img = Image.open(png_path)

    # 1) Flatten transparency onto a white background so the tracer never has to
    #    reason about alpha (traced alpha edges are a common source of artifacts).
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(background, rgba).convert("RGB")
    else:
        img = img.convert("RGB")

    # 2) Posterize to a small adaptive palette, then back to RGB. ADAPTIVE picks
    #    the best `colors` colors for THIS image, so posterization is faithful.
    n = max(2, min(256, int(colors)))
    quantized = img.convert("P", palette=Image.ADAPTIVE, colors=n).convert("RGB")

    # Write the flattened image to a temp PNG next to a unique name.
    fd, tmp_path = tempfile.mkstemp(prefix="vectorize_flat_", suffix=".png")
    os.close(fd)  # we only wanted the unique path; PIL reopens it to write
    quantized.save(tmp_path, format="PNG")
    return tmp_path


def _trace_with_vtracer_py(tmp_png: str, out_svg: str) -> bool:
    """Try the in-process vtracer Python binding. Returns True on success."""
    try:
        import vtracer  # type: ignore
    except Exception:  # noqa: BLE001 - not installed; caller tries the CLI next
        return False

    # color mode with tuned params: fewer speckles, smooth-ish curves. These
    # values are a good general-purpose default for flat, posterized art.
    vtracer.convert_image_to_svg_py(
        tmp_png,
        out_svg,
        colormode="color",
        color_precision=6,
        filter_speckle=4,
        path_precision=8,
    )
    return os.path.isfile(out_svg)


def _trace_with_vtracer_cli(tmp_png: str, out_svg: str) -> bool:
    """Fall back to the `vtracer` command-line tool if it is on PATH."""
    exe = shutil.which("vtracer")
    if not exe:
        return False
    try:
        subprocess.run(
            [
                exe,
                "--input",
                tmp_png,
                "--output",
                out_svg,
                "--colormode",
                "color",
                "--color_precision",
                "6",
                "--filter_speckle",
                "4",
                "--path_precision",
                "8",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001 - treat any CLI failure as "not traced"
        return False
    return os.path.isfile(out_svg)


def _optimize_with_svgo(out_svg: str) -> None:
    """Best-effort in-place optimize with svgo if it is on PATH; skip if missing."""
    exe = shutil.which("svgo")
    if not exe:
        return  # svgo is optional; a missing tool is fine
    try:
        subprocess.run(
            [exe, "--input", out_svg, "--output", out_svg],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001 - optimization is a bonus, never fatal
        pass


def vectorize(png_path: str, out_svg: str, colors: int = 12) -> str:
    """Vectorize `png_path` into a clean SVG at `out_svg`. Returns `out_svg`.

    Pipeline: flatten+posterize -> trace (vtracer py, then CLI) -> optional svgo.
    Raises FileNotFoundError if the input is missing, or RuntimeError if no
    tracer backend is available (with an install hint).
    """
    if not png_path or not os.path.isfile(png_path):
        raise FileNotFoundError("vectorize: input image not found: {0!r}".format(png_path))

    # Make sure the output directory exists so the tracer can write there.
    out_dir = os.path.dirname(os.path.abspath(out_svg))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    tmp_png = _flatten_and_posterize(png_path, colors)
    try:
        traced = _trace_with_vtracer_py(tmp_png, out_svg)
        if not traced:
            traced = _trace_with_vtracer_cli(tmp_png, out_svg)
        if not traced:
            raise RuntimeError(
                "No vtracer backend available. Install the Python binding with "
                "`pip install vtracer`, or put the `vtracer` CLI on your PATH."
            )
        # Best-effort shrink; harmless no-op if svgo is not installed.
        _optimize_with_svgo(out_svg)
    finally:
        # Always clean up the temp flattened PNG, even on failure.
        try:
            if os.path.isfile(tmp_png):
                os.remove(tmp_png)
        except Exception:  # noqa: BLE001 - cleanup must never mask the real error
            pass

    return out_svg
