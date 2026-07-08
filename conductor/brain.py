"""
brain.py - the pluggable conversational "art director" for the conductor.

This is the piece that makes the tool feel like talking to a human art
director. You type plain-language feedback about the current candidates
("too wizard-y, more evil warlock") and the brain rewrites the asset's brief:
its positive prompt, negative prompt, how hard to lean on your reference images
(ip_adapter_weight), and which reference set to steer with.

CRITICAL DESIGN CHOICE - the brain is a SLOT-FILLER, not a chatbot.
Its ONE job is to emit a tiny JSON *patch*:

    {
      "prompt": "...",              # full replacement positive prompt
      "negative": "...",            # full replacement negative prompt
      "ip_adapter_weight": 0.0-1.0, # how tightly to hug the references
      "reference_set": "...",       # name of the reference folder to steer with
      "reasoning": "..."            # one short human sentence (logged to chat)
    }

Keeping the job that narrow is what keeps a small local model (Ollama) reliable:
it never has to write prose, only fill four fields. Any field it omits is left
untouched, so partial patches are fine and a totally failed parse is a no-op.

Three interchangeable backends, chosen by cfg['brain']:
    local      -> POST to a local Ollama server (fully offline, $0; default)
    gemini_api -> POST to Google's Generative Language REST API (needs a key)
    claude     -> shell out to the `claude` CLI on your PATH

Everything is defensive: `requests` is imported lazily, config access falls
back to sane defaults, and a backend that is down degrades to "keep the old
brief and log why" rather than raising.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Lazy / optional imports of our own sibling modules.
#
# We import brief.py so we can reuse append_chat() + snapshot() when present,
# but the module must still import (and refine_brief still work) if brief.py is
# missing or broken. Same story for cfg.py - we only ever read plain values.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - trivial import guard
    from . import brief as _brief_mod  # type: ignore
except Exception:  # noqa: BLE001 - also covers running as a loose script
    try:
        import brief as _brief_mod  # type: ignore
    except Exception:  # noqa: BLE001
        _brief_mod = None  # we fall back to manual chat/snapshot below


def _requests():
    """Import `requests` lazily so this module imports even if it is absent.

    Returns the module, or raises a friendly RuntimeError telling the user how
    to fix it. Callers that want to degrade gracefully should catch this.
    """
    try:
        import requests  # type: ignore

        return requests
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "The 'requests' package is required for the local/gemini brains. "
            "Install it with: pip install requests"
        ) from exc


# ---------------------------------------------------------------------------
# The persona. This single SYSTEM string is the whole personality of the brain.
# It is deliberately strict: output ONE JSON object, nothing else. The few-shot
# examples teach it the *shape* of a good edit (drop the tokens the user is
# reacting against, add the tokens they want, strengthen negatives) without
# ever letting it drift into free-form chat.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are the Art Director for a Stable Diffusion (SD1.5) art pipeline. You do NOT
chat. Your ONLY output is a single JSON object that patches an image "brief".

You receive the CURRENT brief (its prompt, negative prompt, ip_adapter_weight,
and reference_set) plus the user's plain-language FEEDBACK about the last
images. You translate that feedback into concrete prompt-engineering edits.

Rules:
- Reply with EXACTLY ONE JSON object and nothing else. No prose, no code fences.
- Keys (all optional; omit a key to leave it unchanged):
    "prompt"            : the FULL new positive prompt (comma-separated tokens).
    "negative"          : the FULL new negative prompt.
    "ip_adapter_weight" : a number 0.0-1.0. Higher = hug the reference images
                          harder (use when the user wants it to look MORE like
                          the references / more consistent). Lower = more
                          freedom (use when they want something different).
    "reference_set"     : name of a different reference folder, only if the user
                          explicitly asks to steer toward a different style set.
    "reasoning"         : ONE short sentence explaining the edit, for the log.
- When the user says something is "too X", REMOVE the tokens causing X from the
  prompt and ADD them (or their symptoms) to the negative prompt.
- When they want "more Y", ADD strong Y tokens to the prompt.
- Preserve tokens the user did not complain about. Do not rewrite from scratch.

Example 1
FEEDBACK: "too wizard-y, more evil warlock"
CURRENT prompt: "a wizard, long robe, magic staff, pointy hat, arcane, fantasy"
CURRENT negative: "blurry, low quality"
OUTPUT:
{"prompt":"an evil warlock, corrupted sorcerer, dark robe, menacing, sinister, glowing red eyes, fantasy","negative":"blurry, low quality, wizard, staff, pointy hat, friendly, whimsical","ip_adapter_weight":0.75,"reasoning":"Dropped wizard/staff/hat tokens and added corrupted, menacing warlock cues; pushed the old wizard tropes into negatives."}

Example 2
FEEDBACK: "colors are washed out, make it match my references more"
CURRENT prompt: "a rusty combat robot, top-down, game asset"
CURRENT negative: "text, watermark"
OUTPUT:
{"prompt":"a rusty combat robot, top-down, game asset, rich saturated colors, high contrast, vibrant","negative":"text, watermark, washed out, desaturated, pale, flat lighting","ip_adapter_weight":0.9,"reasoning":"Added saturation/contrast cues, pushed 'washed out' into negatives, and raised ip_adapter_weight to hug the references harder."}
"""


