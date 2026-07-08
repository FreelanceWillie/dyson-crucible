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
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
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
    # marker
    L.append(".dc_installed marker: %s" % ("yes" if os.path.isfile(os.path.join(ROOT, ".dc_installed")) else "no"))
    # comfyui startup log
    L.append("\n--- comfyui_startup.log (tail) ---")
    L.append(tail(os.path.join(ROOT, "comfyui_startup.log"), 40))
    return "\n".join(L)


def main():
    report = live_report()
    if report:
        print("(live report from the running app)\n")
        print(report)
    else:
        print(static_report())


if __name__ == "__main__":
    main()
