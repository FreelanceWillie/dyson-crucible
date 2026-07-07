# Dyson Crucible - Architecture

A high-level tour of the system: what it is, how work flows through it, and how the
pieces fit. For the how-to of adding features (step-by-step recipes, contracts, gotchas)
see [EXTENDING.md](EXTENDING.md); this doc stays at the altitude above it and does not
repeat those recipes.

---

## 1. What it is

**Dyson Crucible** is a local, $0 AI art dashboard for a non-artist solo game dev. It runs
entirely on his one machine: a local **ComfyUI** server does the image generation and a local
**Ollama** model does the prompt reasoning. No paid APIs, no cloud, nothing leaves the box.

The premise: a developer who cannot draw can still direct art. He drops a few style-reference
images in a folder, describes a hero in plain language, and the tool generates candidates that
match his taste, ranks them for him, lets him pick and refine by chatting, and post-processes
the winner into whatever form his game needs (transparent sprite, upscaled PNG, vector, pixel
art, ...). The hardware target is modest (a 4GB-VRAM laptop GPU), so low-VRAM safety is a
first-class concern throughout.

---

## 2. Data flow

The durable artifact is the **brief** (`briefs/<name>/brief.yaml`). Everything downstream is
derived from a brief plus a seed, which is exactly what makes the queue safely resumable. The
brain only ever edits briefs; it never generates or ranks images.

```
  references/<set>/*.png ........... the user's taste (style refs + CLIP yardstick)
  categories.yaml .................. category "style DNA" (inherited down a tree)
          |                                   |
          | (IP-Adapter conditioning)         | (folded into the brief; asset overrides win)
          v                                   v
  briefs/<name>/brief.yaml  <---- brain.py <---- user feedback text (plain language)
          |          (structured brief; the brain emits a JSON PATCH, never prose)
          v
        gen.py  ---- enqueue ---->  jobs.py  (self-healing queue, outputs/_jobs.json)
                                        |
                                        v
                    engine: comfyui -> comfyui.py --HTTP--> ComfyUI :8188 (default)
                            diffusers -> in-process HuggingFace (simpler fallback)
                                        |
                                        v
                        outputs/<name>/<version>/cand_*.png   (candidates)
                                        |
                                        v
                             rank.py  (CLIP: candidates vs reference-set centroid)
                                        |
                                        v
                        server.py + app/  (user picks a winner -> chosen.png)
                                        |
                                        v
                        postprocess.py  (composable chain: trim, bg_remove, upscale,
                                         vectorize [optional], pixelate, ... 26 looks)
```

### Three entry points, one funnel

The center stage offers three ways in, and they all feed and consume **categories** (the shared
style DNA tree):

- **New Hero** (the `home` module): you already know what you want. Name it, generate, refine.
- **Surprise Me** (the `explore` module): you want ideas. It fans out a mood board of takes.
- **Find a Style** (the `taste` module): you do not yet know the look. Rate a wide spread of
  images 1-5 stars over several rounds; the session steers toward what you loved until an
  emergent style converges, which you then **save as a category**.

So Find a Style and Surprise Me are how new style DNA is discovered; that DNA lands in
`categories.yaml`, and every subsequent New Hero inherits it. The funnel produces categories;
the categories condition the funnel.

---

## 3. Components

All backend modules live under `conductor/`. They are dependency-light on purpose (PyYAML +
stdlib at import time; heavy deps like torch / diffusers / open_clip / PIL / vtracer / rembg
are imported lazily inside the function that needs them), and every module tolerates a missing
config by falling back to baked-in defaults.