# ---------------------------------------------------------------------------
# Config access helpers - all defensive. We prefer cfg.load_config() but fall
# back to reading config.yaml ourselves, and finally to hard defaults, so the
# brain never breaks just because cfg.py is missing.
# ---------------------------------------------------------------------------
_FALLBACK_CFG: Dict[str, Any] = {
    "brain": "local",
    "ollama_model": "qwen2.5:3b-instruct",
    "ollama_url": "http://localhost:11434",
    "gemini_api_key_env": "GEMINI_API_KEY",
    "gemini_model": "gemini-2.0-flash",
    "claude_cmd": "claude",
}


def _load_cfg_fallback() -> Dict[str, Any]:
    """Best-effort config load used only when the caller passes no cfg.

    Order: cfg.load_config() -> read config.yaml at the repo root -> defaults.
    """
    # 1) Preferred: the shared cfg module.
    try:  # pragma: no cover - depends on sibling module presence
        try:
            from . import cfg as _cfg_mod  # type: ignore
        except Exception:  # noqa: BLE001
            import cfg as _cfg_mod  # type: ignore
        loaded = _cfg_mod.load_config()
        if isinstance(loaded, dict):
            return loaded
    except Exception:  # noqa: BLE001
        pass

    # 2) Fall back to reading config.yaml directly (repo root = parent dir).
    try:
        import yaml  # type: ignore

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg_path = os.path.join(repo_root, "config.yaml")
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, dict):
                merged = dict(_FALLBACK_CFG)
                merged.update(data)
                return merged
    except Exception:  # noqa: BLE001
        pass

    # 3) Last resort: hard defaults.
    return dict(_FALLBACK_CFG)


def _cfg_get(cfg: Optional[Dict[str, Any]], key: str, default: Any) -> Any:
    """Read a top-level config value with a default, tolerating a None cfg."""
    if not isinstance(cfg, dict):
        cfg = _load_cfg_fallback()
    val = cfg.get(key, default)
    return default if val is None else val


# ---------------------------------------------------------------------------
# Prompt assembly + patch parsing. These two helpers are backend-agnostic:
# every backend builds the same messages and parses the same way.
# ---------------------------------------------------------------------------
def _build_messages(brief: Dict[str, Any], feedback: str) -> List[Dict[str, str]]:
    """Build a chat-style [{'role','content'}, ...] message list for the brain.

    We render the CURRENT brief fields the model is allowed to edit, then the
    user's feedback, then a final instruction to emit only the JSON patch. This
    same list is flattened for backends (claude/gemini) that want one string.
    """
    current = (
        "CURRENT brief\n"
        'prompt: "{prompt}"\n'
        'negative: "{negative}"\n'
        "ip_adapter_weight: {weight}\n"
        'reference_set: "{refset}"'
    ).format(
        prompt=brief.get("prompt", ""),
        negative=brief.get("negative", ""),
        weight=brief.get("ip_adapter_weight", 0.8),
        refset=brief.get("reference_set", "default"),
    )
    user_block = (
        current
        + "\n\nFEEDBACK: \""
        + (feedback or "").strip()
        + "\"\n\nOUTPUT the JSON patch now (one object, no other text):"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_block},
    ]


