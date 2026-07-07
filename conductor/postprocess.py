"""Composable post-processing pipeline for the local art pipeline.

Vectorizing is NOT a forced part of the core loop. Instead, this module offers an
ordered, composable array of post-processing STEPS the user can pick, configure,
and build into named chains, run after he picks a winner. `vectorize` is just one
optional step among many.

Design principle: GRACEFUL DEGRADATION. Every step lazily imports its optional
dependency. If a dependency is missing, the step logs a note and passes the image
through UNCHANGED so a chain never hard-fails. `run_chain` never raises; it collects
notes for skipped/failed steps and continues.
"""

from __future__ import annotations

import os
import shutil
import importlib

# ---------------------------------------------------------------------------
# Config access (lazy, with fallback)
# ---------------------------------------------------------------------------


def _load_cfg(cfg=None):
    """Return the config object/dict. Import the sibling `cfg` module lazily so
    this file imports fine even where config machinery is absent. Falls back to
    an empty dict on any failure."""
    if cfg is not None:
        return cfg
    for modname in ("conductor.cfg", "cfg"):
        try:
            mod = importlib.import_module(modname)
            # Common shapes: a module exposing `cfg`, `load()`, or `data` dict.
            for attr in ("cfg", "config", "data"):
                if hasattr(mod, attr):
                    return getattr(mod, attr)
            if hasattr(mod, "load"):
                return mod.load()
            return mod
        except Exception:
            continue
    return {}


def _cfg_get(cfg, dotted, default=None):
    """Fetch a dotted key like 'postprocess.default_chain' from a dict-like or
    attribute-like config, tolerating missing intermediate nodes."""
    node = cfg
    for part in dotted.split("."):
        if node is None:
            return default
        if isinstance(node, dict):
            if part not in node:
                return default
            node = node[part]
        else:
            node = getattr(node, part, None)
            if node is None:
                # Could be a legitimately-absent attr; give up to default.
                return default
    return node if node is not None else default


def _vector_colors(cfg, override=None, fallback=12):
    """Resolve the color count: explicit override > vector.colors > fallback."""
    if override:
        try:
            return int(override)
        except Exception:
            pass
    val = _cfg_get(cfg, "vector.colors", None)
    try:
        return int(val) if val is not None else fallback
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# Small PIL helpers (PIL is a hard-ish dep; if even PIL is missing, steps
# pass through). We import PIL inside each step so the module stays importable.
# ---------------------------------------------------------------------------


def _try_pil():
    try:
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


def _passthrough(in_path, out_path, note):
    """Copy the input to the output path unchanged and return (out_path, note)."""
    try:
        if os.path.abspath(in_path) != os.path.abspath(out_path):
            shutil.copyfile(in_path, out_path)
        return out_path, note
    except Exception as e:
        # As a last resort just return the input path so the chain continues.
        return in_path, "%s; passthrough copy failed: %s" % (note, e)


# ---------------------------------------------------------------------------
# Steps. Each: fn(in_path, params: dict, out_path) -> out_path
# (they also stash a note via the module-level _LAST_NOTE mechanism used by
#  run_chain). To keep the signature exactly fn(in,params,out)->out, notes are
#  returned by run_chain reading step._note isn't reliable across calls, so each
#  step writes its note into params under the reserved key '__note__'.)
# ---------------------------------------------------------------------------

_NOTE_KEY = "__note__"


def _note(params, msg):
    """Record a note for run_chain to pick up, without changing the return type."""
    if isinstance(params, dict):
        params[_NOTE_KEY] = msg
    return msg


