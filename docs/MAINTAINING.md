# Maintaining Dyson Crucible

The operating manual for whoever keeps this project healthy. Start here, then follow the
links. Repo is the source of truth: everything a maintainer needs lives in-tree.

## 1. What this is, in one breath

A local, free, no-subscription AI art studio for a **non-artist friend, Sol**. He talks to
it, rates images, and picks winners; it makes game-ready art (transparent PNGs, pixel
sprites, clean vectors, pose frames, idle loops) entirely on his own machine. No hosted
service, no API keys, no bills.

- Repo: `github.com/FreelanceWillie/dyson-crucible` (public). Local: `E:\Tools\art-conductor`.
- Author identity for every commit: **FreelanceWillie <thefreelancewillie@gmail.com>**.
  Never commit as "Aggfot" (public repo; keep it clean).

## 2. Who it is for (design north star)

Sol is the user. He is **not** a developer or artist. Every decision bends toward:
**ease of use, never confuse the user, always keep them in the loop on what the app is
doing and why.** When in doubt, make it more obvious, not more powerful. Short plain copy
over verbose. It should *feel great*, not merely work.

His hardware (the performance target): ASUS TUF FX506HE, **RTX 3050 Ti Laptop, 4GB VRAM**,
16GB RAM, Windows 11, Python via the `py` launcher. Everything must fit a 4GB card
(low-VRAM handling, size 512, idle loops page to system RAM and run slow).

## 3. Architecture map

Full detail: [ARCHITECTURE.md](ARCHITECTURE.md) and [EXTENDING.md](EXTENDING.md). The shape:

- **Backend** = stdlib Python, no framework. `conductor/*.py`.
  - `server.py` = REST API (ThreadingHTTPServer). Dual-mode import: package or loose
    script. **The launcher runs it as a loose script** (`python conductor/server.py`),
    which is why lazy `import gen`/`import animate` resolve via the sys.path fallback.
  - `jobs.py` = self-healing queue worker (retries, relaunches ComfyUI, recovers crashes).
  - `gen.py` = generation (candidate batches, transparent modes: off/cut/native).
  - `animate.py` = pose frames (IP-Adapter + ControlNet) and idle loops (AnimateDiff).
  - `comfyui.py` = ComfyUI HTTP client + self-heal launch (`ensure_up`).
  - `capabilities.py` = feature packs (on-demand download/install of optional deps).
  - `brain.py` = the conductor brain (local Ollama / Gemini / Claude) + built-in FAQ.
  - `resources.py`, `taste.py`, `models.py`, `webref.py`, `postprocess.py`, `cfg.py`.
- **Frontend** = framework-free ES modules, `app/js/*.js`, loaded through `server.py`
  (browsers block `import` over `file://`, so never open the HTML directly). Modules talk
  only through the `state.js` event bus (`emit`/`on`) and `api.js`. See EXTENDING.md §4.
- **The engine** = ComfyUI (sibling install or `DC_COMFYUI_ROOT`). API-format workflows in
  `workflows/*.json`; the checkpoint is injected at runtime by `gen.py`.

## 4. Run and verify (no test suite)

There is no automated test suite. Verify by booting and probing.

```bash
# real launch path (what the .bat uses):
py conductor/server.py 7860        # then open http://127.0.0.1:7860

# smoke test endpoints (expect 200):
for p in /api/state /api/resources /api/version /api/capabilities /api/poses; do
  curl -s -o /dev/null -w "$p -> %{http_code}\n" http://127.0.0.1:7860$p
done
```

- **Always test the loose-script path** (`py conductor/server.py`), not `-m conductor.server`.
  Under `-m`, lazy bare imports (`import animate`) fail with 500 on `/api/poses`; that is a
  `-m`-only artifact, not a real bug.
- Syntax gate before commit: `py -c "import ast; ast.parse(open('conductor/FILE.py').read())"`
  for Python, `node --check app/js/FILE.js` for JS.
- **Reclaim the box after testing**: kill any ComfyUI / server you started. This machine
  hosts remote sessions; do not leave GPU or ports held.

## 5. Current state and known issues

- **Sol's install is one dependency from working.** ComfyUI core needs **torchaudio matched
  to his torch build** (`2.5.1+cu121` -> `torchaudio 2.5.1+cu121` from the cu121 index).
  A mismatched index gives a DLL error. `tools/verify_comfyui.ps1` filters `torch*` from
  ComfyUI's requirements and installs the matched torchaudio. If a fresh install still
  fails on `import torchaudio`, that is the place to look.
- Onboarding self-assembles on a clean Windows box: `bootstrap.ps1` auto-installs Python /
  Git / 7-Zip (winget or official installer), then ComfyUI + Ollama + models + config.
  Double-click entry points: `Dyson Crucible.bat`, `Update.bat`, `Diagnostics.bat`.
- Diagnostics: in-app `/api/diagnostics` + the **Diagnostics** button (bottom bar) +
  `Diagnostics.bat` for when the app will not open. When Sol reports a failure, get the
  diagnostics report first; it carries build type, model files, and ComfyUI's crash log.

## 6. Update / release flow

- Maintainer: commit only your own files by path (`git add <paths>`, never `git add -A`);
  never `git stash` (this tree has concurrent WIP). Push to `origin` (`master`).
- User update: `Update.bat` (or `git pull` then `Update.bat`). It pulls, re-runs the
  idempotent bootstrap (re-verifies engine + models, re-patches nodes), and restarts.
  `update.ps1` is the guts.

## 7. House rules (non-negotiable)

- **No em-dash (`—`) in any user-facing string** (labels, toasts, chat, prompts). Commas,
  periods, ellipses only. Comments/docs may use them.
- Commit author = FreelanceWillie (see §1). Commit messages end with the
  `Co-Authored-By: Claude Opus 4.8` trailer when AI-assisted.
- Secrets never live in the repo. (This project needs none; it is fully local.)
- Keep the UI honest and in-the-loop: every long action shows progress + state (Engine
  pill, fill-ring + ETA, "why?" on failures). Do not add a feature that can look frozen.
- The brain is a conductor, not a developer: it only emits JSON patches within the
  contract. See [LLM_GUIDE.md](LLM_GUIDE.md).

## 8. Doc index

- [README.md](../README.md) - what it is + quick start (user-facing).
- [ARCHITECTURE.md](ARCHITECTURE.md) - how it is built, data flow, resilience.
- [EXTENDING.md](EXTENDING.md) - backend/frontend maps, module contract, how to add features.
- [LLM_GUIDE.md](LLM_GUIDE.md) - the brain's role + JSON patch contract.
- [SETUP.md](SETUP.md) - install detail + manual path.
- [ANIMATION.md](ANIMATION.md) - pose frames + idle loops + 4GB VRAM budget.