def _flatten_messages(messages: List[Dict[str, str]]) -> str:
    """Flatten a message list into a single prompt string for CLI/REST backends."""
    parts = []
    for m in messages:
        role = m.get("role", "user").upper()
        parts.append("{0}:\n{1}".format(role, m.get("content", "")))
    return "\n\n".join(parts)


def _parse_patch(text: str) -> Dict[str, Any]:
    """Robustly extract the FIRST JSON object from a model reply.

    Tolerates ```json code fences and surrounding prose. On any failure returns
    an empty dict so the caller keeps the old brief (a no-op edit) and can log
    the raw reply. We deliberately do NOT trust the model to be clean.
    """
    if not text:
        return {}

    # Strip common code-fence wrappers first.
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    # Fast path: the whole thing is already valid JSON.
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass

    # Fallback: scan for the first balanced {...} block and try to parse it.
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except Exception:  # noqa: BLE001
                        break  # malformed; advance to the next '{'
        start = cleaned.find("{", start + 1)

    return {}


# ---------------------------------------------------------------------------
# Backend callers. Each returns the RAW model text (or "" on failure). Parsing
# is the caller's job so all three share one code path afterwards.
# ---------------------------------------------------------------------------
def _call_local(messages: List[Dict[str, str]], cfg: Dict[str, Any]) -> str:
    """Call a local Ollama server's /api/chat endpoint (stream off)."""
    requests = _requests()
    url = str(_cfg_get(cfg, "ollama_url", "http://localhost:11434")).rstrip("/")
    model = str(_cfg_get(cfg, "ollama_model", "qwen2.5:3b-instruct"))
    # keep_alive=0 unloads the model from VRAM right after the reply, so the brain
    # does not hold GPU memory that the image generator (ComfyUI) needs. On a 4GB
    # card the brain and the gen would otherwise fight over VRAM. They run
    # sequentially (chat, then gen), so a brief reload per reply is a fine trade.
    payload = {"model": model, "messages": messages, "stream": False, "keep_alive": 0}
    resp = requests.post(url + "/api/chat", json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    # /api/chat returns {"message": {"content": "..."}}; be defensive anyway.
    msg = data.get("message") or {}
    return msg.get("content", "") or data.get("response", "") or ""


def _call_gemini(messages: List[Dict[str, str]], cfg: Dict[str, Any]) -> str:
    """Call Google's Generative Language generateContent REST endpoint."""
    requests = _requests()
    key_env = str(_cfg_get(cfg, "gemini_api_key_env", "GEMINI_API_KEY"))
    api_key = os.environ.get(key_env, "")
    if not api_key:
        raise RuntimeError(
            "Gemini brain selected but env var '{0}' is not set.".format(key_env)
        )
    model = str(_cfg_get(cfg, "gemini_model", "gemini-2.0-flash"))
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model
        + ":generateContent?key="
        + api_key
    )
    # Gemini has no dedicated system role in v1beta generateContent, so we fold
    # the system persona into the single user turn.
    prompt = _flatten_messages(messages)
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"] or ""
    except Exception:  # noqa: BLE001
        return ""