def step_trim(in_path, params, out_path):
    """Autocrop transparent / near-white borders. params: {pad:int}."""
    params = params or {}
    if not _try_pil():
        _note(params, "trim skipped: PIL not available")
        return _passthrough(in_path, out_path, params[_NOTE_KEY])[0]
    try:
        from PIL import Image, ImageChops
        pad = int(params.get("pad", 0) or 0)
        img = Image.open(in_path)
        img = img.convert("RGBA")
        # Prefer the alpha channel; if fully opaque, fall back to a whitened diff.
        alpha = img.split()[-1]
        bbox = alpha.getbbox()
        if bbox is None or bbox == (0, 0, img.width, img.height):
            rgb = img.convert("RGB")
            bg = Image.new("RGB", rgb.size, (255, 255, 255))
            diff = ImageChops.difference(rgb, bg)
            bbox2 = diff.getbbox()
            if bbox2 is not None:
                bbox = bbox2
        if bbox is None:
            _note(params, "trim: nothing to crop")
            img.save(out_path)
            return out_path
        if pad:
            l, t, r, b = bbox
            bbox = (max(0, l - pad), max(0, t - pad),
                    min(img.width, r + pad), min(img.height, b + pad))
        img.crop(bbox).save(out_path)
        _note(params, "trim: cropped to %s (pad=%d)" % (bbox, pad))
        return out_path
    except Exception as e:
        _note(params, "trim failed: %s" % e)
        return _passthrough(in_path, out_path, params.get(_NOTE_KEY, ""))[0]


def step_bg_remove(in_path, params, out_path):
    """Remove background via rembg if installed; else pass through. RGBA PNG out."""
    params = params or {}
    try:
        from rembg import remove
    except Exception:
        _note(params, "bg_remove skipped: rembg not installed")
        return _passthrough(in_path, out_path, params[_NOTE_KEY])[0]
    if not _try_pil():
        _note(params, "bg_remove skipped: PIL not available")
        return _passthrough(in_path, out_path, params[_NOTE_KEY])[0]
    try:
        from PIL import Image
        import io
        with open(in_path, "rb") as f:
            data = f.read()
        result = remove(data)
        # rembg may return bytes or a PIL image depending on version.
        if isinstance(result, (bytes, bytearray)):
            img = Image.open(io.BytesIO(result))
        else:
            img = result
        img.convert("RGBA").save(out_path)
        _note(params, "bg_remove: rembg applied")
        return out_path
    except Exception as e:
        _note(params, "bg_remove failed: %s" % e)
        return _passthrough(in_path, out_path, params.get(_NOTE_KEY, ""))[0]


def step_palette_reduce(in_path, params, out_path):
    """Posterize/quantize to params.colors via PIL ADAPTIVE palette. Flattens for
    a cleaner look / smaller files."""
    params = params or {}
    if not _try_pil():
        _note(params, "palette_reduce skipped: PIL not available")
        return _passthrough(in_path, out_path, params[_NOTE_KEY])[0]
    try:
        from PIL import Image
        colors = params.get("colors")
        try:
            colors = int(colors) if colors else 12
        except Exception:
            colors = 12
        colors = max(2, min(256, colors))
        img = Image.open(in_path).convert("RGBA")
        # Preserve transparency: quantize RGB, re-apply the alpha channel.
        alpha = img.split()[-1]
        rgb = img.convert("RGB")
        q = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT
                         if hasattr(Image, "Quantize") else 0)
        out = q.convert("RGBA")
        out.putalpha(alpha)
        out.save(out_path)
        _note(params, "palette_reduce: %d colors" % colors)
        return out_path
    except Exception as e:
        _note(params, "palette_reduce failed: %s" % e)
        return _passthrough(in_path, out_path, params.get(_NOTE_KEY, ""))[0]


