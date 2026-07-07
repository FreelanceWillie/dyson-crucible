"""Web image-reference fetch for the local art pipeline (art-conductor).

Fetch a few reference images from a *keyless* web image search so the user can
feed them to an art pipeline (e.g. IP-Adapter style/subject input) or just use
them as loose inspiration while authoring.

------------------------------------------------------------------------------
COPYRIGHT / TRADEMARK NOTE  --  READ THIS.
------------------------------------------------------------------------------
Images fetched by this module are pulled from the public web and are almost
certainly COPYRIGHTED and/or TRADEMARKED. They are intended for PRIVATE,
LOCAL reference ONLY (mood-boarding, style/subject conditioning, study).

    * DO NOT ship, redistribute, or embed any fetched image (or a derivative
      that is recognizably the same character / logo / person) in a game or
      any other released product.
    * Using "the main character from <IP>" as a reference does NOT grant any
      right to that character's likeness. Recognizable IP must not end up in
      shipped content.

Treat everything this module returns as throwaway scratch input, never as an
asset.
------------------------------------------------------------------------------
"""

import json
import os
import re

# --- defensive / lazy config -------------------------------------------------

_DEFAULTS = {
    "provider": "duckduckgo",
    "max_images": 8,
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_TIMEOUT = 15
_MAX_BYTES = 10 * 1024 * 1024  # ~10MB cap per file

# content-type -> extension for the formats we keep as-is
_KEEP_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _cfg_get(cfg, key, default):
    """Read webref.<key> from a cfg object/dict, falling back to default.

    Accepts either a dict-like cfg or a module exposing a `cfg` mapping, and
    tolerates the whole config layer being unavailable.
    """
    try:
        if cfg is None:
            # Lazy import of the project's config; fine if it's missing.
            try:
                from conductor import config as _config  # type: ignore
                cfg = getattr(_config, "cfg", None) or getattr(_config, "CONFIG", None)
            except Exception:
                cfg = None
        if cfg is None:
            return default

        # Support nested {"webref": {...}} or flat "webref.<key>" access.
        section = None
        if isinstance(cfg, dict):
            section = cfg.get("webref")
        else:
            section = getattr(cfg, "webref", None)

        if section is None:
            return default
        if isinstance(section, dict):
            val = section.get(key, default)
        else:
            val = getattr(section, key, default)
        return default if val is None else val
    except Exception:
        return default


def _require_requests():
    """Import `requests` lazily with a clear message if it's missing."""
    try:
        import requests  # noqa: F401
        return requests
    except Exception as exc:  # pragma: no cover - env dependent
        raise RuntimeError(
            "webref needs the 'requests' package. Install it with: "
            "pip install requests"
        ) from exc


# --- DuckDuckGo keyless image search -----------------------------------------
#
# NOTE: this uses DuckDuckGo's UNOFFICIAL image endpoint (i.js / vqd token
# handshake). It is undocumented and may change or break without notice; all
# failures are swallowed and result in an empty list.


def _ddg_vqd(requests, query):
    """Fetch the vqd token DuckDuckGo requires for image queries."""
    try:
        resp = requests.get(
            "https://duckduckgo.com/",
            params={"q": query},
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
        html = resp.text or ""
        # vqd appears in a few shapes: vqd='...'  vqd="..."  vqd=...&
        for pat in (
            r"vqd=['\"]([-\d]+)['\"]",
            r"vqd=([-\d]+)&",
            r"vqd=([-\d]+)",
        ):
            m = re.search(pat, html)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def search_images(query, n=8, cfg=None):
    """Search the web for reference images (keyless).

    Args:
        query: free-text subject/style, e.g. "brutalist cathedral".
        n:     max number of results to return.
        cfg:   optional config object/dict; falls back to project cfg/defaults.

    Returns:
        list[dict] of up to `n` items shaped:
            {"url": str, "thumb": str, "title": str, "source": str}
        Empty list on any failure (never raises).
    """
    query = (query or "").strip()
    if not query:
        return []

    try:
        n = int(n)
    except Exception:
        n = _cfg_get(cfg, "max_images", _DEFAULTS["max_images"])
    if n <= 0:
        return []

    provider = _cfg_get(cfg, "provider", _DEFAULTS["provider"])
    if str(provider).lower() != "duckduckgo":
        # Only the keyless DDG provider is implemented; be graceful otherwise.
        return []

    try:
        requests = _require_requests()
    except Exception:
        return []

    vqd = _ddg_vqd(requests, query)
    if not vqd:
        return []

    try:
        resp = requests.get(
            "https://duckduckgo.com/i.js",
            params={
                "l": "us-en",
                "o": "json",
                "q": query,
                "vqd": vqd,
                "f": ",,,",
                "p": "1",
            },
            headers={
                "User-Agent": _UA,
                "Referer": "https://duckduckgo.com/",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception:
        return []

    results = []
    try:
        for item in (data.get("results") or []):
            url = item.get("image")
            if not url:
                continue
            results.append({
                "url": url,
                "thumb": item.get("thumbnail") or url,
                "title": (item.get("title") or "").strip(),
                "source": item.get("url") or "",
            })
            if len(results) >= n:
                break
    except Exception:
        return []

    return results


# --- download ----------------------------------------------------------------


def _ext_for(content_type, url):
    """Pick a file extension from content-type, falling back to the URL."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _KEEP_EXT:
        return _KEEP_EXT[ct]
    if ct.startswith("image/"):
        # Some other image type (gif/bmp/tiff/svg/avif...) -- keep by url ext.
        pass
    elif ct:
        # Non-image content-type -> reject.
        return None
    # Fall back to the URL's extension if it looks like a known image type.
    m = re.search(r"\.(jpg|jpeg|png|webp)(?:$|[?#])", (url or "").lower())
    if m:
        e = m.group(1)
        return ".jpg" if e == "jpeg" else "." + e
    return None


def fetch(query, n, dest_dir, cfg=None):
    """Search then download the top `n` reference images into `dest_dir`.

    Files are written as web_1.<ext>, web_2.<ext>, ... . Items that fail to
    download, are not images, or exceed the ~10MB cap are skipped. PNG/JPG/WEBP
    are kept as-is.

    Never raises out to the caller -- returns the list of paths that actually
    saved (may be shorter than `n`, or empty).

    Remember: these files are PRIVATE reference only (see module docstring).
    """
    saved = []
    try:
        try:
            n = int(n)
        except Exception:
            n = _cfg_get(cfg, "max_images", _DEFAULTS["max_images"])
        if n <= 0:
            return saved

        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception:
            return saved

        try:
            requests = _require_requests()
        except Exception:
            return saved

        # Over-fetch a little so failed downloads don't starve the target count.
        candidates = search_images(query, n=max(n * 3, n), cfg=cfg)

        idx = 0
        for item in candidates:
            if len(saved) >= n:
                break
            url = item.get("url")
            if not url:
                continue
            idx += 1
            path = _download_one(requests, url, dest_dir, len(saved) + 1)
            if path:
                saved.append(path)
    except Exception:
        # Absolutely never propagate; return whatever we managed to save.
        return saved
    return saved


def _download_one(requests, url, dest_dir, seq):
    """Download a single image with streaming, size cap and content-type check.

    Returns the saved path, or None on any failure.
    """
    try:
        with requests.get(
            url,
            headers={"User-Agent": _UA, "Accept": "image/*,*/*;q=0.8"},
            timeout=_TIMEOUT,
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                return None

            content_type = resp.headers.get("Content-Type", "")
            ext = _ext_for(content_type, url)
            if not ext:
                return None

            # Reject obviously oversized payloads up front when declared.
            try:
                declared = int(resp.headers.get("Content-Length") or 0)
            except Exception:
                declared = 0
            if declared and declared > _MAX_BYTES:
                return None

            out_path = os.path.join(dest_dir, "web_%d%s" % (seq, ext))
            total = 0
            try:
                with open(out_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > _MAX_BYTES:
                            raise ValueError("exceeds size cap")
                        fh.write(chunk)
            except Exception:
                # Clean up a partial/oversized file.
                try:
                    os.remove(out_path)
                except Exception:
                    pass
                return None

            if total == 0:
                try:
                    os.remove(out_path)
                except Exception:
                    pass
                return None

            return out_path
    except Exception:
        return None


# --- reachability ------------------------------------------------------------


def available(cfg=None):
    """Quick reachability check for the Doctor / UI.

    Returns (ok: bool, message: str). Never raises.
    """
    try:
        requests = _require_requests()
    except Exception as exc:
        return (False, str(exc))

    try:
        resp = requests.get(
            "https://duckduckgo.com/",
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return (True, "duckduckgo reachable")
        return (False, "duckduckgo returned HTTP %s" % resp.status_code)
    except Exception as exc:
        return (False, "cannot reach duckduckgo: %s" % exc)