def _call_claude(messages: List[Dict[str, str]], cfg: Dict[str, Any]) -> str:
    """Shell out to the `claude` CLI: claude -p "<full prompt>"."""
    cmd = str(_cfg_get(cfg, "claude_cmd", "claude"))
    prompt = _flatten_messages(messages)
    try:
        completed = subprocess.run(
            [cmd, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "claude brain selected but '{0}' was not found on PATH.".format(cmd)
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError(
            "claude CLI exited {0}: {1}".format(
                completed.returncode, (completed.stderr or "").strip()
            )
        )
    return completed.stdout or ""


_BACKENDS = {
    "local": _call_local,
    "gemini_api": _call_gemini,
    "claude": _call_claude,
}


# ---------------------------------------------------------------------------
# Chat / snapshot helpers that use brief.py when available, else do it manually.
# ---------------------------------------------------------------------------
def _append_chat(brief: Dict[str, Any], role: str, text: str) -> None:
    """Append a chat turn, preferring brief.append_chat when importable."""
    if _brief_mod is not None and hasattr(_brief_mod, "append_chat"):
        try:
            _brief_mod.append_chat(brief, role, text)
            return
        except Exception:  # noqa: BLE001 - fall through to manual append
            pass
    brief.setdefault("chat", []).append({"role": role, "text": text})


def _snapshot(brief: Dict[str, Any]) -> None:
    """Snapshot history before mutation, preferring brief.snapshot if present."""
    if _brief_mod is not None and hasattr(_brief_mod, "snapshot"):
        try:
            _brief_mod.snapshot(brief)
            return
        except Exception:  # noqa: BLE001 - snapshotting is best-effort
            pass
    # Minimal manual fallback so history is never silently lost.
    versions = brief.setdefault("versions", [])
    versions.append(
        {
            "index": len(versions),
            "prompt": brief.get("prompt", ""),
            "negative": brief.get("negative", ""),
            "ip_adapter_weight": brief.get("ip_adapter_weight", 0.8),
            "reference_set": brief.get("reference_set", "default"),
        }
    )


def _apply_patch(brief: Dict[str, Any], patch: Dict[str, Any]) -> List[str]:
    """Apply only known, validated keys from `patch` to `brief` in place.

    Returns the list of field names actually changed (for the chat summary).
    Unknown keys are ignored; bad types are skipped rather than raising.
    """
    changed: List[str] = []

    if isinstance(patch.get("prompt"), str) and patch["prompt"].strip():
        brief["prompt"] = patch["prompt"].strip()
        changed.append("prompt")

    if isinstance(patch.get("negative"), str):
        brief["negative"] = patch["negative"].strip()
        changed.append("negative")

    if "ip_adapter_weight" in patch:
        try:
            w = float(patch["ip_adapter_weight"])
            # Clamp into the valid IP-Adapter range.
            brief["ip_adapter_weight"] = max(0.0, min(1.0, w))
            changed.append("ip_adapter_weight")
        except (TypeError, ValueError):
            pass  # ignore a non-numeric weight

    ref = patch.get("reference_set")
    if isinstance(ref, str) and ref.strip():
        brief["reference_set"] = ref.strip()
        changed.append("reference_set")

    return changed


# ---------------------------------------------------------------------------
# THE public entry point.
# ---------------------------------------------------------------------------
def refine_brief(brief: Dict[str, Any], feedback: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Rewrite `brief` from plain-language `feedback` using the configured brain.

    Flow:
      1. Snapshot the brief so the edit is undoable.
      2. Ask the configured backend for a JSON patch.
      3. Apply only the known/validated keys, clamping ip_adapter_weight.
      4. Log the exchange to the brief's chat (user feedback + assistant note).

    Never raises for an unreachable/failed backend: it logs the problem to chat
    and returns the brief unchanged, so the caller's loop keeps going.
    """
    if not isinstance(brief, dict):
        raise TypeError("refine_brief expected a brief dict, got %r" % type(brief))

    # Always record what the user asked for, even if the brain then fails.
    _append_chat(brief, "user", feedback or "")

    # Snapshot BEFORE mutating so we can compare/undo later.
    _snapshot(brief)

    which = str(_cfg_get(cfg, "brain", "local")).strip().lower()
    backend = _BACKENDS.get(which)
    if backend is None:
        _append_chat(
            brief,
            "assistant",
            "[brain error] unknown brain '{0}' (expected local|gemini_api|claude); "
            "brief left unchanged.".format(which),
        )
        return brief

    # Call the backend, tolerating any failure.
    try:
        messages = _build_messages(brief, feedback)
        raw = backend(messages, cfg if isinstance(cfg, dict) else _load_cfg_fallback())
    except Exception as exc:  # noqa: BLE001 - degrade, do not crash the loop
        _append_chat(
            brief,
            "assistant",
            "[brain error] {0} backend failed: {1}; brief left unchanged.".format(
                which, exc
            ),
        )
        return brief

    patch = _parse_patch(raw)
    if not patch:
        # Keep the old brief but preserve the raw reply for debugging.
        preview = (raw or "").strip().replace("\n", " ")
        if len(preview) > 400:
            preview = preview[:400] + "..."
        _append_chat(
            brief,
            "assistant",
            "[brain warning] could not parse a JSON patch; brief unchanged. "
            "Raw reply: " + (preview or "<empty>"),
        )
        return brief

    changed = _apply_patch(brief, patch)
    reasoning = patch.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        reasoning = "Updated: " + (", ".join(changed) if changed else "nothing")
    summary = reasoning.strip()
    if changed:
        summary += "  (fields changed: {0})".format(", ".join(changed))
    else:
        summary += "  (no fields changed)"
    _append_chat(brief, "assistant", summary)
    return brief


# ---------------------------------------------------------------------------
# Reachability probe so the UI can warn before the user wastes a turn.
# ---------------------------------------------------------------------------
def available(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """Quickly check whether the configured brain is reachable.

    Returns (ok, human_message). Cheap and non-fatal: any exception maps to
    (False, reason). This is a courtesy check for the UI, not a guarantee.
    """
    which = str(_cfg_get(cfg, "brain", "local")).strip().lower()

    if which == "local":
        url = str(_cfg_get(cfg, "ollama_url", "http://localhost:11434")).rstrip("/")
        model = str(_cfg_get(cfg, "ollama_model", "qwen2.5:3b-instruct"))
        try:
            requests = _requests()
            resp = requests.get(url + "/api/tags", timeout=4)
            if resp.status_code != 200:
                return False, "Ollama at {0} returned HTTP {1}.".format(url, resp.status_code)
            # If we can list tags, also note whether the model is pulled.
            try:
                names = [m.get("name", "") for m in (resp.json().get("models") or [])]
                # Match on the bare model name or the family prefix.
                short = model.split(":")[0]
                if names and not any(model == n or n.startswith(short) for n in names):
                    return True, (
                        "Ollama is up at {0}, but model '{1}' is not pulled "
                        "(have: {2}). Run: ollama pull {1}".format(
                            url, model, ", ".join(names) or "none"
                        )
                    )
            except Exception:  # noqa: BLE001 - listing is best-effort
                pass
            return True, "Ollama reachable at {0} (model {1}).".format(url, model)
        except Exception as exc:  # noqa: BLE001
            return False, "Ollama not reachable at {0}: {1}".format(url, exc)

    if which == "gemini_api":
        key_env = str(_cfg_get(cfg, "gemini_api_key_env", "GEMINI_API_KEY"))
        if os.environ.get(key_env):
            return True, "Gemini API key present in ${0}.".format(key_env)
        return False, "Gemini brain selected but ${0} is not set.".format(key_env)

    if which == "claude":
        cmd = str(_cfg_get(cfg, "claude_cmd", "claude"))
        if shutil.which(cmd):
            return True, "claude CLI found on PATH ({0}).".format(cmd)
        return False, "claude brain selected but '{0}' is not on PATH.".format(cmd)

    return False, "Unknown brain '{0}' (expected local|gemini_api|claude).".format(which)


# ===========================================================================
# Divergent exploration + chat command routing.
#
# These power the "surprise me" workflow: type one evocative phrase, get many
# WILDLY DIFFERENT takes, cherry-pick the good parts, synthesize a direction.
# And the chat-as-command router so plain language ("make a category X that
# looks like Y, gen 5 styles") drives the app.
#
# Every function degrades gracefully: if the brain is missing/offline, a
# deterministic template fallback still produces divergent prompts, so the
# feature never hard-fails on a weak local model.
# ===========================================================================

def _dispatch_raw(system, user, cfg):
    """Call the configured backend with a one-off system+user message pair."""
    which = str(_cfg_get(cfg, "brain", "local")).strip().lower()
    backend = _BACKENDS.get(which)
    if backend is None:
        raise RuntimeError("unknown brain '{0}'".format(which))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    return backend(messages, cfg if isinstance(cfg, dict) else _load_cfg_fallback())


def _extract_json(text):
    """Find and parse the first JSON value (object OR array) in a reply.

    Tolerates code fences and surrounding prose. Bracket-balance scan so nested
    structures survive. Returns the parsed value or None.
    """
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if "```" in s:
            s = s[: s.rfind("```")]
    start = None
    opener = None
    for i, ch in enumerate(s):
        if ch in "[{":
            start = i
            opener = ch
            break
    if start is None:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:j + 1])
                except Exception:
                    return None
    return None


# Style axes for the deterministic fallback: cross the phrase with these so no
# two takes look alike even when there is no LLM available.
_ERA = ["baroque", "art nouveau", "brutalist", "retro-futurist", "medieval woodcut",
        "1980s airbrush", "ukiyo-e", "cyberpunk", "dieselpunk", "gothic revival",
        "bauhaus", "vaporwave", "renaissance oil", "soviet constructivist"]
_MEDIUM = ["oil painting", "ink and wash", "3d render", "papercut collage",
           "charcoal sketch", "stained glass", "pixel art", "gouache", "etching",
           "airbrush", "marble sculpture", "watercolor", "vector flat", "risograph"]
_MOOD = ["ominous", "regal", "playful", "melancholic", "ferocious", "serene",
         "decadent", "austere", "feverish", "triumphant"]
_PALETTE = ["monochrome", "jewel tones", "muted earth", "neon", "gold and black",
            "pastel", "blood red accents", "icy blues", "sepia", "high-contrast"]


def _fallback_directions(phrase, n):
    """No-LLM divergence: cross the phrase with rotating style axes."""
    out = []
    for i in range(max(1, n)):
        era = _ERA[i % len(_ERA)]
        med = _MEDIUM[(i * 3 + 1) % len(_MEDIUM)]
        mood = _MOOD[(i * 5 + 2) % len(_MOOD)]
        pal = _PALETTE[(i * 7 + 3) % len(_PALETTE)]
        out.append({
            "label": "{0} / {1}".format(era, med).title(),
            "prompt": "{0}, {1} style, {2} rendering, {3} mood, {4} palette, "
                      "highly detailed, single subject, centered".format(phrase, era, med, mood, pal),
            "negative": "text, watermark, blurry, extra limbs, low quality",
        })
    return out


EXPLORE_SYSTEM = (
    "You are a DIVERGENT art director. Given one short, evocative style phrase, invent\n"
    "N maximally-DIFFERENT visual interpretations of it. The goal is spread, not polish:\n"
    "no two takes should look alike. Vary the ERA, MEDIUM, PALETTE, MOOD, MATERIAL, and\n"
    "SILHOUETTE across the set so the user can cherry-pick parts he likes from very\n"
    "different directions. Be bold and specific; some takes should be unexpected.\n\n"
    "Output ONLY a JSON array of exactly N objects, each:\n"
    '  {"label":"3 to 5 word name","prompt":"a full image-generation prompt","negative":"things to avoid"}\n'
    "No prose, no markdown, just the array."
)


def explore(phrase, n, cfg):
    """Expand ONE phrase into n distinct interpretations (label+prompt+negative).

    Tries the brain; on any failure or short reply, falls back to the
    deterministic axis-cross so the mood board always fills.
    """
    n = max(1, min(int(n or 10), 30))
    try:
        raw = _dispatch_raw(
            EXPLORE_SYSTEM.replace("N", str(n)),
            "Phrase: {0}\nProduce exactly {1} maximally-different interpretations.".format(phrase, n),
            cfg)
        data = _extract_json(raw)
        if isinstance(data, list) and data:
            out = []
            for d in data:
                if not isinstance(d, dict):
                    continue
                prompt = str(d.get("prompt") or "").strip()
                if not prompt:
                    continue
                out.append({
                    "label": str(d.get("label") or phrase)[:60],
                    "prompt": prompt,
                    "negative": str(d.get("negative") or ""),
                })
            if out:
                if len(out) < n:
                    out.extend(_fallback_directions(phrase, n - len(out)))
                return out[:n]
    except Exception:
        pass
    return _fallback_directions(phrase, n)


INTERPRET_SYSTEM = (
    "You route a user's plain-language message (about generating game art) into ONE\n"
    "action. Choose the single best action and extract its parameters.\n\n"
    "Actions and params:\n"
    "  new_category  {name, look, explore_n}   e.g. 'make a category X that looks like Y, gen 5 styles'\n"
    "  explore       {phrase, n, category}     e.g. 'surprise me with a dark vampire steampunk, 10 takes'\n"
    "  refine        {feedback}                e.g. 'more armor, less blue'\n"
    "  generate      {}                        e.g. 'make some', 'gen it'\n"
    "  assign        {category}                e.g. 'put this in the Frost category'\n"
    "  help          {}                        e.g. 'how do I ...'\n"
    "  chat          {text}                    anything else\n\n"
    'Output ONLY a JSON object: {"action":"...","params":{...}}. No prose.'
)


def interpret(message, context, cfg):
    """Classify a chat message into {action, params}. Falls back to 'refine' if
    an asset is selected (so plain feedback still works) else 'chat'."""
    ctx = "selected asset: {0}; selected category: {1}".format(
        (context or {}).get("asset"), (context or {}).get("category"))
    try:
        raw = _dispatch_raw(INTERPRET_SYSTEM, "Context: {0}\nMessage: {1}".format(ctx, message), cfg)
        obj = _extract_json(raw)
        if isinstance(obj, dict) and obj.get("action"):
            action = str(obj["action"]).strip()
            params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
            return {"action": action, "params": params}
    except Exception:
        pass
    low = (message or "").lower()
    if any(w in low for w in ("surprise", "explore", "styles", "impressions", "takes", "variations")):
        return {"action": "explore", "params": {"phrase": message, "n": 10}}
    if (context or {}).get("asset"):
        return {"action": "refine", "params": {"feedback": message}}
    return {"action": "chat", "params": {"text": message}}


SYNTH_SYSTEM = (
    "You are an art director. The user liked parts of several different style takes.\n"
    "Merge the aspects he named into ONE coherent direction. Keep what he praised,\n"
    "drop the rest. Output ONLY a JSON object: "
    '{"prompt":"...","negative":"...","label":"short name"}.'
)


def synthesize(phrase, picks, cfg):
    """Merge cherry-picked takes into one direction. Falls back to concatenating
    the picked prompts if the brain is unavailable."""
    picked_text = "\n".join(
        "- {0}: {1} {2}".format(
            p.get("label", ""), p.get("prompt", ""),
            "(likes: " + p["note"] + ")" if p.get("note") else "")
        for p in (picks or []))
    try:
        raw = _dispatch_raw(
            SYNTH_SYSTEM,
            "Original phrase: {0}\nLiked takes:\n{1}".format(phrase, picked_text), cfg)
        obj = _extract_json(raw)
        if isinstance(obj, dict) and obj.get("prompt"):
            return {"label": str(obj.get("label") or phrase)[:60],
                    "prompt": str(obj["prompt"]).strip(),
                    "negative": str(obj.get("negative") or "")}
    except Exception:
        pass
    merged = ", ".join(p.get("prompt", "") for p in (picks or []) if p.get("prompt"))
    return {"label": phrase[:60], "prompt": (phrase + ", " + merged).strip(", "),
            "negative": "text, watermark, blurry, low quality"}


# ---------------------------------------------------------------------------
# Real two-way conversation. Unlike refine/explore/interpret (which emit
# structured JSON), this just TALKS: brainstorming styles, answering questions,
# thinking out loud with him. Falls back to a helpful canned line if offline.
# ---------------------------------------------------------------------------
CHAT_SYSTEM = (
    "You are a friendly, concise art-direction assistant for a solo game developer "
    "using a local image generator. Help him brainstorm styles, characters, and ideas, "
    "and answer questions about using the tool. Keep replies short and practical. "
    "You cannot see the generated images; the human and the ranking step judge visuals, "
    "so never claim you saw an image. No em-dash in your replies."
)

# A short built-in FAQ the brain can answer from (used by the global control chat).
# Keep it plain and layman-friendly.
FAQ = (
    "FAQ about this tool:\n"
    "- Find a Style: start here when you do not know what you want. It shows a wide "
    "spread, you rate each 1 to 5 stars, and it steers toward what you star. Save the "
    "result as a Style (a category).\n"
    "- Surprise Me: type a vague phrase and get many different takes to cherry-pick from.\n"
    "- New Hero: use once you have a Style, to make a specific character.\n"
    "- Categories hold a shared look; heroes inside them stay consistent. Drop that "
    "category's reference images in its references folder.\n"
    "- References teach the generator your style (via IP-Adapter) and are what candidates "
    "are ranked against.\n"
    "- Pick a winner, then Post-process (transparent PNG, upscale, vectorize, and more) to "
    "get a game-ready file. Vectorize is optional, not automatic.\n"
    "- More like this: reseed a fresh batch from a winner. Poses: make the same character "
    "in new poses.\n"
    "- If gens are slow or fail: check the health panel (Doctor). On a 4GB GPU, launch "
    "ComfyUI with --lowvram and keep size at 512."
)


def chat(message, history, cfg, with_faq=False):
    """A plain conversational reply from the configured brain. `history` is a
    list of {role, text} turns (most recent last). Set with_faq for the global
    control chat so it can answer how-to questions. Never raises."""
    try:
        which = str(_cfg_get(cfg, "brain", "local")).strip().lower()
        backend = _BACKENDS.get(which)
        if backend is None:
            return "No brain is configured. Set 'brain' in settings (local, gemini_api, or claude)."
        sys_prompt = CHAT_SYSTEM + ("\n\n" + FAQ if with_faq else "")
        msgs = [{"role": "system", "content": sys_prompt}]
        for h in (history or [])[-8:]:
            role = "user" if h.get("role") == "user" else "assistant"
            txt = str(h.get("text") or "").strip()
            if txt:
                msgs.append({"role": role, "content": txt})
        msgs.append({"role": "user", "content": message})
        reply = backend(msgs, cfg if isinstance(cfg, dict) else _load_cfg_fallback())
        return (reply or "").strip() or "(the brain returned an empty reply)"
    except Exception as exc:  # noqa: BLE001
        return "(brain offline: {0}. Start Ollama or check settings.)".format(exc)


# ---------------------------------------------------------------------------
# Plain-language error diagnosis. Feed a raw error/log line, get back a friendly
# explanation + concrete fix for a non-technical user. Pairs with the Doctor.
# ---------------------------------------------------------------------------
DIAGNOSE_SYSTEM = (
    "You explain a technical error to a NON-TECHNICAL solo game dev running a local "
    "image generator (ComfyUI + Ollama + Stable Diffusion on a Windows gaming laptop). "
    "Given the error text, reply in 2 to 4 short sentences: what went wrong in plain "
    "words, and the single most likely fix he can do himself. No jargon, no em-dash."
)


def diagnose(error_text, context, cfg):
    """Return a friendly plain-language explanation + fix for an error. Falls
    back to a generic pointer if the brain is unavailable."""
    try:
        which = str(_cfg_get(cfg, "brain", "local")).strip().lower()
        backend = _BACKENDS.get(which)
        if backend is None:
            raise RuntimeError("no brain")
        user = "Context: {0}\nError:\n{1}".format(context or "", (error_text or "")[:1500])
        reply = backend([{"role": "system", "content": DIAGNOSE_SYSTEM},
                         {"role": "user", "content": user}],
                        cfg if isinstance(cfg, dict) else _load_cfg_fallback())
        reply = (reply or "").strip()
        if reply:
            return reply
    except Exception:
        pass
    low = (error_text or "").lower()
    if "connection" in low or "refused" in low or "8188" in low:
        return "Looks like ComfyUI is not running. Start ComfyUI, then use Re-check in the health panel."
    if "11434" in low or "ollama" in low:
        return "The local brain (Ollama) is not running. Open a terminal and run: ollama serve"
    if "out of memory" in low or "cuda" in low and "memory" in low:
        return "The GPU ran out of memory. Lower the image size in Settings, or close other GPU apps."
    return "Something failed. Check the health panel (Doctor) for what is missing, then try again."