def step_upscale(in_path, params, out_path):
    """Upscale by params.scale. Try Real-ESRGAN; else PIL LANCZOS fallback."""
    params = params or {}
    try:
        scale = int(params.get("scale", 2) or 2)
    except Exception:
        scale = 2
    scale = max(1, scale)

    # --- Try Real-ESRGAN first --------------------------------------------
    if _realesrgan_available():
        try:
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet
            import numpy as np
            from PIL import Image
            img = Image.open(in_path).convert("RGB")
            arr = np.array(img)[:, :, ::-1]  # RGB->BGR for cv2-style pipeline
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                            num_block=23, num_grow_ch=32, scale=4)
            upsampler = RealESRGANer(scale=4, model_path=None, model=model)
            output, _ = upsampler.enhance(arr, outscale=scale)
            out_img = Image.fromarray(output[:, :, ::-1])
            out_img.save(out_path)
            _note(params, "upscale: Real-ESRGAN x%d" % scale)
            return out_path
        except Exception as e:
            _note(params, "upscale: Real-ESRGAN failed (%s), fell back to LANCZOS" % e)
            # fall through to LANCZOS

    # --- PIL LANCZOS fallback ---------------------------------------------
    if not _try_pil():
        _note(params, "upscale skipped: PIL not available")
        return _passthrough(in_path, out_path, params[_NOTE_KEY])[0]
    try:
        from PIL import Image
        img = Image.open(in_path)
        w, h = img.size
        resample = getattr(Image, "Resampling", Image).LANCZOS \
            if hasattr(Image, "Resampling") else Image.LANCZOS
        img.resize((w * scale, h * scale), resample).save(out_path)
        prev = params.get(_NOTE_KEY)
        _note(params, (prev + "; " if prev else "") + "upscale: PIL LANCZOS x%d" % scale)
        return out_path
    except Exception as e:
        _note(params, "upscale failed: %s" % e)
        return _passthrough(in_path, out_path, params.get(_NOTE_KEY, ""))[0]


def step_outline(in_path, params, out_path):
    """Add a clean sticker outline: dilate the alpha, fill dark, composite under
    the subject. params: {width:int, color:str}."""
    params = params or {}
    if not _try_pil():
        _note(params, "outline skipped: PIL not available")
        return _passthrough(in_path, out_path, params[_NOTE_KEY])[0]
    try:
        from PIL import Image, ImageFilter
        try:
            width = int(params.get("width", 6) or 6)
        except Exception:
            width = 6
        color = params.get("color", "#202020") or "#202020"
        img = Image.open(in_path).convert("RGBA")
        alpha = img.split()[-1]
        # Dilate alpha by MaxFilter (kernel must be odd).
        k = max(3, width * 2 + 1)
        dil = alpha.filter(ImageFilter.MaxFilter(min(k, 25)))
        # For widths beyond one MaxFilter pass, repeat to grow the stroke.
        passes = max(0, (k - 25) // 24)
        for _ in range(passes):
            dil = dil.filter(ImageFilter.MaxFilter(25))
        # Build the solid outline layer using the dilated alpha as its mask.
        outline_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        fill = Image.new("RGBA", img.size, _parse_color(color))
        outline_layer.paste(fill, (0, 0), dil)
        # Composite the original subject on top of the outline.
        composed = Image.alpha_composite(outline_layer, img)
        composed.save(out_path)
        _note(params, "outline: width=%d color=%s" % (width, color))
        return out_path
    except Exception as e:
        _note(params, "outline failed: %s" % e)
        return _passthrough(in_path, out_path, params.get(_NOTE_KEY, ""))[0]


def step_crop_square(in_path, params, out_path):
    """Center-crop or pad to a square canvas. params: {size:int}."""
    params = params or {}
    if not _try_pil():
        _note(params, "crop_square skipped: PIL not available")
        return _passthrough(in_path, out_path, params[_NOTE_KEY])[0]
    try:
        from PIL import Image
        img = Image.open(in_path).convert("RGBA")
        w, h = img.size
        side = max(w, h)
        # Pad to a centered square (transparent background).
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - w) // 2, (side - h) // 2))
        size = params.get("size")
        try:
            size = int(size) if size else None
        except Exception:
            size = None
        if size and size > 0:
            resample = getattr(Image, "Resampling", Image).LANCZOS \
                if hasattr(Image, "Resampling") else Image.LANCZOS
            canvas = canvas.resize((size, size), resample)
        canvas.save(out_path)
        _note(params, "crop_square: side=%s" % (size or side))
        return out_path
    except Exception as e:
        _note(params, "crop_square failed: %s" % e)
        return _passthrough(in_path, out_path, params.get(_NOTE_KEY, ""))[0]


