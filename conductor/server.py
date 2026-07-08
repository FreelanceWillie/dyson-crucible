"""
server.py - stdlib-only web dashboard for the art-conductor pipeline.

One-stop-shop for a NON-artist: describe a hero, watch the gen queue self-heal,
see candidates, pick a winner, vectorize. No Flask, no external deps beyond the
sibling conductor modules.

On start we launch jobs.worker_loop in a daemon thread (so the queue drains and
retries in the background) and then serve HTTP on 127.0.0.1.

Run:  python -m conductor.server        (or: python conductor/server.py [port])
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Import the sibling modules. We support being run both as a package
# (python -m conductor.server) and as a loose script (python conductor/server.py).
# ---------------------------------------------------------------------------
try:
    from conductor import (cfg, brief as briefmod, brain, jobs, categories as catmod,
                           resources as resmod, models as modelsmod, webref as webrefmod,
                           postprocess as ppmod, taste as tastemod, capabilities as capmod)
except Exception:  # pragma: no cover - fallback for loose-script execution
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cfg  # type: ignore
    import brief as briefmod  # type: ignore
    import brain  # type: ignore
    import jobs  # type: ignore
    import categories as catmod  # type: ignore
    import resources as resmod  # type: ignore
    import models as modelsmod  # type: ignore
    import webref as webrefmod  # type: ignore
    import postprocess as ppmod  # type: ignore
    import taste as tastemod  # type: ignore
    import capabilities as capmod  # type: ignore


import subprocess as _subprocess


def _git(args, timeout=120):
    """Run a git command in the repo root; return (ok, stdout+stderr)."""
    try:
        r = _subprocess.run(["git"] + args, cwd=cfg.REPO_ROOT, capture_output=True,
                            text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


def _version_info():
    """Current commit + whether the remote is ahead (an update is available)."""
    ok, head = _git(["rev-parse", "--short", "HEAD"])
    info = {"version": head.strip() if ok else "unknown", "update_available": False, "behind": 0}
    okd, desc = _git(["log", "-1", "--format=%cd", "--date=short"])
    if okd:
        info["date"] = desc.strip()
    # fetch quietly, then count commits we are behind origin
    _git(["fetch", "--quiet"], timeout=30)
    okc, cnt = _git(["rev-list", "--count", "HEAD..@{u}"])
    if okc and cnt.strip().isdigit():
        info["behind"] = int(cnt.strip())
        info["update_available"] = int(cnt.strip()) > 0
    return info


def _run_update():
    """Pull latest (autostash preserves local config edits). App restart applies it."""
    before_ok, before = _git(["rev-parse", "--short", "HEAD"])
    ok, out = _git(["pull", "--autostash", "--no-edit"], timeout=180)
    after_ok, after = _git(["rev-parse", "--short", "HEAD"])
    changed = before_ok and after_ok and before.strip() != after.strip()
    return {
        "ok": ok,
        "changed": changed,
        "from": before.strip(), "to": after.strip(),
        "log": out.strip()[-2000:],
        "restart_required": changed,
        "note": ("Updated. Restart the app to apply (and re-run update.ps1 if a "
                 "feature-pack node needs its patch)." if changed else "Already up to date."),
    }


# Background feature-group installs: {group_id: {"log": [str], "done": bool, "ok": bool}}
_CAP_PROGRESS = {}
_CAP_LOCK = threading.Lock()


def _cap_install_bg(group_id, conf):
    def log(msg):
        with _CAP_LOCK:
            _CAP_PROGRESS.setdefault(group_id, {"log": [], "done": False, "ok": False})["log"].append(msg)
    with _CAP_LOCK:
        _CAP_PROGRESS[group_id] = {"log": [], "done": False, "ok": False}
    ok = False
    try:
        ok = capmod.install(group_id, conf, log)
    except Exception as exc:  # noqa: BLE001
        log("install crashed: " + str(exc))
    with _CAP_LOCK:
        _CAP_PROGRESS[group_id]["done"] = True
        _CAP_PROGRESS[group_id]["ok"] = ok


REPO_ROOT = cfg.REPO_ROOT
APP_DIR = os.path.join(REPO_ROOT, "app")

def _root_for(key, default):
    """Absolute path for a paths.<key> root, tolerant of cfg.path's signature."""
    try:
        return cfg.path(key)
    except Exception:
        return os.path.join(REPO_ROOT, default)


# Static roots that are safe to serve, keyed by the URL prefix.
_STATIC_ROOTS = {
    "/app": APP_DIR,
    "/outputs": _root_for("outputs", "outputs"),
    "/vectors": _root_for("vectors", "vectors"),
    "/references": _root_for("references", "references"),
}


# ---------------------------------------------------------------------------
# Small helpers for path resolution against config, tolerant of shape.
# ---------------------------------------------------------------------------
def _cfg():
    return cfg.load_config()


def _paths(conf):
    p = conf.get("paths", {}) or {}

    def _abs(key, default):
        val = p.get(key, default)
        if not os.path.isabs(val):
            val = os.path.join(REPO_ROOT, val)
        return val

    return {
        "references": _abs("references", "references"),
        "briefs": _abs("briefs", "briefs"),
        "outputs": _abs("outputs", "outputs"),
        "vectors": _abs("vectors", "vectors"),
    }


def _asset_out_dir(paths, name):
    return os.path.join(paths["outputs"], name)


def _latest_version_dir(asset_dir):
    """Return (vname, vpath) for the highest vNNN dir, or (None, None)."""
    if not os.path.isdir(asset_dir):
        return None, None
    vers = [d for d in os.listdir(asset_dir)
            if d.startswith("v") and os.path.isdir(os.path.join(asset_dir, d))]
    if not vers:
        return None, None
    vers.sort()
    v = vers[-1]
    return v, os.path.join(asset_dir, v)


def _candidate_urls(paths, name):
    """List /outputs/... URLs for the latest version's candidate pngs."""
    asset_dir = _asset_out_dir(paths, name)
    vname, vpath = _latest_version_dir(asset_dir)
    urls = []
    if vpath:
        for f in sorted(os.listdir(vpath)):
            if f.lower().endswith(".png") and f.startswith("cand"):
                urls.append("/outputs/{0}/{1}/{2}".format(name, vname, f))
    return vname, urls


def _next_version_dir(paths, name):
    """Compute the next vNNN path (does not create it; gen.generate should)."""
    asset_dir = _asset_out_dir(paths, name)
    vname, _ = _latest_version_dir(asset_dir)
    n = 0 if vname is None else int(vname[1:]) + 1
    return os.path.join(asset_dir, "v{0:03d}".format(n))