| Module | Responsibility |
| --- | --- |
| `cfg.py` | Load + deep-merge `config.yaml` over baked-in `DEFAULTS`, cache it, and resolve every path relative to `REPO_ROOT`. The one contract every other module imports. |
| `brief.py` | Per-asset brief storage at `briefs/<name>/brief.yaml`: prompt, negative, IP weight, reference set, chat log, version history. The single source of truth for one asset. |
| `categories.py` | The category tree and its inherited "style DNA". Resolves effective settings root-to-leaf, then folds them under an asset's brief (asset overrides win). |
| `brain.py` | The pluggable "art director" slot-filler. Turns plain-language feedback into a small JSON patch against a brief. Backends: local Ollama (default), Gemini API, `claude` CLI. |
| `comfyui.py` | Small self-healing HTTP client for a local ComfyUI server. Liveness, submit workflow, poll, download images, upload refs, interrupt, free VRAM. Can auto-launch ComfyUI. |
| `gen.py` | Turn a brief into N candidate images. Owns both engines; builds the API-format workflow and injects prompt/seed/size/steps/cfg/checkpoint plus IP-Adapter and LoRA branches. |
| `jobs.py` | The persistent, self-healing job queue at `outputs/_jobs.json`. Worker never dies; requeues crash-orphaned jobs; retries with backoff; can relaunch a dead engine; pause/stop. |
| `rank.py` | CLIP "taste proxy": embed the reference set, mean-pool to one centroid, cosine-sort candidates. No-op (original order) when there are no references. |
| `vectorize.py` | Raster PNG to clean SVG. Flatten first (composite on white, posterize to a small palette), trace with vtracer, optional svgo minify. |
| `postprocess.py` | The composable post-pick chain: ~26 named steps, each degrading gracefully (passes the image through unchanged if its optional dep is missing). `run_chain` never raises. |
| `models.py` | Model Manager: search + download LoRAs / ControlNets from Civitai into ComfyUI's `models/` folder. Downloads are always explicit user actions. |
| `webref.py` | Keyless web image-reference fetch (DuckDuckGo). PRIVATE reference only; module and UI both warn against shipping fetched imagery. |
| `taste.py` | "Find a Style" session engine: manages rate-and-steer session state and round logic only (does not gen or embed). State in `outputs/_taste/<id>.json`. |
| `resources.py` | Live machine readout (CPU / RAM / GPU / VRAM / disk) for honest UI meters. psutil optional; GPU via `nvidia-smi` if present. |
| `server.py` | The dashboard host: a stdlib `http.server` exposing the REST API and running the self-healing gen worker in a background thread (see below). |

**`server.py` is a stdlib `http.server`.** It is a `ThreadingHTTPServer` with a
`BaseHTTPRequestHandler` subclass (`Handler`), no Flask and no external web deps. On start it
launches `jobs.worker_loop` in a daemon thread so the queue drains and self-heals in the
background, then serves the static `app/` files plus an `/api/*` REST surface on
`127.0.0.1:7860`. Endpoint groups (full list mirrored in `app/js/api.js`): state/reads,
asset lifecycle, explore/taste, queue controls, post-process, models/web-refs, categories, and
diagnostics. The complete path list is in EXTENDING.md section 3.

---

## 4. The engine

`config.yaml` `engine:` selects how images are actually made, read by `gen.py`.