def step_vectorize(in_path, params, out_path):
    """Call the existing sibling vectorize.vectorize(in_png, out_svg, colors).
    NOTE: output is an .svg, not a .png, so this returns the SVG path. The chain
    runner detects the extension change and threads the new path forward."""
    params = params or {}
    # Ensure the output path carries an .svg extension.
    root, _ext = os.path.splitext(out_path)
    svg_out = root + ".svg"
    vfn = _import_vectorize()
    if vfn is None:
        _note(params, "vectorize skipped: vectorize module / vtracer not available")
        # Pass the raster through unchanged (keep original extension).
        return _passthrough(in_path, out_path, params[_NOTE_KEY])[0]
    try:
        colors = _vector_colors(_load_cfg(None), params.get("colors"))
        vfn(in_path, svg_out, colors)
        _note(params, "vectorize: %d colors -> %s" % (colors, os.path.basename(svg_out)))
        return svg_out
    except Exception as e:
        _note(params, "vectorize failed: %s" % e)
        return _passthrough(in_path, out_path, params.get(_NOTE_KEY, ""))[0]


# ---------------------------------------------------------------------------
# Availability probes for optional deps
# ---------------------------------------------------------------------------


def _rembg_available():
    try:
        importlib.import_module("rembg")
        return True
    except Exception:
        return False


def _realesrgan_available():
    try:
        importlib.import_module("realesrgan")
        importlib.import_module("basicsr")
        return True
    except Exception:
        return False


def _import_vectorize():
    """Return the sibling vectorize.vectorize callable, or None. Also requires
    vtracer to be importable (the underlying engine) to count as 'available'."""
    for modname in ("conductor.vectorize", "vectorize"):
        try:
            mod = importlib.import_module(modname)
            fn = getattr(mod, "vectorize", None)
            if callable(fn):
                return fn
        except Exception:
            continue
    return None


def _vtracer_available():
    try:
        importlib.import_module("vtracer")
        return True
    except Exception:
        return False


def _parse_color(s):
    """Parse a '#rrggbb' / '#rgb' / named color into an RGBA tuple. Falls back to
    dark gray. Uses PIL's parser when available."""
    try:
        from PIL import ImageColor
        rgb = ImageColor.getrgb(s)
        if len(rgb) == 3:
            return (rgb[0], rgb[1], rgb[2], 255)
        return tuple(rgb)
    except Exception:
        return (32, 32, 32, 255)


# ---------------------------------------------------------------------------
# Step registry
# ---------------------------------------------------------------------------

def step_sharpen(in_path, params, out_path):
    """Sharpen via unsharp mask. params: amount (default 1.5)."""
    from PIL import Image, ImageFilter  # lazy
    amount = float((params or {}).get("amount", 1.5))
    im = Image.open(in_path)
    radius = 2
    im = im.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 100), threshold=2))
    im.save(out_path)
    return out_path


def step_adjust(in_path, params, out_path):
    """Brightness / contrast / saturation. params each default 1.0 (no change)."""
    from PIL import Image, ImageEnhance  # lazy
    p = params or {}
    im = Image.open(in_path).convert("RGBA")
    rgb = im.convert("RGB")
    rgb = ImageEnhance.Brightness(rgb).enhance(float(p.get("brightness", 1.0)))
    rgb = ImageEnhance.Contrast(rgb).enhance(float(p.get("contrast", 1.0)))
    rgb = ImageEnhance.Color(rgb).enhance(float(p.get("saturation", 1.0)))
    out = Image.merge("RGBA", (*rgb.split(), im.split()[3]))
    out.save(out_path)
    return out_path


