"""Find a Style — taste-discovery / steering engine.

Orchestrates a rate-and-steer loop: the user gens a wide spread of images,
rates each 1-5 stars, and the system biases the next round toward the
high-rated ones (reference conditioning + prompt synthesis), converging on
an emergent taste that can be saved as a category.

This module manages SESSION STATE and ROUND LOGIC only. It does NOT gen
images, embed, or save categories — the server/jobs do that. It produces a
PLAN (directions to gen + reference paths) and tracks ratings / loved images.

All state lives on disk as JSON under ``outputs/_taste/<session_id>.json``.
Every shared dependency (cfg, brain) is imported lazily and its absence is
tolerated. rate() and record_batch() never raise.
"""

import json
import os
import re

# ---------------------------------------------------------------------------
# Lazy / defensive shared-piece access
# ---------------------------------------------------------------------------

def _cfg(cfg=None):
    """Return a usable cfg-like object, or a tiny fallback shim.

    The fallback provides load_config/path/resolve so the module works even
    when the real cfg package is unavailable (tests, standalone use).
    """
    if cfg is not None:
        return cfg
    try:
        import cfg as _c  # type: ignore
        try:
            _c.load_config()
        except Exception:
            pass
        return _c
    except Exception:
        return _FallbackCfg()


class _FallbackCfg:
    """Minimal cfg shim: resolves paths relative to cwd."""

    def load_config(self):
        return {}

    def path(self, key):
        # Best-effort: outputs lives under cwd.
        if key in ("outputs", "output", "out"):
            return os.path.join(os.getcwd(), "outputs")
        return os.path.join(os.getcwd(), str(key))

    def resolve(self, *parts):
        return os.path.join(*[str(p) for p in parts if p is not None])


def _outputs_dir(cfg):
    """Absolute path to the outputs root, defensively."""
    c = _cfg(cfg)
    for getter in ("path",):
        fn = getattr(c, getter, None)
        if callable(fn):
            try:
                p = fn("outputs")
                if p:
                    return str(p)
            except Exception:
                pass
    return os.path.join(os.getcwd(), "outputs")


def _taste_dir(cfg):
    d = os.path.join(_outputs_dir(cfg), "_taste")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _session_file(session_id, cfg):
    return os.path.join(_taste_dir(cfg), "%s.json" % session_id)


def _explore(phrase, n, cfg):
    """Call brain.explore lazily; fall back to a trivial spread on absence."""
    try:
        import brain  # type: ignore
        fn = getattr(brain, "explore", None)
        if callable(fn):
            out = fn(phrase, n, _cfg(cfg))
            if out:
                return list(out)
    except Exception:
        pass
    # Fallback: a minimal non-empty spread so callers still get a plan.
    base = (phrase or "a game character").strip()
    dirs = []
    for i in range(max(1, int(n or 1))):
        dirs.append({
            "label": "%s (variant %d)" % (base, i + 1),
            "prompt": base,
            "negative": "",
        })
    return dirs


# ---------------------------------------------------------------------------
# Slug / id helpers (deterministic — no Date.now / random)
# ---------------------------------------------------------------------------

def _slug(text, maxlen=40):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not s:
        s = "untitled"
    return s[:maxlen].strip("-") or "untitled"