def _url_to_fspath(paths, url):
    """Translate an /outputs/... or /vectors/... URL back to a filesystem path."""
    url = urlparse(url).path if "://" in url or url.startswith("/") else url
    for prefix, root in (("/outputs", paths["outputs"]),
                         ("/vectors", paths["vectors"]),
                         ("/references", paths["references"])):
        if url.startswith(prefix + "/"):
            rel = url[len(prefix) + 1:]
            fp = os.path.normpath(os.path.join(root, rel))
            if os.path.commonpath([os.path.abspath(fp), os.path.abspath(root)]) == os.path.abspath(root):
                return fp
    # Maybe it is already a filesystem path.
    return url


# ---------------------------------------------------------------------------
# The request handler.
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ArtConductor/1.0"

    # Quieter logging.
    def log_message(self, fmt, *args):  # noqa: N802
        sys.stderr.write("[server] " + (fmt % args) + "\n")

    # -- response helpers ---------------------------------------------------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_bytes(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # -- static serving -----------------------------------------------------
    def _serve_static(self, url_path):
        for prefix, root in _STATIC_ROOTS.items():
            if url_path == prefix or url_path.startswith(prefix + "/"):
                rel = url_path[len(prefix):].lstrip("/")
                fp = os.path.normpath(os.path.join(root, rel))
                # Path-traversal guard.
                if os.path.commonpath([os.path.abspath(fp), os.path.abspath(root)]) != os.path.abspath(root):
                    self._send_json({"error": "forbidden"}, 403)
                    return True
                if not os.path.isfile(fp):
                    self._send_json({"error": "not found: " + url_path}, 404)
                    return True
                ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                with open(fp, "rb") as fh:
                    self._send_bytes(fh.read(), ctype)
                return True
        return False

    # -- verb dispatch ------------------------------------------------------
    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/" or path == "/index.html":
                fp = os.path.join(APP_DIR, "index.html")
                if os.path.isfile(fp):
                    with open(fp, "rb") as fh:
                        self._send_bytes(fh.read(), "text/html; charset=utf-8")
                else:
                    self._send_bytes(b"<h1>index.html missing</h1>", "text/html", 404)
                return

            if path.startswith("/api/"):
                self._handle_get_api(path, parse_qs(parsed.query))
                return

            if self._serve_static(path):
                return

            self._send_json({"error": "not found: " + path}, 404)
        except Exception as exc:  # never crash the server
            self._fail(exc)

    def do_POST(self):  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/"):
                self._handle_post_api(path, self._read_json())
                return
            self._send_json({"error": "not found: " + path}, 404)
        except Exception as exc:
            self._fail(exc)

    def _fail(self, exc):
        traceback.print_exc()
        try:
            self._send_json({"error": str(exc) or exc.__class__.__name__}, 500)
        except Exception:
            pass

    # -- API: GET -----------------------------------------------------------
    def _handle_get_api(self, path, query):
        conf = _cfg()
        paths = _paths(conf)

        if path == "/api/state":
            self._send_json(self._build_state(conf, paths))
            return

        if path == "/api/asset":
            name = (query.get("name") or [""])[0]
            if not name:
                self._send_json({"error": "name required"}, 400)
                return
            if not briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "no such asset: " + name}, 404)
                return
            b = briefmod.load(name, paths["briefs"])
            vname, cand_urls = _candidate_urls(paths, name)
            chosen_url = None
            chosen_fp = os.path.join(_asset_out_dir(paths, name), "chosen.png")
            if os.path.isfile(chosen_fp):
                chosen_url = "/outputs/{0}/chosen.png".format(name)
            vector_url = None
            vec_fp = os.path.join(paths["vectors"], name + ".svg")
            if os.path.isfile(vec_fp):
                vector_url = "/vectors/{0}.svg".format(name)
            self._send_json({
                "brief": b,
                "latestVersion": vname,
                "candidates": cand_urls,
                "chosen": chosen_url,
                "vector": vector_url,
            })
            return

        if path == "/api/queue":
            self._send_json({"queue": self._jobs_list()})
            return

        if path == "/api/moodboard":
            # the latest "surprise me" board for an asset: takes + image urls as
            # they complete, so the grid fills in live.
            asset = (query.get("name") or query.get("asset") or [""])[0]
            if not asset:
                self._send_json({"error": "name required"}, 400)
                return
            explores = [j for j in self._jobs_list() if j.get("kind") == "explore" and j.get("asset") == asset]
            job = explores[-1] if explores else None
            takes = []
            directions = (job.get("params", {}) or {}).get("directions", []) if job else []
            results = job.get("result") if job else None
            if isinstance(results, list):
                for r in results:
                    fp = r.get("path", "") if isinstance(r, dict) else ""
                    takes.append({
                        "label": r.get("label", "") if isinstance(r, dict) else "",
                        "prompt": r.get("prompt", "") if isinstance(r, dict) else "",
                        "url": self._fs_to_url(paths, fp),
                    })
            self._send_json({
                "status": job.get("status") if job else "none",
                "job": job.get("id") if job else None,
                "directions": directions,
                "takes": takes,
            })
            return

        if path == "/api/categories":
            # the category tree + each node's inheritable "style DNA"
            self._send_json({"tree": catmod.tree()})
            return

        if path == "/api/resources":
            # never black-box his only machine: live CPU/RAM/GPU/VRAM + queue state
            snap = resmod.snapshot()
            snap["queue_paused"] = jobs.is_paused()
            self._send_json(snap)
            return

        if path == "/api/settings":
            self._send_json({"settings": self._editable_settings(conf)})
            return

        if path == "/api/doctor":
            self._send_json({"checks": self._doctor(conf, paths)})
            return

        if path == "/api/animate/result":
            jid = (query.get("job") or [""])[0]
            job = None
            for j in self._jobs_list():
                if j.get("id") == jid:
                    job = j; break
            if not job:
                self._send_json({"status": "none"}); return
            urls = []
            for p in (job.get("result") or []):
                u = self._fs_to_url(paths, p)
                if u:
                    urls.append(u)
            self._send_json({"status": job.get("status"), "error": job.get("error"), "urls": urls})
            return

        if path == "/api/version":
            self._send_json(_version_info())
            return

        if path == "/api/poses":
            try:
                import animate as animmod
                poses = animmod.list_poses()
                for p in poses:
                    p["url"] = "/app/poses/" + os.path.basename(p["path"])
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500); return
            self._send_json({"poses": poses})
            return

        if path == "/api/capabilities":
            try:
                st = capmod.status(conf)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500); return
            with _CAP_LOCK:
                prog = {k: dict(v) for k, v in _CAP_PROGRESS.items()}
            self._send_json({"groups": st, "progress": prog})
            return

        if path == "/api/models/search":
            q = (query.get("q") or [""])[0]
            kind = (query.get("kind") or ["lora"])[0]
            try:
                results = modelsmod.search(q, kind, conf)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 502)
                return
            self._send_json({"results": results, "kind": kind})
            return

        if path == "/api/models/installed":
            try:
                self._send_json(modelsmod.list_installed(conf))
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/postprocess/steps":
            try:
                self._send_json({"steps": ppmod.available_steps()})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/postprocess/samples":
            # precomputed example of each look on a bundled sample sprite, so the
            # picker shows "what this does" before he uses it. Generated once, cached.
            try:
                sdir = self._ensure_samples()
                samples = {}
                for f in os.listdir(sdir):
                    if f.lower().endswith(".png") and not f.startswith("_"):
                        samples[f[:-4]] = "/app/samples/" + f
                self._send_json({"base": "/app/samples/_base.png", "samples": samples})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/presets":
            self._send_json({"presets": self._load_presets(paths)})
            return

        if path == "/api/chat":
            scope = (query.get("scope") or ["global"])[0]
            name = (query.get("name") or [""])[0]
            self._send_json({"scope": scope, "history": self._load_history(paths, scope, name)})
            return

        if path == "/api/taste":
            sid = (query.get("id") or [""])[0]
            s = tastemod.load(sid, conf) if sid else None
            if not s:
                self._send_json({"error": "no such session"}, 404)
                return
            # attach servable urls to any produced images
            self._decorate_taste(paths, s)
            self._send_json({"session": s})
            return

        self._send_json({"error": "unknown endpoint: " + path}, 404)

    # -- API: POST ----------------------------------------------------------
    def _handle_post_api(self, path, data):
        conf = _cfg()
        paths = _paths(conf)

        if path == "/api/update":
            self._send_json(_run_update())
            return

        if path == "/api/animate/export":
            import animate as animmod
            urls = data.get("frames") or []
            fmt = (data.get("format") or "gif").strip()
            # map servable urls -> disk paths
            frames = []
            for u in urls:
                fp = u
                for pref, key in (("/outputs/", "outputs"), ("/references/", "references"),
                                  ("/vectors/", "vectors")):
                    if isinstance(u, str) and u.startswith(pref):
                        fp = os.path.join(paths.get(key, key), u[len(pref):].replace("/", os.sep)); break
                if os.path.isfile(fp):
                    frames.append(fp)
            if not frames:
                self._send_json({"error": "no valid frames"}, 400); return
            exp_dir = os.path.join(paths.get("outputs", "outputs"), "animation", "export")
            try:
                tw = int(data.get("tween") or 0)
                if tw > 0:
                    frames = animmod.tween(frames, tw, os.path.join(exp_dir, "_tween"),
                                           loop=bool(data.get("loop", True)))
                if fmt == "sheet":
                    out = animmod.export_sheet(frames, os.path.join(exp_dir, "spritesheet.png"),
                                               columns=int(data.get("columns") or 0))
                elif fmt == "zip":
                    out = animmod.export_zip(frames, os.path.join(exp_dir, "frames.zip"))
                else:
                    out = animmod.export_gif(frames, os.path.join(exp_dir, "animation.gif"),
                                             fps=int(data.get("fps") or 8),
                                             loop=bool(data.get("loop", True)))
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500); return
            self._send_json({"ok": True, "url": self._fs_to_url(paths, out)})
            return

        if path == "/api/animate":
            mode = (data.get("mode") or "keyframes").strip()
            asset = (data.get("asset") or "animation").strip()
            params = {
                "mode": mode,
                "prompt": data.get("prompt") or "",
                "negative": data.get("negative") or "",
                "out_dir": os.path.join(paths.get("outputs", "outputs"), asset, mode),
                "seed": int(data.get("seed") or (42 if mode == "keyframes" else 7)),
            }
            if mode == "idle":
                params.update({"frames": int(data.get("frames") or 16),
                               "size": int(data.get("size") or 512),
                               "fps": int(data.get("fps") or 8)})
            else:
                hero = (data.get("hero") or "").strip()
                if not hero:
                    self._send_json({"error": "keyframes mode needs a hero reference image"}, 400); return
                # accept a servable url (/outputs/.., /references/..) -> disk path
                for pref, key in (("/outputs/", "outputs"), ("/references/", "references"),
                                  ("/vectors/", "vectors")):
                    if hero.startswith(pref):
                        hero = os.path.join(paths.get(key, key), hero[len(pref):].replace("/", os.sep))
                        break
                if not os.path.isfile(hero):
                    self._send_json({"error": "hero image not found: " + hero}, 400); return
                params.update({"hero": hero,
                               "poses": data.get("poses") or ["idle"],
                               "identity": float(data.get("identity") or 0.7),
                               "pose_strength": float(data.get("pose_strength") or 1.0)})
            try:
                job = jobs.enqueue(asset, "animate", params)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 400); return
            self._send_json({"ok": True, "job": job, "out_dir": params["out_dir"]})
            return

        if path == "/api/capabilities/install":
            group = (data.get("group") or "").strip()
            if group not in capmod.GROUPS:
                self._send_json({"error": "unknown group: " + group}, 400); return
            with _CAP_LOCK:
                running = group in _CAP_PROGRESS and not _CAP_PROGRESS[group].get("done", True)
            if running:
                self._send_json({"ok": True, "already": True}); return
            threading.Thread(target=_cap_install_bg, args=(group, conf), daemon=True).start()
            self._send_json({"ok": True, "started": group})
            return

        if path == "/api/new":
            name = (data.get("name") or "").strip()
            prompt = (data.get("prompt") or "").strip()
            if not name:
                self._send_json({"error": "name required"}, 400)
                return
            if briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "asset already exists: " + name}, 409)
                return
            b = briefmod.new(name, prompt)
            category = (data.get("category") or "").strip()
            if category:
                b["category"] = category  # asset inherits this category's style DNA
            briefmod.save(name, b, paths["briefs"])
            self._send_json({"ok": True, "brief": b})
            return

        if path.startswith("/api/category/"):
            op = path[len("/api/category/"):]
            cpath = (data.get("path") or "").strip()
            try:
                if op == "new":
                    catmod.add(cpath, data.get("parent"), data.get("settings") or {})
                elif op == "update":
                    catmod.update(cpath, data.get("settings") or {})
                elif op == "move":
                    catmod.move(cpath, data.get("parent"))
                elif op == "delete":
                    catmod.delete(cpath, bool(data.get("cascade")))
                else:
                    self._send_json({"error": "unknown category op: " + op}, 404)
                    return
            except Exception as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            self._send_json({"ok": True, "tree": catmod.tree()})
            return

        if path.startswith("/api/taste/"):
            op = path[len("/api/taste/"):]
            try:
                if op == "start":
                    phrase = (data.get("phrase") or "").strip()
                    n = int(data.get("n") or 8)
                    s = tastemod.start(phrase, n, conf)
                    job = self._enqueue_taste_round(conf, paths, s)
                    self._send_json({"ok": True, "session": s, "job": job})
                    return
                if op == "rate":
                    s = tastemod.load(data.get("session") or "", conf)
                    if not s:
                        self._send_json({"error": "no such session"}, 404)
                        return
                    src = _url_to_fspath(paths, data.get("path") or "") or data.get("path") or ""
                    s = tastemod.rate(s, src, int(data.get("stars") or 0), conf)
                    self._send_json({"ok": True, "session": s})
                    return
                if op == "next":
                    s = tastemod.load(data.get("session") or "", conf)
                    if not s:
                        self._send_json({"error": "no such session"}, 404)
                        return
                    s = tastemod.advance(s, conf)
                    job = self._enqueue_taste_round(conf, paths, s)
                    self._send_json({"ok": True, "session": s, "job": job})
                    return
                if op == "save-as-style":
                    s = tastemod.load(data.get("session") or "", conf)
                    name = (data.get("name") or "").strip()
                    if not s or not name:
                        self._send_json({"error": "session and name required"}, 400)
                        return
                    style = tastemod.emergent_style(s, conf)
                    # loved images become the category's references; descriptors its style
                    ref_dir = os.path.join(paths["references"], *name.split("/"))
                    os.makedirs(ref_dir, exist_ok=True)
                    for i, lp in enumerate(style.get("loved_paths", [])):
                        try:
                            if os.path.isfile(lp):
                                shutil.copyfile(lp, os.path.join(ref_dir, "loved_{0}.png".format(i + 1)))
                        except Exception:
                            pass
                    catmod.add(name, None, {"style_prompt": style.get("style_prompt", ""),
                                            "reference_set": name})
                    self._send_json({"ok": True, "category": name, "style": style})
                    return
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
                return
            self._send_json({"error": "unknown taste op: " + op}, 404)
            return

        if path == "/api/morelike":
            # "More like this": reseed a batch from a chosen candidate (as subject ref)
            name = (data.get("name") or "").strip()
            cand = (data.get("candidate") or "").strip()
            if not name or not briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "no such asset: " + name}, 404)
                return
            src = _url_to_fspath(paths, cand) or cand
            if not os.path.isfile(src):
                self._send_json({"error": "candidate not found"}, 404)
                return
            b = briefmod.load(name, paths["briefs"])
            b["refs"] = [{"role": "subject", "path": src}]
            briefmod.save(name, b, paths["briefs"])
            self._send_json({"ok": True, "job": self._enqueue_gen(conf, paths, name)})
            return

        if path == "/api/character/poses":
            # consistent character: gen the chosen base in new poses/expressions
            name = (data.get("name") or "").strip()
            poses = data.get("poses") or []
            if not name or not briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "no such asset: " + name}, 404)
                return
            chosen = os.path.join(_asset_out_dir(paths, name), "chosen.png")
            if not os.path.isfile(chosen):
                self._send_json({"error": "pick a base image for this character first"}, 400)
                return
            b = briefmod.load(name, paths["briefs"])
            out = []
            for pose in poses:
                pose = str(pose).strip()
                if not pose:
                    continue
                pb = dict(b)
                pb["prompt"] = (b.get("prompt", "") + ", " + pose).strip(", ")
                pb["refs"] = [{"role": "subject", "path": chosen}]
                slug = "".join(c if c.isalnum() else "_" for c in pose)[:24] or "pose"
                out_dir = os.path.join(_asset_out_dir(paths, name), "_poses", slug)
                jid = jobs.enqueue(name, "gen", {"n_candidates": 1, "out_dir": out_dir, "brief": pb,
                                                 "clip_model": (conf.get("rank", {}) or {}).get("clip_model", "")})
                out.append({"pose": pose, "job": jid})
            self._send_json({"ok": True, "jobs": out})
            return

        if path == "/api/presets":
            presets = self._load_presets(paths)
            if data.get("delete"):
                presets = [p for p in presets if p.get("label") != data.get("delete")]
            else:
                label = (data.get("label") or "").strip()
                if not label:
                    self._send_json({"error": "label required"}, 400)
                    return
                presets = [p for p in presets if p.get("label") != label]
                presets.append({"label": label, "prompt": data.get("prompt", ""),
                                "negative": data.get("negative", ""), "style": data.get("style", "")})
            self._save_presets(paths, presets)
            self._send_json({"ok": True, "presets": presets})
            return

        if path == "/api/loras":
            # set the LoRAs applied to a hero's gens: [{name, weight}]
            name = (data.get("name") or "").strip()
            loras = data.get("loras")
            if not name or not briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "no such asset: " + name}, 404)
                return
            b = briefmod.load(name, paths["briefs"])
            b["loras"] = loras if isinstance(loras, list) else []
            briefmod.save(name, b, paths["briefs"])
            self._send_json({"ok": True, "brief": b})
            return

        if path == "/api/refs":
            # attach role-tagged reference images to a hero for multi-image blend
            # refs: [{role:"style"|"subject", path:"<fs path from /api/upload>"}]
            name = (data.get("name") or "").strip()
            refs = data.get("refs")
            if not name or not briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "no such asset: " + name}, 404)
                return
            b = briefmod.load(name, paths["briefs"])
            b["refs"] = refs if isinstance(refs, list) else []
            briefmod.save(name, b, paths["briefs"])
            self._send_json({"ok": True, "brief": b})
            return

        if path == "/api/assign":
            # move an asset into a category (or clear it with an empty category)
            name = (data.get("name") or "").strip()
            category = (data.get("category") or "").strip()
            if not name or not briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "no such asset: " + name}, 404)
                return
            b = briefmod.load(name, paths["briefs"])
            b["category"] = category or None
            briefmod.save(name, b, paths["briefs"])
            self._send_json({"ok": True, "brief": b})
            return

        if path == "/api/say":
            name = (data.get("name") or "").strip()
            feedback = (data.get("feedback") or "").strip()
            do_gen = bool(data.get("gen"))
            if not name or not feedback:
                self._send_json({"error": "name and feedback required"}, 400)
                return
            if not briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "no such asset: " + name}, 404)
                return
            b = briefmod.load(name, paths["briefs"])
            briefmod.append_chat(b, "user", feedback)
            try:
                b = brain.refine_brief(b, feedback, conf)
            except Exception as exc:
                briefmod.append_chat(b, "system", "brain unavailable: " + str(exc))
            briefmod.save(name, b, paths["briefs"])
            job_id = None
            if do_gen:
                job_id = self._enqueue_gen(conf, paths, name)
            self._send_json({"ok": True, "brief": b, "job": job_id})
            return

        if path == "/api/gen":
            name = (data.get("name") or "").strip()
            if not name or not briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "no such asset: " + name}, 404)
                return
            job_id = self._enqueue_gen(conf, paths, name)
            self._send_json({"ok": True, "job": job_id})
            return

        if path == "/api/pick":
            name = (data.get("name") or "").strip()
            candidate = (data.get("candidate") or "").strip()
            if not name or not candidate:
                self._send_json({"error": "name and candidate required"}, 400)
                return
            src = _url_to_fspath(paths, candidate)
            if not os.path.isfile(src):
                self._send_json({"error": "candidate not found: " + candidate}, 404)
                return
            asset_dir = _asset_out_dir(paths, name)
            os.makedirs(asset_dir, exist_ok=True)
            dst = os.path.join(asset_dir, "chosen.png")
            shutil.copyfile(src, dst)
            b = briefmod.load(name, paths["briefs"])
            b["chosen"] = dst
            briefmod.save(name, b, paths["briefs"])
            self._send_json({"ok": True, "chosen": "/outputs/{0}/chosen.png".format(name)})
            return

        if path == "/api/vector":
            name = (data.get("name") or "").strip()
            if not name or not briefmod.exists(name, paths["briefs"]):
                self._send_json({"error": "no such asset: " + name}, 404)
                return
            chosen_fp = os.path.join(_asset_out_dir(paths, name), "chosen.png")
            if not os.path.isfile(chosen_fp):
                self._send_json({"error": "pick a winner before vectorizing"}, 400)
                return
            out_svg = os.path.join(paths["vectors"], name + ".svg")
            params = {
                "png": chosen_fp,
                "out_svg": out_svg,
                "colors": (conf.get("vector", {}) or {}).get("colors", 8),
            }
            job_id = jobs.enqueue(name, "vector", params)
            self._send_json({"ok": True, "job": job_id})
            return

        if path == "/api/explore":
            # "Surprise me": one phrase -> N wildly different takes (a mood board)
            phrase = (data.get("phrase") or "").strip()
            n = int(data.get("n") or 10)
            asset = (data.get("asset") or "explore").strip() or "explore"
            category = (data.get("category") or "").strip()
            if not phrase:
                self._send_json({"error": "phrase required"}, 400)
                return
            directions = brain.explore(phrase, n, conf)
            base = self._explore_base(category)
            out_dir = os.path.join(paths["outputs"], asset, "_explore")
            job = jobs.enqueue(asset, "explore",
                               {"directions": directions, "out_dir": out_dir, "base_brief": base})
            self._send_json({"ok": True, "job": job, "asset": asset, "directions": directions})
            return

        if path == "/api/command":
            # chat as control surface: interpret plain language -> an action
            message = (data.get("message") or "").strip()
            context = data.get("context") or {}
            if not message:
                self._send_json({"error": "message required"}, 400)
                return
            self._send_json(self._run_command(conf, paths, message, context, bool(data.get("gen", True))))
            return

        if path == "/api/synthesize":
            # merge cherry-picked takes into one direction (optionally save to a category)
            phrase = (data.get("phrase") or "").strip()
            picks = data.get("picks") or []
            category = (data.get("category") or "").strip()
            direction = brain.synthesize(phrase, picks, conf)
            if category and direction.get("prompt"):
                try:
                    catmod.update(category, {"style_prompt": direction["prompt"],
                                             "negative": direction.get("negative", "")})
                except Exception:
                    pass
            self._send_json({"ok": True, "direction": direction})
            return

        if path == "/api/queue/pause":
            jobs.set_paused(True)
            self._send_json({"ok": True, "paused": True})
            return
        if path == "/api/queue/resume":
            jobs.set_paused(False)
            self._send_json({"ok": True, "paused": False})
            return
        if path == "/api/queue/cancel":
            job_id = (data.get("id") or "").strip()
            ok = jobs.cancel(job_id, conf) if job_id else False
            self._send_json({"ok": ok})
            return
        if path == "/api/queue/clear":
            removed = jobs.clear_finished()
            self._send_json({"ok": True, "removed": removed})
            return
        if path == "/api/models/download":
            url = (data.get("downloadUrl") or "").strip()
            kind = (data.get("kind") or "lora").strip()
            filename = (data.get("filename") or "").strip()
            if not url or not filename:
                self._send_json({"error": "downloadUrl and filename required"}, 400)
                return
            try:
                dest = modelsmod.dest_dir(kind, conf)
                if not dest:
                    self._send_json({"error": "ComfyUI models dir unknown; set comfyui.root in settings"}, 400)
                    return
                p = modelsmod.download(url, dest, filename, conf)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 502)
                return
            self._send_json({"ok": True, "path": p})
            return

        if path == "/api/webref":
            q = (data.get("query") or "").strip()
            n = int(data.get("n") or 8)
            into = (data.get("into") or "_web").strip() or "_web"
            if not q:
                self._send_json({"error": "query required"}, 400)
                return
            dest = os.path.join(paths["references"], *into.split("/"))
            try:
                saved = webrefmod.fetch(q, n, dest, conf)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 502)
                return
            urls = [self._fs_to_url(paths, p) for p in saved]
            self._send_json({"ok": True, "saved": urls, "into": into,
                             "note": "Private reference only. Do not ship copyrighted or trademarked images."})
            return

        if path == "/api/postprocess/preview":
            # apply ONE step to a downscaled copy of an image, return a data URL.
            # fast for the look filters; the heavy steps (bg_remove/upscale/vectorize)
            # take a moment but still work.
            step = (data.get("step") or "").strip()
            raw_src = data.get("src") or ""
            src = _url_to_fspath(paths, raw_src) or raw_src
            if not os.path.isfile(src) and raw_src.startswith("/app/"):
                src = os.path.join(APP_DIR, raw_src[len("/app/"):].replace("/", os.sep))
            if not step or step not in ppmod.STEPS:
                self._send_json({"error": "unknown step"}, 400)
                return
            if not os.path.isfile(src):
                self._send_json({"error": "source not found"}, 404)
                return
            try:
                import base64
                import tempfile
                from PIL import Image
                tmpd = tempfile.mkdtemp()
                small = os.path.join(tmpd, "in.png")
                im = Image.open(src).convert("RGBA")
                im.thumbnail((256, 256), Image.LANCZOS)
                im.save(small)
                out = os.path.join(tmpd, "out.png")
                res = ppmod.STEPS[step](small, data.get("params") or {}, out)
                if res.lower().endswith(".svg"):
                    with open(res, "r", encoding="utf-8") as fh:
                        self._send_json({"svg": fh.read()})
                    return
                with open(res, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("ascii")
                self._send_json({"dataUrl": "data:image/png;base64," + b64})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/postprocess":
            name = (data.get("name") or "").strip()
            src = (data.get("src") or "").strip()
            if not src and name:
                src = os.path.join(_asset_out_dir(paths, name), "chosen.png")
            if not src or not os.path.isfile(src):
                self._send_json({"error": "need a source image (pick a winner first)"}, 400)
                return
            out_dir = os.path.join(_asset_out_dir(paths, name or "post"), "_post")
            params = {"src": src, "out_dir": out_dir}
            if isinstance(data.get("chain"), list):
                params["chain"] = data["chain"]
            else:
                params["chain_name"] = (data.get("chain_name") or "default")
            job_id = jobs.enqueue(name or "post", "post", params)
            self._send_json({"ok": True, "job": job_id})
            return

        if path == "/api/diagnose":
            reply = brain.diagnose(data.get("error") or "", data.get("context") or "", conf)
            self._send_json({"ok": True, "reply": reply})
            return

        if path == "/api/upload":
            # save a base64 data-url image into a reference set (for multi-image blend)
            durl = data.get("dataUrl") or ""
            into = (data.get("into") or "_uploads").strip() or "_uploads"
            fname = (data.get("filename") or "").strip()
            try:
                import base64
                if "," in durl:
                    header, b64 = durl.split(",", 1)
                else:
                    header, b64 = "", durl
                ext = ".png"
                if "jpeg" in header or "jpg" in header:
                    ext = ".jpg"
                elif "webp" in header:
                    ext = ".webp"
                raw = base64.b64decode(b64)
                if len(raw) > 15 * 1024 * 1024:
                    self._send_json({"error": "image too large"}, 400)
                    return
                dest = os.path.join(paths["references"], *into.split("/"))
                os.makedirs(dest, exist_ok=True)
                if not fname:
                    fname = "upload_{0}{1}".format(len(os.listdir(dest)) + 1, ext)
                fp = os.path.join(dest, fname)
                with open(fp, "wb") as fh:
                    fh.write(raw)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            self._send_json({"ok": True, "path": fp, "url": self._fs_to_url(paths, fp), "into": into})
            return

        if path == "/api/gen/stop":
            # interrupt whatever ComfyUI is generating right now (best effort)
            try:
                import comfyui as _c  # type: ignore
                url = (conf.get("comfyui", {}) or {}).get("url", "")
                if url:
                    _c.interrupt(url)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
                return
            self._send_json({"ok": True})
            return

        if path == "/api/panic":
            # KILL SWITCH / reclaim the machine: pause the queue, stop the current
            # gen, and free ComfyUI's VRAM. Instant GPU back.
            jobs.set_paused(True)
            freed = False
            try:
                import comfyui as _c  # type: ignore
                url = (conf.get("comfyui", {}) or {}).get("url", "")
                if url:
                    _c.interrupt(url)
                    freed = _c.free_vram(url)
            except Exception:
                pass
            self._send_json({"ok": True, "paused": True, "vram_freed": freed})
            return

        if path == "/api/vram/free":
            try:
                import comfyui as _c  # type: ignore
                url = (conf.get("comfyui", {}) or {}).get("url", "")
                ok = _c.free_vram(url) if url else False
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
                return
            self._send_json({"ok": ok})
            return

        if path.startswith("/api/setup/"):
            # actionable Doctor: launch a required service so a red check turns green
            what = path[len("/api/setup/"):]
            try:
                import subprocess
                if what == "start-comfyui":
                    import comfyui as _c  # type: ignore
                    ok = _c.ensure_up(conf)
                    self._send_json({"ok": bool(ok),
                                     "detail": "Starting ComfyUI" if ok else "Set comfyui.exe in settings so it can auto-start."})
                    return
                if what == "start-ollama":
                    try:
                        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        self._send_json({"ok": True, "detail": "Starting Ollama"})
                    except Exception:
                        self._send_json({"ok": False, "detail": "Ollama not found. Install from ollama.com/download."})
                    return
                self._send_json({"error": "unknown setup action"}, 404)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/settings":
            # patch a small allow-list of config.yaml values from the GUI
            try:
                saved = self._save_settings(data.get("settings") or {})
            except Exception as exc:
                self._send_json({"error": str(exc)}, 400)
                return
            self._send_json({"ok": True, "settings": saved})
            return

        self._send_json({"error": "unknown endpoint: " + path}, 404)

    def _ensure_samples(self):
        """Generate a small example of every post-processing step on a bundled
        sample sprite (once, cached under app/samples/), so the picker can show
        what each look does before it is applied."""
        sdir = os.path.join(APP_DIR, "samples")
        marker = os.path.join(sdir, "_done")
        if os.path.isfile(marker):
            return sdir
        os.makedirs(sdir, exist_ok=True)
        from PIL import Image, ImageDraw
        # a simple colorful sprite with transparency (so bg_remove/outline read well)
        s = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        dr = ImageDraw.Draw(s)
        dr.ellipse((14, 14, 114, 114), fill=(70, 120, 210, 255))
        dr.ellipse((40, 34, 88, 82), fill=(245, 205, 70, 255))
        dr.rectangle((58, 74, 70, 116), fill=(200, 60, 60, 255))
        dr.ellipse((52, 44, 62, 54), fill=(20, 20, 30, 255))
        dr.ellipse((70, 44, 80, 54), fill=(20, 20, 30, 255))
        base = os.path.join(sdir, "_base.png")
        s.save(base)
        for name, fn in ppmod.STEPS.items():
            out = os.path.join(sdir, name + ".png")
            try:
                res = fn(base, {}, out)
                # vectorize outputs svg; leave no png so the UI falls back to the base
                if res.lower().endswith(".svg") and os.path.isfile(out):
                    pass
            except Exception:
                pass
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("1")
        return sdir

    def _presets_file(self, paths):
        return os.path.join(paths["outputs"], "_presets.json")

    def _load_presets(self, paths):
        try:
            with open(self._presets_file(paths), "r", encoding="utf-8") as fh:
                return json.load(fh) or []
        except Exception:
            return []

    def _save_presets(self, paths, presets):
        try:
            os.makedirs(paths["outputs"], exist_ok=True)
            with open(self._presets_file(paths), "w", encoding="utf-8") as fh:
                json.dump(presets, fh)
        except Exception:
            pass

    def _fs_to_url(self, paths, fp):
        """Map an on-disk output/vector path back to a servable URL, else ''."""
        if not fp:
            return ""
        fp = os.path.abspath(fp)
        for key, prefix in (("outputs", "/outputs"), ("vectors", "/vectors"),
                            ("references", "/references")):
            root = os.path.abspath(paths.get(key, key))
            try:
                if os.path.commonpath([fp, root]) == root:
                    rel = os.path.relpath(fp, root).replace("\\", "/")
                    return prefix + "/" + rel
            except Exception:
                continue
        return ""

    # -- find-a-style (taste) helpers ---------------------------------------
    def _enqueue_taste_round(self, conf, paths, session):
        """Plan the current round and queue an explore job that conditions on the
        loved-image pool (the emergent taste), so each round drifts toward it."""
        plan = tastemod.plan_round(session, conf)
        directions = plan.get("directions", [])
        ref_paths = plan.get("reference_paths", []) or []
        base = {"reference_set": "default", "ip_adapter_weight": 0.6}
        if ref_paths:
            # steer later rounds by using the loved images as style references
            base["refs"] = [{"role": "style", "path": p} for p in ref_paths if os.path.isfile(p)]
        out_dir = os.path.join(paths["outputs"], "_taste", str(session.get("id")),
                               "round_{0}".format(session.get("round", 0)))
        return jobs.enqueue("_taste_" + str(session.get("id")), "explore",
                            {"directions": directions, "out_dir": out_dir, "base_brief": base})

    def _decorate_taste(self, paths, session):
        for rnd in session.get("rounds", []):
            for im in rnd.get("images", []) or []:
                if isinstance(im, dict) and im.get("path"):
                    im["url"] = self._fs_to_url(paths, im["path"])
        for lv in session.get("loved", []) or []:
            if isinstance(lv, dict) and lv.get("path"):
                lv["url"] = self._fs_to_url(paths, lv["path"])
        return session

    # -- explore + chat command router --------------------------------------
    def _explore_base(self, category):
        """Base brief for an explore run: inherit a category's reference set +
        weight if one was given, so the mood board still respects his style."""
        base = {"reference_set": category or "default", "ip_adapter_weight": 0.55}
        if category:
            try:
                r = catmod.resolve(category)
                base["reference_set"] = r.get("reference_set") or category
                if r.get("ip_adapter_weight") is not None:
                    base["ip_adapter_weight"] = r["ip_adapter_weight"]
            except Exception:
                pass
        return base

    def _run_command(self, conf, paths, message, context, do_gen):
        context = dict(context or {})
        # Two chat scopes, each with its own durable rolling history queried on input:
        # 'asset' (this hero's brief.chat) or 'global' (the control panel).
        scope = (context.get("scope") or ("asset" if context.get("asset") else "global"))
        context["scope"] = scope
        context["history"] = self._load_history(paths, scope, context.get("asset"))
        routed = brain.interpret(message, context, conf)
        action = routed.get("action")
        p = routed.get("params") or {}
        out = {"ok": True, "action": action, "params": p, "message": message, "scope": scope}
        try:
            if action == "explore":
                phrase = (p.get("phrase") or message).strip()
                n = int(p.get("n") or 10)
                cat = (p.get("category") or context.get("category") or "").strip()
                asset = (context.get("asset") or "explore")
                dirs = brain.explore(phrase, n, conf)
                out_dir = os.path.join(paths["outputs"], asset, "_explore")
                job = jobs.enqueue(asset, "explore",
                                   {"directions": dirs, "out_dir": out_dir,
                                    "base_brief": self._explore_base(cat)})
                out.update({"job": job, "asset": asset, "directions": dirs})
            elif action == "new_category":
                name = (p.get("name") or "").strip()
                look = (p.get("look") or "").strip()
                n = int(p.get("explore_n") or 0)
                if name:
                    catmod.add(name, None, {"style_prompt": look})
                    out["category"] = name
                    if n > 0:
                        dirs = brain.explore(look or name, n, conf)
                        out_dir = os.path.join(paths["outputs"], name, "_explore")
                        job = jobs.enqueue(name, "explore",
                                           {"directions": dirs, "out_dir": out_dir,
                                            "base_brief": self._explore_base(name)})
                        out.update({"job": job, "asset": name, "directions": dirs})
            elif action == "refine":
                name = context.get("asset")
                if name and briefmod.exists(name, paths["briefs"]):
                    b = briefmod.load(name, paths["briefs"])
                    b = brain.refine_brief(b, p.get("feedback") or message, conf)
                    briefmod.save(name, b, paths["briefs"])
                    out["brief"] = b
                    if do_gen:
                        out["job"] = self._enqueue_gen(conf, paths, name)
                else:
                    out["reply"] = "Pick or create a hero first, then tell me what to change."
            elif action == "generate":
                name = context.get("asset")
                if name and briefmod.exists(name, paths["briefs"]):
                    out["job"] = self._enqueue_gen(conf, paths, name)
                else:
                    out["reply"] = "Select a hero to generate."
            elif action == "assign":
                name = context.get("asset")
                cat = (p.get("category") or "").strip()
                if name and briefmod.exists(name, paths["briefs"]):
                    b = briefmod.load(name, paths["briefs"])
                    b["category"] = cat or None
                    briefmod.save(name, b, paths["briefs"])
                    out["brief"] = b
            else:  # help / chat -> a real conversational reply from the brain
                hist = context.get("history") or []
                out["reply"] = brain.chat(p.get("text") or message, hist, conf,
                                          with_faq=(scope == "global"))
        except Exception as exc:
            out["error"] = str(exc)
        # persist the turn to the right rolling store (asset refine already logs to
        # brief.chat, so only persist here for the global scope + plain replies).
        try:
            if scope == "global":
                self._append_history(paths, "global", None, "user", message)
                note = out.get("reply") or ("did: " + str(action))
                self._append_history(paths, "global", None, "assistant", note)
            elif out.get("reply") and action not in ("refine",):
                self._append_history(paths, "asset", context.get("asset"), "user", message)
                self._append_history(paths, "asset", context.get("asset"), "assistant", out["reply"])
        except Exception:
            pass
        return out

    def _global_chat_file(self, paths):
        return os.path.join(paths["outputs"], "_global_chat.json")

    def _load_history(self, paths, scope, asset):
        """Rolling window (last ~16 turns) for the given scope."""
        try:
            if scope == "global":
                with open(self._global_chat_file(paths), "r", encoding="utf-8") as fh:
                    return (json.load(fh) or [])[-16:]
            if asset and briefmod.exists(asset, paths["briefs"]):
                return (briefmod.load(asset, paths["briefs"]).get("chat") or [])[-16:]
        except Exception:
            pass
        return []

    def _append_history(self, paths, scope, asset, role, text):
        if scope == "global":
            hist = []
            try:
                with open(self._global_chat_file(paths), "r", encoding="utf-8") as fh:
                    hist = json.load(fh) or []
            except Exception:
                hist = []
            hist.append({"role": role, "text": text})
            hist = hist[-200:]  # cap the on-disk log
            try:
                os.makedirs(paths["outputs"], exist_ok=True)
                with open(self._global_chat_file(paths), "w", encoding="utf-8") as fh:
                    json.dump(hist, fh)
            except Exception:
                pass
        elif asset and briefmod.exists(asset, paths["briefs"]):
            b = briefmod.load(asset, paths["briefs"])
            briefmod.append_chat(b, role, text)
            briefmod.save(asset, b, paths["briefs"])

    # -- settings + doctor --------------------------------------------------
    # Only these keys are exposed to the GUI (safe, useful knobs). Nested keys
    # use dotted paths so the client can send a flat patch.
    _SETTING_KEYS = [
        "brain", "ollama_model", "gemini_model",
        "gen.n_candidates", "gen.steps", "gen.cfg", "gen.width", "gen.height",
        "gen.ip_adapter", "engine", "comfyui.checkpoint",
        "rank.clip_model", "vector.colors",
        "queue.max_retries", "queue.poll_seconds", "queue.restart_engine_on_fail",
    ]

    def _editable_settings(self, conf):
        out = {}
        for key in self._SETTING_KEYS:
            cur = conf
            for part in key.split("."):
                cur = (cur or {}).get(part) if isinstance(cur, dict) else None
            out[key] = cur
        return out

    def _save_settings(self, patch):
        import yaml  # config is yaml
        cfg_path = cfg.resolve("config.yaml")
        with open(cfg_path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        for key, val in patch.items():
            if key not in self._SETTING_KEYS:
                continue
            parts = key.split(".")
            node = doc
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = val
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
        os.replace(tmp, cfg_path)
        cfg.load_config(force_reload=True)
        return self._editable_settings(cfg.load_config())

    def _doctor(self, conf, paths):
        """Environment self-check for a non-technical first run: what is ready,
        what is missing, and the one-line fix for each."""
        checks = []

        def add(name, ok, detail, fix=""):
            checks.append({"name": name, "ok": bool(ok), "detail": detail, "fix": fix})

        # brain
        try:
            ok, detail = brain.available(conf)
        except Exception as exc:
            ok, detail = False, str(exc)
        add("Conductor brain (" + str(conf.get("brain", "local")) + ")", ok, detail,
            "Start it: run `ollama serve` and `ollama pull " + str(conf.get("ollama_model", "")) + "`")
        # engine
        if (conf.get("engine") or "comfyui") == "comfyui":
            try:
                import comfyui as _c  # type: ignore
                up = _c.is_up((conf.get("comfyui", {}) or {}).get("url", ""))
            except Exception:
                up = False
            add("ComfyUI server", up, "reachable" if up else "not reachable",
                "Start ComfyUI (or set comfyui.exe in config so it auto-starts).")
        else:
            add("Diffusers engine", True, "in-process (no ComfyUI needed)", "")
        # references
        ref_root = paths["references"]
        has_refs = os.path.isdir(ref_root) and any(
            f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
            for _r, _d, fs in os.walk(ref_root) for f in fs
        )
        add("Style references", has_refs, "found images" if has_refs else "none yet",
            "Drop 8 to 20 style images in references/default/.")
        # python deps
        for mod, why in [("open_clip", "candidate ranking"), ("vtracer", "vectorizing"),
                         ("rembg", "transparent sprites (bg remove)"), ("psutil", "resource meters")]:
            try:
                __import__(mod)
                add("Python: " + mod, True, "installed (" + why + ")", "")
            except Exception:
                add("Python: " + mod, False, "missing (" + why + ")", "Run setup.ps1 again.")
        # LayerDiffuse (native transparent gen) readiness: node + models present.
        try:
            comfy_cfg = (conf.get("comfyui") or {}) if isinstance(conf, dict) else {}
            root = comfy_cfg.get("root") or ""
            candidates = []
            if root:
                candidates += [os.path.join(root, "custom_nodes", "ComfyUI-layerdiffuse"),
                               os.path.join(root, "ComfyUI", "custom_nodes", "ComfyUI-layerdiffuse")]
            node_dir = next((p for p in candidates if os.path.isdir(p)), None)
            if node_dir:
                # models auto-download on first use; presence is a bonus, not required.
                add("Transparent gen (LayerDiffuse)", True, "node installed",
                    "Set gen.transparent: native to generate true alpha. Models "
                    "(~330MB) download automatically on first use.")
            else:
                add("Transparent gen (LayerDiffuse)", False, "node not found",
                    "Optional. Run bootstrap.ps1 to install it, or use "
                    "gen.transparent: cut (rembg) which needs no extra install.")
        except Exception:
            pass  # doctor must never crash on an optional probe

        # gpu + low-vram guidance
        gpu = resmod.snapshot().get("gpu")
        if gpu:
            vram = gpu.get("vram_total_mb") or 0
            low = vram and vram <= 6144
            add("GPU", True, gpu.get("name", "GPU") + " " + str(vram) + "MB",
                "Under 6GB: launch ComfyUI with --lowvram and keep image size at 512. "
                "Your system RAM absorbs the overflow (slower, but no crash)." if low
                else "Plenty of VRAM for SD1.5.")
        else:
            add("GPU", False, "no NVIDIA GPU detected", "Generation will run on CPU (slow but works).")
        return checks

    # -- shared builders ----------------------------------------------------
    def _enqueue_gen(self, conf, paths, name):
        out_dir = _next_version_dir(paths, name)
        params = {
            "n_candidates": (conf.get("gen", {}) or {}).get("n_candidates", 4),
            "out_dir": out_dir,
            "clip_model": (conf.get("rank", {}) or {}).get("clip_model", ""),
            "ref_dir": paths["references"],
        }
        return jobs.enqueue(name, "gen", params)

    def _jobs_list(self):
        try:
            return jobs.list_jobs() or []
        except Exception:
            return []

    def _build_state(self, conf, paths):
        assets = []
        for name in briefmod.list_assets(paths["briefs"]):
            try:
                b = briefmod.load(name, paths["briefs"])
            except Exception:
                continue
            vname, cand_urls = _candidate_urls(paths, name)
            chosen_url = None
            chosen_fp = os.path.join(_asset_out_dir(paths, name), "chosen.png")
            if os.path.isfile(chosen_fp):
                chosen_url = "/outputs/{0}/chosen.png".format(name)
            thumb = chosen_url or (cand_urls[0] if cand_urls else None)
            assets.append({
                "name": name,
                "prompt": b.get("prompt", ""),
                "category": b.get("category"),
                "chosen": chosen_url,
                "thumb": thumb,
                "candidateCount": len(cand_urls),
                "lastVersion": vname,
            })

        brain_info = {"name": conf.get("brain", "local"), "ok": False, "detail": "unknown"}
        try:
            ok, detail = brain.available(conf)
            brain_info["ok"] = bool(ok)
            brain_info["detail"] = detail
        except Exception as exc:
            brain_info["detail"] = str(exc)

        return {
            "assets": assets,
            "queue": self._jobs_list(),
            "brain": brain_info,
        }


# ---------------------------------------------------------------------------
# Boot: worker thread + HTTP serve.
# ---------------------------------------------------------------------------
def _start_worker():
    """Kick off jobs.worker_loop in a daemon thread so the queue self-heals."""
    stop_flag = threading.Event()
    conf = _cfg()

    def _run():
        try:
            jobs.worker_loop(conf, stop_flag)
        except Exception:
            traceback.print_exc()

    t = threading.Thread(target=_run, name="job-worker", daemon=True)
    t.start()
    return stop_flag, t


def serve(port=None):
    if port is None:
        port = int(os.environ.get("CONDUCTOR_PORT", "7860"))
    _start_worker()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:{0}/".format(port)
    print("Dyson Crucible dashboard running at " + url)
    print("Open that URL in your browser. Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    port = None
    if argv:
        try:
            port = int(argv[0])
        except ValueError:
            pass
    serve(port)


if __name__ == "__main__":
    main()