def step_drop_shadow(in_path, params, out_path):
    """Soft drop shadow under the subject (needs alpha). params: offset, blur, opacity, color."""
    from PIL import Image, ImageFilter  # lazy
    p = params or {}
    offset = int(p.get("offset", 8)); blur = int(p.get("blur", 8))
    opacity = int(255 * float(p.get("opacity", 0.5)))
    im = Image.open(in_path).convert("RGBA")
    a = im.split()[3]
    pad = offset + blur * 2 + 4
    canvas = Image.new("RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.putalpha(a.point(lambda v: min(v, opacity)))
    shadow.paste(sh, (pad + offset, pad + offset), sh)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.paste(im, (pad, pad), im)
    canvas.save(out_path)
    return out_path


def step_resize(in_path, params, out_path):
    """Resize to exact width x height. params: width, height, keep_aspect (bool)."""
    from PIL import Image  # lazy
    p = params or {}
    w = int(p.get("width", 512)); h = int(p.get("height", 512))
    im = Image.open(in_path)
    if bool(p.get("keep_aspect", True)):
        im = im.copy(); im.thumbnail((w, h), Image.LANCZOS)
    else:
        im = im.resize((w, h), Image.LANCZOS)
    im.save(out_path)
    return out_path


STEPS = {
    "trim": step_trim,
    "bg_remove": step_bg_remove,
    "palette_reduce": step_palette_reduce,
    "upscale": step_upscale,
    "sharpen": step_sharpen,
    "adjust": step_adjust,
    "drop_shadow": step_drop_shadow,
    "resize": step_resize,
    "outline": step_outline,
    "crop_square": step_crop_square,
    "vectorize": step_vectorize,
}

# Metadata describing each step's params for the GUI. Description + param schema.
_STEP_META = {
    "trim": {
        "description": "Autocrop transparent / near-white borders.",
        "params": {"pad": {"type": "int", "default": 0}},
        "changes_ext": None,
    },
    "bg_remove": {
        "description": "Remove background (rembg). Output RGBA PNG.",
        "params": {},
        "changes_ext": None,
    },
    "palette_reduce": {
        "description": "Posterize/quantize to N colors (adaptive palette) for a "
                       "cleaner look and smaller files.",
        "params": {"colors": {"type": "int", "default": 12}},
        "changes_ext": None,
    },
    "upscale": {
        "description": "Upscale (Real-ESRGAN if available, else PIL LANCZOS).",
        "params": {"scale": {"type": "int", "default": 2}},
        "changes_ext": None,
    },
    "sharpen": {
        "description": "Sharpen the image (unsharp mask).",
        "params": {"amount": {"type": "float", "default": 1.5}},
        "changes_ext": None,
    },
    "adjust": {
        "description": "Tune brightness, contrast, and color saturation (1.0 = no change).",
        "params": {"brightness": {"type": "float", "default": 1.0},
                   "contrast": {"type": "float", "default": 1.0},
                   "saturation": {"type": "float", "default": 1.0}},
        "changes_ext": None,
    },
    "drop_shadow": {
        "description": "Add a soft drop shadow under the subject (needs a cut-out).",
        "params": {"offset": {"type": "int", "default": 8},
                   "blur": {"type": "int", "default": 8},
                   "opacity": {"type": "float", "default": 0.5}},
        "changes_ext": None,
    },
    "resize": {
        "description": "Resize to an exact width and height.",
        "params": {"width": {"type": "int", "default": 512},
                   "height": {"type": "int", "default": 512},
                   "keep_aspect": {"type": "bool", "default": True}},
        "changes_ext": None,
    },
    "outline": {
        "description": "Add a clean sticker outline/stroke around the subject.",
        "params": {"width": {"type": "int", "default": 6},
                   "color": {"type": "str", "default": "#202020"}},
        "changes_ext": None,
    },
    "crop_square": {
        "description": "Center-crop or pad to a square canvas.",
        "params": {"size": {"type": "int", "default": 0}},
        "changes_ext": None,
    },
    "vectorize": {
        "description": "Vectorize to SVG (vtracer). Optional final step.",
        "params": {"colors": {"type": "int", "default": 12}},
        "changes_ext": "svg",
    },
}


def available_steps():
    """Return a list of dicts describing every step so the GUI can present and
    build chains. `available` reflects whether optional deps are present."""
    has_pil = _try_pil()
    result = []
    for name, meta in _STEP_META.items():
        # Determine availability + a note about optional deps.
        note = ""
        if name in ("trim", "palette_reduce", "crop_square", "outline"):
            available = has_pil
            if not has_pil:
                note = "requires Pillow (PIL)"
        elif name == "bg_remove":
            available = has_pil and _rembg_available()
            if not _rembg_available():
                note = "requires rembg (else passes through unchanged)"
        elif name == "upscale":
            # Always runnable via PIL fallback; 'available' reflects the good path.
            available = _realesrgan_available()
            note = ("Real-ESRGAN available" if available
                    else "Real-ESRGAN missing; uses PIL LANCZOS fallback")
        elif name == "vectorize":
            available = (_import_vectorize() is not None) and _vtracer_available()
            if not available:
                note = "requires vtracer + sibling vectorize module (else passes through)"
        else:
            available = True
        result.append({
            "name": name,
            "description": meta["description"],
            "params": meta["params"],
            "available": available,
            "note": note,
        })
    return result


# ---------------------------------------------------------------------------
# Chain runners
# ---------------------------------------------------------------------------


def _ext_of(path):
    e = os.path.splitext(path)[1].lstrip(".").lower()
    return e or "png"


def run_chain(src_path, chain, out_dir, cfg=None):
    """Run an ordered chain of steps. Threads each step's output into the next;
    a step that changes extension (e.g. vectorize) updates the working path.
    Intermediates are written as `NN_<step>.<ext>`.

    Never raises. Returns:
        {
          "final": <path>,
          "steps": [{"step": name, "out": path, "note": str}, ...],
          "ok": bool,
        }
    """
    cfg = _load_cfg(cfg)
    steps_report = []
    ok = True

    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return {"final": src_path, "steps": [], "ok": False,
                "note": "could not create out_dir: %s" % e}

    if not src_path or not os.path.exists(src_path):
        return {"final": src_path, "steps": [], "ok": False,
                "note": "source path missing: %s" % src_path}

    current = src_path
    idx = 0
    for entry in (chain or []):
        # Normalize entry into (step_name, params).
        if isinstance(entry, dict):
            step_name = entry.get("step")
            params = dict(entry.get("params") or {})
        else:
            step_name = entry
            params = {}

        idx += 1
        prefix = "%02d_%s" % (idx, step_name or "unknown")

        fn = STEPS.get(step_name)
        if fn is None:
            note = "unknown step '%s' skipped" % step_name
            steps_report.append({"step": step_name, "out": current, "note": note})
            ok = False
            continue

        # Decide the intermediate output path (extension may be overridden by the
        # step itself, e.g. vectorize -> .svg).
        out_ext = _STEP_META.get(step_name, {}).get("changes_ext") or _ext_of(current)
        out_path = os.path.join(out_dir, "%s.%s" % (prefix, out_ext))

        # Clear any stale note key before invoking.
        params.pop(_NOTE_KEY, None)
        try:
            produced = fn(current, params, out_path)
            note = params.get(_NOTE_KEY, "")
            if not produced or not os.path.exists(produced):
                # Step returned nothing usable; keep the previous working path.
                note = (note + "; " if note else "") + "no output produced, kept previous"
                ok = False
                produced = current
            current = produced
            steps_report.append({"step": step_name, "out": current, "note": note})
        except Exception as e:
            # Steps are meant to self-handle, but belt-and-suspenders here.
            note = params.get(_NOTE_KEY, "")
            note = (note + "; " if note else "") + "step raised: %s" % e
            steps_report.append({"step": step_name, "out": current, "note": note})
            ok = False
            # Leave `current` unchanged and continue.

    return {"final": current, "steps": steps_report, "ok": ok}


def run_named(src_path, name, out_dir, cfg=None):
    """Look up a named chain in postprocess.chains and run it. If `name` is
    'default' or empty, use postprocess.default_chain."""
    cfg = _load_cfg(cfg)

    if not name or name == "default":
        chain = _cfg_get(cfg, "postprocess.default_chain", []) or []
    else:
        chains = _cfg_get(cfg, "postprocess.chains", {}) or {}
        if isinstance(chains, dict):
            chain = chains.get(name)
        else:
            chain = None
        if chain is None:
            return {"final": src_path, "steps": [], "ok": False,
                    "note": "named chain '%s' not found" % name}

    return run_chain(src_path, chain, out_dir, cfg=cfg)
