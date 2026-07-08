#!/usr/bin/env python3
"""Standalone diagnostics dump. Run by Diagnostics.bat.

Prefers the running app's live report (richest); if the app is down, gathers a
static report from the repo so support still gets the essentials. Stdlib only.
"""
import os
import platform
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def live_report():
    try:
        with urllib.request.urlopen("http://127.0.0.1:7860/api/diagnostics", timeout=4) as r:
            import json
            return json.loads(r.read()).get("text")
    except Exception:
        return None


def tail(path, n=30):
    try:
        # utf-8-sig strips a BOM if present (config.yaml / logs can have one, which
        # crashed the cp1252 console on Windows).
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return "".join(fh.readlines()[-n:])
    except Exception:
        return "(not found)"


def static_report():
    L = ["=== Dyson Crucible diagnostics (static: app not running) ==="]
    L.append("platform: %s | python: %s" % (platform.platform(), sys.version.split()[0]))
    # git version
    try:
        import subprocess
        v = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        L.append("version: " + (v.stdout.strip() or "unknown"))
    except Exception:
        L.append("version: (git unavailable)")
    # config.yaml (as-is; small)
    cfgp = os.path.join(ROOT, "config.yaml")
    L.append("\n--- config.yaml ---")
    L.append(tail(cfgp, 200) if os.path.isfile(cfgp) else "(missing)")
    # venv python check
    venv_py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    L.append("\n.venv python: %s" % ("present" if os.path.isfile(venv_py) else "MISSING"))
    # ComfyUI build type (portable = self-contained + reliable; source = fragile).
    L.append("\n--- ComfyUI install ---")
    found = False
    for base in (os.path.join(os.path.dirname(ROOT), "ComfyUI"), "E:/Tools/ComfyUI", "E:/ComfyUI"):
        if os.path.isdir(base):
            has_emb = os.path.isfile(os.path.join(base, "python_embeded", "python.exe"))
            has_bat = os.path.isfile(os.path.join(base, "run_nvidia_gpu.bat"))
            main1 = os.path.isfile(os.path.join(base, "ComfyUI", "main.py"))
            main2 = os.path.isfile(os.path.join(base, "main.py"))
            if has_emb or has_bat or main1 or main2:
                found = True
                kind = "PORTABLE (self-contained)" if (has_emb or has_bat) else "SOURCE build (fragile)"
                L.append("root: %s" % base)
                L.append("type: %s" % kind)
                L.append("python_embeded: %s | run_nvidia_gpu.bat: %s | main.py: %s"
                         % (has_emb, has_bat, main1 or main2))
                # list custom nodes (a hanging one can block startup)
                cn = os.path.join(base, "ComfyUI", "custom_nodes")
                if not os.path.isdir(cn):
                    cn = os.path.join(base, "custom_nodes")
                if os.path.isdir(cn):
                    try:
                        nodes = [d for d in os.listdir(cn) if os.path.isdir(os.path.join(cn, d)) and not d.startswith("__")]
                        L.append("custom_nodes: " + (", ".join(nodes) or "(none)"))
                    except Exception:
                        pass
                break
    if not found:
        L.append("ComfyUI NOT found (not installed).")
    # marker
    L.append(".dc_installed marker: %s" % ("yes" if os.path.isfile(os.path.join(ROOT, ".dc_installed")) else "no"))
    # comfyui logs: the app's launch log AND the installer's verify test-launch logs
    # (%TEMP%). One of these has the real reason ComfyUI will not start.
    L.append("\n--- comfyui_startup.log (app launch, tail) ---")
    L.append(tail(os.path.join(ROOT, "comfyui_startup.log"), 50))
    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    for name in ("dc_comfy_verify.err.log", "dc_comfy_verify.out.log"):
        p = os.path.join(tmp, name)
        L.append("\n--- %s (installer test-launch, tail) ---" % name)
        L.append(tail(p, 50))
    return "\n".join(L)


def _emit(text):
    # Never crash on the Windows cp1252 console: force UTF-8, replace what won't map.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        print(text)
    except Exception:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))


def main():
    report = live_report()
    if report:
        _emit("(live report from the running app)\n")
        _emit(report)
    else:
        _emit(static_report())


if __name__ == "__main__":
    main()