- **comfyui** (default, recommended): drives the local ComfyUI server over HTTP. This is the
  full-featured engine. It powers the **Model Manager** (LoRA / ControlNet install into
  ComfyUI's `models/`), **multi-image blend** (the stacked IP-Adapter `workflow_multi`), and
  **low-VRAM handling** (`--lowvram`, so a 4GB card streams the model from system RAM instead
  of OOM-crashing). It is queue-native and self-heals.
- **diffusers**: a simpler in-process HuggingFace `diffusers` fallback (fp16 + attention/VAE
  slicing for 4GB). No ComfyUI install required, but also no LoRA/ControlNet manager, no
  multi-image blend, and no ComfyUI queue. It is the easy-to-install escape hatch, not the
  target configuration.

ComfyUI capabilities are expressed as workflow-graph node injection in `gen.py`; the diffusers
path re-expresses only a subset as pipeline calls.

---

## 5. Front end

The UI is plain ES modules: no framework, no build step. The design is deliberately modular so
that each feature is a small file, dynamically imported, with **isolated failures**.

**The boot loop (`app.js`)** dynamically imports each name in its `MODULES` list inside a
`try/catch` and calls the module's `mount()` if present. A module that throws is logged and
skipped, so a broken feature never takes down the app. After mounting it refreshes state and
starts the poll loops.

**The event bus (`state.js`)** holds shared state, a tiny pub/sub (`on` / `emit`), a `toast`
helper, and the poll loops (which pause when the tab is hidden, to spare the machine). Modules
**never import each other**; they coordinate only through the bus and the `#main` view router
(`home | asset | explore | taste`) plus panel `open` events.

**The endpoint contract (`api.js`)** wraps every server endpoint as a method returning parsed
JSON. The `api` object's method list IS the client-side contract.

**Modules** (registered in `MODULES`): `home`, `rail`, `asset`, `chat`, `queue`, `explore`,
`taste`, `postprocess`, `models`, `webref`, `settings`, `chrome`.

**DOM regions** each module owns by id: `#main` (center stage), `#rail` (asset/category tree),
`#modal`, `#ppside` (post-process side panel), `#chatpanel`, `#queuestrip` + `#resbar` (queue
strip and resource meters), `#settings`, `#doctor`, `#help`, `#palette` (command palette), and
`#toasts`.

> **One module per file.** This tool was deliberately broken out of a monolith so features are
> isolated and a broken module cannot break the app. Add a feature as a NEW `app/js/<feature>.js`
> and register its name in `MODULES`; never fold new UI into an existing module, and never let
> two authors edit the same JS file at once.

---

## 6. Resilience

The tool assumes it will crash, lose power, and hog the only machine the user owns, and is built
so none of that loses work.

- **Self-healing queue.** `jobs.worker_loop` (in `jobs.py`, run by `server.py` in a daemon
  thread) catches every exception so the worker never dies. Failed gen jobs retry with backoff
  up to `queue.max_retries`; if ComfyUI dies mid-job the worker can relaunch it
  (`restart_engine_on_fail`, needs `comfyui.exe` configured).
- **Crash recovery.** Queue state persists to `outputs/_jobs.json`, written atomically (temp
  file + rename) so a kill mid-write never corrupts it. On boot, any job orphaned in `running`
  by a prior crash is requeued. Because each job carries its brief snapshot, a requeued job
  regenerates exactly what was asked.
- **The kill switch.** The top bar's **Reclaim machine** control (`api.panic`) lets the user
  take his computer back in one click: it pauses the queue, stops the current gen, and frees
  VRAM (asks ComfyUI to unload models). Paired with the honest CPU/RAM/GPU/VRAM meters from
  `resources.py`, the machine is never a black box.
- **Everything persists to disk.** Briefs, categories, the queue, chat logs, taste sessions,
  candidates, and winners all live as files, so closing the lid and coming back later just works.

---

## 7. The brain is not a developer

There are two distinct "intelligence" layers and it matters that they stay separate:

- **The brain (`brain.py`)** is an art-direction **slot-filler**. Its only job is to turn
  plain-language feedback into a tiny JSON patch (prompt, negative, IP weight, reference set,
  plus a one-line reasoning). It never sees images, never judges them, and never writes code.
  Keeping the job that narrow is what keeps a small local 7B model reliable.
- **The tool itself** (Python backend + ES-module front end) is what you extend.

So **extending Dyson Crucible is a coding task** for a human or a capable coding assistant, not
something the local model can do to itself. Nobody should wait for the 7B to build a feature; it
can only fill prompt or command slots inside features already built. The step-by-step recipes
live in [EXTENDING.md](EXTENDING.md).

---

## 8. On-disk state and config surface

**Config** (`config.yaml`, loaded by `cfg.py`, deep-merged over `cfg.DEFAULTS` so a missing key
is never fatal). Key groups: `brain` (+ per-backend `ollama_*` / `gemini_*` / `claude_cmd`),
`engine`, `comfyui` (url, optional `exe`/`root`, `workflow`, `workflow_multi`, `checkpoint`,
`ip_adapter_preset`), `models` (Civitai), `webref`, `postprocess` (`default_chain` + named
`chains`), `gen` (base_model, steps, cfg, size, ip_adapter, n_candidates, LoRAs), `low_vram`,
`queue`, `rank` (`clip_model`), and `vector` (`colors`).

**State on disk:**

- `briefs/<name>/brief.yaml` - the durable per-asset brief (single source of truth for one asset).
- `categories.yaml` - the flat category tree keyed by full path, each node's style DNA.
- `references/<set>/*.png` - the user's style reference images, one folder per set.
- `outputs/<name>/<version>/cand_*.png` and `chosen.png` - candidates and the picked winner.
- `outputs/_jobs.json` - the self-healing queue, written atomically.
- `outputs/_global_chat.json` - the global chat log.
- `outputs/_taste/<session_id>.json` - Find-a-Style session state.
- `vectors/<name>.svg` - vectorized winners.

---

## 9. Onboarding

The stack (Python venv, Ollama + a model, ComfyUI, the IP-Adapter node, and the model files) is
non-trivial to assemble, so first-run is automated:

- **`bootstrap.ps1`** is a one-click, idempotent, forgiving installer. It walks nine steps
  (venv + deps, Ollama + model, ComfyUI portable/clone into `E:/Tools/ComfyUI`, the IPAdapter
  node, the model files, then wiring `config.yaml`), skips anything already present so it is
  safe to re-run, and wraps every heavy step so one failure prints a manual fallback and
  continues rather than dumping a stack trace.
- **The in-app Doctor** (`/api/doctor`, the `#doctor` region) reports what is present or missing
  at runtime (is ComfyUI up, is Ollama reachable, are the models in place, is VRAM tight) and
  offers actionable fixes, launching a required service so a red check turns green.

---

## Notes on style

**No em-dash in any user-facing string** (labels, toasts, chat text, prompts). Use commas,
periods, or ellipses. ES modules require the HTTP server (browsers block `import` over
`file://`), so always load the UI through `server.py`, never by opening the HTML directly.