def _next_index(cfg):
    """Counter over existing session files (deterministic id source)."""
    try:
        files = [f for f in os.listdir(_taste_dir(cfg)) if f.endswith(".json")]
        return len(files) + 1
    except Exception:
        return 1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save(session, cfg=None):
    """Write the session to disk. Best-effort; never raises."""
    if not session or not session.get("id"):
        return session
    try:
        session["updated"] = _next_index(cfg)  # monotonic-ish stamp, no clock
    except Exception:
        pass
    try:
        path = _session_file(session["id"], cfg)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(session, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        # Fall back to a direct write if atomic replace fails.
        try:
            with open(_session_file(session["id"], cfg), "w", encoding="utf-8") as fh:
                json.dump(session, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return session


def load(session_id, cfg=None):
    """Load a session by id, or None if missing/corrupt."""
    try:
        with open(_session_file(session_id, cfg), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def list_sessions(cfg=None):
    """Return a list of {id, phrase, round, loved, created} summaries."""
    out = []
    try:
        for f in sorted(os.listdir(_taste_dir(cfg))):
            if not f.endswith(".json"):
                continue
            sid = f[:-5]
            s = load(sid, cfg)
            if not s:
                continue
            out.append({
                "id": s.get("id", sid),
                "phrase": s.get("phrase", ""),
                "round": s.get("round", 0),
                "loved": len(s.get("loved", []) or []),
                "created": s.get("created"),
            })
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def start(phrase="", n=8, cfg=None):
    """Create and persist a fresh session. Does NOT gen."""
    idx = _next_index(cfg)
    sid = "%04d-%s" % (idx, _slug(phrase or "a-game-character"))
    session = {
        "id": sid,
        "phrase": phrase or "",
        "created": idx,
        "updated": idx,
        "round": 0,
        "loved": [],          # [{path, stars, prompt}] — rated >= 4
        "rounds": [],         # [{directions, rated:{path:stars}, images}]
        "settings": {"n": int(n or 8)},
    }
    return save(session, cfg)


def _ensure_round(session):
    """Ensure a round dict exists at session['round']; return it."""
    rounds = session.setdefault("rounds", [])
    r = int(session.get("round", 0) or 0)
    while len(rounds) <= r:
        rounds.append({"directions": [], "rated": {}, "images": []})
    return rounds[r]


# ---------------------------------------------------------------------------
# Steering logic
# ---------------------------------------------------------------------------

def _loved_sorted(session):
    loved = [dict(x) for x in (session.get("loved") or []) if x]
    loved.sort(key=lambda x: x.get("stars", 0), reverse=True)
    return loved


def _synth_phrase(session, limit=3):
    """Join the highest-rated loved prompts into a seed phrase for explore."""
    loved = _loved_sorted(session)
    prompts = []
    for x in loved:
        p = (x.get("prompt") or "").strip()
        if p and p not in prompts:
            prompts.append(p)
        if len(prompts) >= limit:
            break
    if prompts:
        return ", ".join(prompts)
    return session.get("phrase", "") or "a game character"


def _vary(direction, i):
    """Produce a small re-roll variation of a loved direction."""
    d = dict(direction or {})
    base = (d.get("prompt") or "").strip()
    tweaks = [
        "alternate angle", "different lighting", "refined detail",
        "subtle palette shift", "closer framing", "cleaner composition",
    ]
    tweak = tweaks[i % len(tweaks)]
    d["prompt"] = ("%s, %s" % (base, tweak)).strip(", ")
    d["label"] = (d.get("label") or base or "loved") + " / " + tweak
    d.setdefault("negative", "")
    return d


def plan_round(session, cfg=None):
    """Return the plan for the CURRENT round.

    Round 0 (no loved pool): a WIDE, unanchored spread — brain.explore on the
    raw phrase for maximum divergence, no references.

    Later rounds: BIAS toward the loved pool by mixing
      (a) brain.explore seeded with a phrase synthesized from the top loved
          prompts (continued, taste-aligned variety), and
      (b) direct re-rolls of the loved directions with small variations,
    while returning the loved image paths as IP-Adapter references so the
    caller conditions on them.

    Returns: {directions, reference_paths, round}
    Each direction carries an ``intent`` telling the caller how to gen it.
    """
    n = int((session.get("settings") or {}).get("n", 8) or 8)
    rnd = int(session.get("round", 0) or 0)
    phrase = session.get("phrase", "") or ""
    loved = _loved_sorted(session)

    if rnd == 0 or not loved:
        directions = _explore(phrase or "a game character", n, cfg)
        for d in directions:
            d["intent"] = "explore"
        return {"directions": directions, "reference_paths": [], "round": rnd}

    # --- steered round -----------------------------------------------------
    reference_paths = [x.get("path") for x in loved if x.get("path")]

    # Split n between continued exploration and loved re-rolls.
    n_reroll = min(len(loved), max(1, n // 2))
    n_explore = max(0, n - n_reroll)

    directions = []

    # (b) direct re-rolls of the loved directions (drift toward taste).
    for i in range(n_reroll):
        src = loved[i % len(loved)]
        d = _vary({"prompt": src.get("prompt", ""),
                   "label": src.get("prompt", "loved"),
                   "negative": src.get("negative", "")}, i)
        d["intent"] = "reroll_loved"
        d["reference_paths"] = list(reference_paths)
        directions.append(d)

    # (a) continued variety seeded from the synthesized loved phrase.
    if n_explore > 0:
        seed = _synth_phrase(session)
        explored = _explore(seed, n_explore, cfg)
        for d in explored:
            d["intent"] = "explore_biased"
            d["reference_paths"] = list(reference_paths)
            directions.append(d)

    return {"directions": directions,
            "reference_paths": reference_paths,
            "round": rnd}


# ---------------------------------------------------------------------------
# Recording / rating
# ---------------------------------------------------------------------------

def record_batch(session, directions, image_paths, cfg=None):
    """Attach produced image paths to the current round (paired by index).

    Never raises. Stores directions + a directions-indexed image list so
    rate() can recover a path's originating prompt.
    """
    try:
        r = _ensure_round(session)
        dirs = list(directions or [])
        imgs = list(image_paths or [])
        r["directions"] = dirs
        images = []
        for i, p in enumerate(imgs):
            if not p:
                continue
            d = dirs[i] if i < len(dirs) else (dirs[-1] if dirs else {})
            images.append({
                "path": p,
                "prompt": (d or {}).get("prompt", ""),
                "label": (d or {}).get("label", ""),
            })
        r["images"] = images
        save(session, cfg)
    except Exception:
        pass
    return session


def _prompt_for_path(session, image_path):
    """Recover the originating prompt for an image path, if recorded."""
    try:
        for r in session.get("rounds", []) or []:
            for img in r.get("images", []) or []:
                if img.get("path") == image_path:
                    return img.get("prompt", "")
    except Exception:
        pass
    return ""


def rate(session, image_path, stars, cfg=None):
    """Set a star rating (1-5) for an image and update the loved pool.

    stars >= 4  -> add/update in loved (with originating prompt if known).
    stars <  4  -> remove from loved.
    Never raises.
    """
    try:
        try:
            stars = int(stars)
        except Exception:
            stars = 0
        stars = max(1, min(5, stars)) if stars else 0

        # Record the rating on the current round.
        r = _ensure_round(session)
        r.setdefault("rated", {})[image_path] = stars

        loved = session.setdefault("loved", [])
        loved = [x for x in loved if x and x.get("path") != image_path]

        if stars >= 4:
            prompt = _prompt_for_path(session, image_path)
            loved.append({"path": image_path, "stars": stars, "prompt": prompt})

        session["loved"] = loved
        save(session, cfg)
    except Exception:
        pass
    return session


def advance(session, cfg=None):
    """Increment the round counter and persist. Caller re-plans afterward."""
    try:
        session["round"] = int(session.get("round", 0) or 0) + 1
        _ensure_round(session)
        save(session, cfg)
    except Exception:
        pass
    return session


# ---------------------------------------------------------------------------
# Emergent-taste summary (feeds "Save as a style")
# ---------------------------------------------------------------------------

def _dedupe_descriptors(prompts):
    """Split loved prompts into comma-separated descriptors, dedupe, keep order."""
    seen = set()
    out = []
    for p in prompts:
        for part in re.split(r"[,\n]", p or ""):
            part = part.strip()
            key = part.lower()
            if part and key not in seen:
                seen.add(key)
                out.append(part)
    return out


def emergent_style(session, cfg=None):
    """Summarize the current taste for category creation.

    Returns {loved_paths, style_prompt, top} where style_prompt is a
    joined/deduped set of descriptors mined from the loved prompts and top is
    the best few loved entries.
    """
    loved = _loved_sorted(session)
    loved_paths = [x.get("path") for x in loved if x.get("path")]
    descriptors = _dedupe_descriptors([x.get("prompt", "") for x in loved])
    style_prompt = ", ".join(descriptors)
    top = loved[:5]
    return {
        "loved_paths": loved_paths,
        "style_prompt": style_prompt,
        "top": top,
    }
