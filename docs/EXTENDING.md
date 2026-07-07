# Extending Dyson Crucible

Audience: a developer (or a coding assistant) who will add features to this tool later.

Read this before touching code. It maps the whole system so you can add a feature without
spelunking through every file. It assumes you can read Python and JavaScript; it does not
assume you already know the codebase.

One rule up front, because it decides who does what:

> **The local 7B brain is an art-direction slot-filler, not a developer.** It fills prompt
> and command slots. It cannot design, wire, or ship code. **Every task in this document is a
> coding task for a human or a capable coding assistant.** Do not expect the local model to
> extend the tool itself. See section 2.

Style note for everything you add: **no em-dash in any user-facing string** (see section 8).

---

## 1. Big picture and data flow

The durable artifact is the **brief** (`briefs/<name>/brief.yaml`). Everything else is derived
from a brief plus a seed. The brain only edits briefs; it never generates or ranks images.

```
  references/<set>/*.png ........... the user's taste (style + yardstick)
  categories.yaml .................. category "style DNA" (inherited down a tree)
          |                                   |
          |  (IP-Adapter conditioning)        |  (folded into the brief: category then asset wins)
          v                                   v
  briefs/<name>/brief.yaml  <---- brain.py <---- user feedback text (plain language)
          |          (structured brief; brain emits a JSON PATCH, never prose)
          v
        gen.py  ---- enqueue ---->  jobs.py  (self-healing queue, outputs/_jobs.json)
                                        |
                                        v
                          engine: comfyui  ->  comfyui.py  --HTTP-->  ComfyUI :8188
                                  diffusers ->  in-process HuggingFace (fallback)
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
                              postprocess.py  (composable chain: trim, bg_remove,
                                               upscale, vectorize, pixelate, ...)
```

Given a brief and a seed, generation, ranking, and post-processing are deterministic. That is
what makes the queue safely resumable.

---

## 2. The two-layer split that matters

There are two distinct "intelligence" layers. Do not confuse them.

- **Layer A: the brain (`brain.py`).** A small local model (Ollama `qwen2.5:7b-instruct` by
  default). Its ONLY job is to turn plain-language feedback into a tiny JSON patch against a
  brief: `prompt`, `negative`, `ip_adapter_weight`, `reference_set`, plus a one-line
  `reasoning`. It never sees images, never judges them, never writes code. Keeping the job that
  narrow is what keeps a 7B reliable. It is an art-direction slot-filler.

- **Layer B: the tool itself.** Python backend + ES-module frontend. This is what you extend.
  Adding a post-process step, an endpoint, a workflow, or a UI panel is a **coding task**.

**The local brain cannot self-extend.** If a change requires new code, new wiring, a new
endpoint, or a new dependency, that is Layer B work and must be done by a developer or a coding
assistant. Nobody should wait for the 7B to build a feature. It can only be asked to fill
prompt or command slots inside features you have already built.

---

## 3. Backend map (`conductor/*.py`)

All modules live under `conductor/`. They are dependency-light on purpose: only PyYAML plus
stdlib at import time. Heavy deps (torch, diffusers, open_clip, requests, PIL, vtracer, rembg,
realesrgan) are imported **lazily** inside the function that needs them, so importing any module
is always cheap and never crashes on a box that is missing a package. Every module tolerates a
missing config by falling back to baked-in defaults.

Modules use a **flat import style** (`import cfg`, `import comfyui`) with a package fallback
(`from conductor import cfg`). Keep new modules the same.

| Module | Owns | Key public functions |
| --- | --- | --- |
| `cfg.py` | Config load + path resolution, anchored to `REPO_ROOT`. Deep-merges `config.yaml` over baked-in `DEFAULTS`, caches it. | `load_config(force_reload=False)`, `resolve(*parts)`, `path(key)`, const `REPO_ROOT` |
| `brief.py` | Per-asset brief storage at `briefs/<name>/brief.yaml`: prompt, negative, ip weight, reference_set, chat log, version history. Knows nothing about gen/rank/LLM. | `new(name, prompt)`, `exists`, `load`, `save`, `append_chat`, `snapshot`, `list_assets` |
| `categories.py` | The category TREE and its inherited "style DNA". Flat map in `categories.yaml` keyed by full path. Resolves effective settings root->leaf, then folds under an asset's brief (asset overrides win). | `load`, `save`, `tree`, `ancestry`, `children`, `resolve(path)`, `effective_brief(brief)`, `add`, `update`, `move`, `delete` |
| `brain.py` | The pluggable slot-filler (Layer A). Builds messages, calls a backend, parses a JSON patch, applies it, logs to chat. Also higher-level helpers used by Explore / chat / diagnose. | `refine_brief(brief, feedback, cfg)`, `available(cfg)`, `explore(phrase, n, cfg)`, `interpret`, `synthesize`, `chat`, `diagnose`. Backends registered in `_BACKENDS` (`_call_local`, `_call_gemini`, `_call_claude`) |
| `comfyui.py` | Self-healing HTTP client for a local ComfyUI server. REST only. Can auto-launch ComfyUI from a configured exe/bat. | `is_up(url)`, `ensure_up(cfg)`, `submit(url, workflow, client_id)`, `wait(url, prompt_id, ...)`, `upload_image`, `interrupt`, `new_client_id` |
| `gen.py` | Brief -> N candidate images. Two engines. Builds the API-format workflow from a template and injects prompt/negative/seed/size/steps/cfg/checkpoint, plus IP-Adapter and LoRA branches at runtime. | `generate(brief, n, out_dir, cfg) -> list[str]`. Internals: `_build_workflow`, `_build_multi_workflow`, `_inject_common`, `_inject_loras`, `_generate_comfyui`, `_generate_diffusers`, `_parse_refs`, `_seed_for` |
| `jobs.py` | Persistent, self-healing queue at `outputs/_jobs.json`. Job kinds: `gen`, `vector` (and post-process). Worker never dies; requeues crash-orphaned jobs; retries with backoff; can relaunch a dead engine. | `enqueue(asset, kind, params)`, `list_jobs`, `get`, `run_next(cfg)`, `worker_loop(cfg, stop_flag=None)`, `cancel`, `clear_finished`, `is_paused`, `set_paused` |
| `rank.py` | CLIP "taste proxy". Embeds the reference set, mean-pools to one centroid, cosine-sorts candidates. No-op (original order) if no references. | `rank(...)`, `best(...)` |
| `vectorize.py` | Raster PNG -> clean SVG. Flatten-first: composite on white, posterize to a small palette, trace with vtracer (py binding, then CLI), optional svgo minify. | `vectorize(png_path, out_svg, colors=12)` |
| `postprocess.py` | Composable post-pick chain. ~26 named steps, each a graceful-degradation function that passes the image through unchanged if its optional dep is missing. `run_chain` never raises. | `run_chain(src, chain, out_dir, cfg=None)`, `run_named(src, name, out_dir, cfg=None)`, `available_steps()`, the `STEPS` registry and `_STEP_META` |
| `models.py` | Model Manager. Search + download LoRAs / ControlNets from Civitai into ComfyUI's `models/` folder. Downloads are always explicit user actions. | `search(query, kind, cfg)`, `download(url, dest_dir, filename, ...)`, `list_installed`, `models_root`, `dest_dir`, `available` |
| `webref.py` | Keyless web image-reference fetch (DuckDuckGo). PRIVATE reference only; the module and UI both warn against shipping fetched imagery. | `search_images(query, n, cfg)`, `fetch(query, n, dest_dir, cfg)`, `available` |
| `taste.py` | "Find a Style": a rate-and-steer session loop. Manages session state + round logic only (does not gen or embed). State in `outputs/_taste/<id>.json`. | `start`, `plan_round`, `record_batch`, `rate`, `advance`, `save`, `load`, `list_sessions`, `emergent_style` |
| `resources.py` | Live machine readout (CPU / RAM / GPU / VRAM / disk) for honest UI meters. psutil optional; GPU via `nvidia-smi` if present. | `snapshot()` |
| `server.py` | The dashboard host. See below. | `serve(port=None)`, `main(argv=None)`, class `Handler` |

### `server.py` is the REST API

`server.py` is **stdlib `http.server`** (`ThreadingHTTPServer` + a `BaseHTTPRequestHandler`
subclass named `Handler`). No Flask, no external web deps. On start it launches
`jobs.worker_loop` in a daemon thread so the queue drains in the background, then serves on
`127.0.0.1:7860`. It serves the static `app/` files and dispatches `/api/*` inside
`do_GET` / `do_POST` by string-matching `self.path`.

Endpoint groups (the full contract is mirrored in `app/js/api.js`, section 4):

- **State / reads:** `/api/state`, `/api/asset`, `/api/queue`, `/api/resources`,
  `/api/settings`, `/api/doctor`, `/api/categories`, `/api/moodboard`, `/api/presets`,
  `/api/chat`.
- **Asset lifecycle:** `/api/new`, `/api/gen`, `/api/pick`, `/api/vector`, `/api/morelike`,
  `/api/character/poses`, `/api/assign`, `/api/refs`, `/api/upload`, `/api/command`, `/api/say`.
- **Explore / taste:** `/api/explore`, `/api/synthesize`, `/api/taste`, `/api/taste/start`,
  `/api/taste/rate`, `/api/taste/next`, `/api/taste/save-as-style`.
- **Queue controls:** `/api/queue/pause`, `/api/queue/resume`, `/api/queue/cancel`,
  `/api/queue/clear`, `/api/gen/stop`.
- **Post-process:** `/api/postprocess`, `/api/postprocess/steps`, `/api/postprocess/samples`,
  `/api/postprocess/preview`.
- **Models / web refs:** `/api/models/search`, `/api/models/installed`, `/api/models/download`,
  `/api/webref`.
- **Categories:** `/api/category/new`, `/api/category/update`, `/api/category/move`,
  `/api/category/delete`.
- **Diagnostics:** `/api/diagnose`.

---

## 4. Frontend map (`app/js/*.js`)

The UI is plain ES modules, no framework, no build step. The pattern is deliberate and gives
crash isolation; follow it exactly so new UI is safe.

### The module contract

Each `app/js/<feature>.js` is an **independent module** that:

1. `export`s a `mount()` function.
2. Imports `api` from `./api.js` and the event bus (`state`, `on`, `emit`, `toast`, ...) from
   `./state.js`. Modules **never import each other**. They coordinate only through the bus.
3. Owns one DOM region, found by id (e.g. `document.getElementById('main')`,
   `'rail'`, `'modal'`, `'ppside'`).
4. In `mount()`, subscribes to bus events and renders. It does not run work at import time.

`app.js` is the boot loop. It **dynamically imports** each module name in `MODULES` inside a
`try/catch` and calls `mount()` if present. A module that throws is logged and skipped, so a
broken feature never takes down the app. After mounting, it calls `refreshState()` and
`startPolling()`.

### The event bus (`state.js`)

`state.js` holds shared state, a tiny pub/sub (`on(evt, fn)` / `emit(evt, payload)`), a `toast`
helper, and the poll loops. Events in use:

| Event | Emitted when | Typical subscriber |
| --- | --- | --- |
| `state` | `/api/state` refreshed (assets, tree, brain) | home, rail |
| `queue` | queue polled (every 2s) | queue |
| `resources` | resources polled (every 2s) | settings / meters |
| `jobdone` | any job flips to `done` | app.js (calls `refreshState`) |
| `select` | an asset is selected (`selectAsset`) | asset, rail |
| `selectCat` | a category is selected | rail |
| `view` | the center-pane view changes (`setView`) | home, asset, explore, taste |
| `open` | a panel should open (`emit('open', '<name>')`) | postprocess, models, webref, ... |
| `offline` | a state refresh failed | modules show their own offline state |

Two routing conventions:

- **`#main` view router.** `state.view` is `'home' | 'asset' | 'explore' | 'taste'`. Modules
  that own the center pane render only when `state.view` matches theirs, subscribing via
  `on('view', v => { if (v === 'mine') render(); })`. Change the view with `setView(v)`.
- **Panels open via `emit('open', '<name>')`.** A panel module subscribes with
  `on('open', n => { if (n === 'postprocess') open(); })`.

Polling pauses when the tab is hidden (to spare the machine) and resumes on `visibilitychange`.

### `api.js` is the endpoint contract

`api.js` wraps every server endpoint as a method (same-origin fetch, returns parsed JSON or
throws with a message). The `api` object's method list IS the client-side contract. When you
add an endpoint, add its wrapper here so UI modules call `api.myThing(...)` instead of raw
`fetch`.

### Current modules

`home`, `rail`, `asset`, `chat`, `queue`, `explore`, `taste`, `postprocess`, `models`,
`webref`, `settings`, `chrome`.

### THE clobber rule (read this)

> **One module per file. Never have two authors edit the same file at once.** This tool was
> deliberately broken out of a monolith so that features are isolated and a broken module cannot
> break the app. If you add a feature, add a NEW `app/js/<feature>.js` and register its name in
> `MODULES` in `app.js`. Do not fold new UI into an existing module, and do not let two people
> or two agent sessions edit the same JS file concurrently. That is exactly the failure the
> module split exists to prevent.

---

## 5. How to add common things

### 5a. A new post-processing step

In `postprocess.py`:

1. Write `step_<name>(in_path, params, out_path)`. Lazily import any dep; on missing dep or
   error, call the passthrough helper so the chain degrades gracefully (never raise).
2. Register it in the `STEPS` dict (`"<name>": step_<name>`).
3. Add a `_STEP_META` entry (label, default params, and `changes_ext` if it changes the file
   extension, e.g. vectorize -> svg).

That is all. `available_steps()` derives the picker from `STEPS` + `_STEP_META`, so the UI shows
your step automatically, and it becomes usable in named chains in `config.yaml`.

### 5b. A new brain backend

In `brain.py`:

1. Write `_call_<name>(messages, cfg) -> str` that returns the raw model text (the shared
   `_parse_patch` turns it into the JSON patch, so you only produce text).
2. Register it in the `_BACKENDS` dict.
3. Add its config keys to `config.yaml` (and `cfg.DEFAULTS`), and select it with `brain:`.

The JSON-patch contract is the seam; nothing else changes.

### 5c. A new ComfyUI capability / workflow

ComfyUI workflows are API-format JSON under `workflows/`. The runtime-injection pattern
(see the IP-Adapter and LoRA paths in `gen.py`) is:

1. Add a workflow JSON with a documented **`_doc` block** listing the injection node ids and
   how they are wired / bypassed. `gen.py` strips `_doc` before submitting.
2. Point config at it (like `comfyui.workflow` / `comfyui.workflow_multi`) or load it in `gen.py`.
3. Inject fields at runtime in `gen.py` by node id, exactly like `_inject_common`,
   `_inject_loras`, and the IP-Adapter branch do (find the node, set its inputs, rewire consumers).

**ControlNet follows the same shape:** the ControlNet ComfyUI custom nodes must be installed,
then add (a) a workflow with ControlNet loader/apply nodes documented in its `_doc`, (b) a
`gen.py` injection path, (c) a brief field to carry the control image / type, and (d) a UI
control to set it. Downloading a ControlNet `.safetensors` via the Model Manager is only half
the job; using it requires this wiring.

### 5d. A new API endpoint + wrapper + UI

1. **Server:** add a `path == "/api/<thing>"` branch in `Handler.do_GET` or `do_POST` in
   `server.py`, call the owning `conductor` module, and reply with `self._send_json(...)`.
2. **Client contract:** add a wrapper to the `api` object in `app/js/api.js`.
3. **UI:** add a NEW `app/js/<thing>.js` that exports `mount()`, register its name in `MODULES`
   in `app.js`, and have it open via `emit('open', '<thing>')` or render on a `view`. Do not
   edit an unrelated module (section 4 clobber rule).

### 5e. A new gen input type (like refs / loras)

Follow how references and LoRAs already thread through the system:

1. **Brief:** add a field to the brief (`brief.py`) so the input persists per asset.
2. **Gen:** handle it in `gen.py` (parse it in `_parse_refs`-style, inject the needed nodes into
   the workflow like `_inject_loras`).
3. **UI:** surface it in `asset.js` (the selected-hero panel owns refs, ip weight, category) and
   add any needed `api.js` method + server endpoint to save it.

---

## 6. The engine choice (`config.yaml` `engine:`)

`engine: comfyui | diffusers`, read by `gen.py`.

- **comfyui** (recommended): drives a local ComfyUI server over HTTP. This engine powers the
  **Model Manager** (LoRA / ControlNet install into ComfyUI's `models/`), **multi-image blend**
  (the stacked IP-Adapter `workflow_multi`), and **low-VRAM handling** (`--lowvram`, so a 4GB
  card streams the model from system RAM). It is queue-native and self-heals.
- **diffusers**: a simpler in-process HuggingFace `diffusers` fallback (fp16 + attention/vae
  slicing for 4GB). No ComfyUI install, but no LoRA/ControlNet manager, no multi-image blend
  workflow, and no ComfyUI queue.

**Porting a ComfyUI-only feature to diffusers:** the work lives in `gen.py`'s
`_generate_diffusers`. ComfyUI features are expressed as workflow-graph node injection; to
support them under diffusers you must re-express them as diffusers pipeline calls (e.g. load a
LoRA via the pipeline's LoRA loader, add a ControlNet pipeline). If a feature is only feasible
in ComfyUI on this hardware, gate it so it degrades cleanly when `engine: diffusers`.

---

## 7. Config surface and on-disk state

### Config (`config.yaml`, loaded by `cfg.py`)

Deep-merged over baked-in `DEFAULTS`, so a missing key is never fatal. Key groups:

- `brain` (`local | gemini_api | claude`) and per-backend settings (`ollama_model`,
  `ollama_url`, `gemini_api_key_env`, `gemini_model`, `claude_cmd`).
- `engine` (`comfyui | diffusers`).
- `comfyui` (`url`, optional `exe` for auto-start, optional `root` for the models folder,
  `workflow`, `workflow_multi`, `checkpoint`, `ip_adapter_preset`).
- `models` (Civitai `civitai_api`, `civitai_token_env`).
- `webref` (`provider`, `max_images`).
- `postprocess` (`default_chain` plus named reusable `chains`).
- `gen` (`base_model`, `steps`, `cfg`, `width`, `height`, `ip_adapter`, `n_candidates`).
- `low_vram` (`enabled`, `max_dim`).
- `queue` (`poll_seconds`, `max_retries`, `restart_engine_on_fail`).
- `rank` (`clip_model`).
- `vector` (`colors`).

### On-disk state

- `briefs/<name>/brief.yaml` - the durable per-asset brief (prompt, negative, ip weight,
  reference_set, chat log, version history). The single source of truth for one asset.
- `categories.yaml` - the flat category tree keyed by full path, with each node's style DNA.
- `references/<set>/*.png` - the user's style reference images, one folder per set (mirrors the
  category tree; a category's `reference_set` defaults to its own path).
- `outputs/<name>/<version>/cand_*.png` and `chosen.png` - candidates and the picked winner.
- `outputs/_jobs.json` - the self-healing queue (see section 8), written atomically.
- `outputs/_global_chat.json` - the global chat log.
- `outputs/_taste/<session_id>.json` - Find-a-Style session state.
- `vectors/<name>.svg` - vectorized winners.

---

## 8. Gotchas

- **No em-dash in user-facing strings.** Never put `-` (em-dash) in any label, toast, chat text,
  or prompt the user reads. Use commas, periods, or ellipses.
- **ES modules require the HTTP server.** The frontend uses `import`, which browsers block over
  `file://`. Always load the UI through `server.py` (`http://127.0.0.1:7860`), not by opening the
  HTML file directly.
- **The gen queue is self-healing and disk-backed.** `jobs.py` persists to `outputs/_jobs.json`
  written atomically (temp file + rename), requeues crash-orphaned `running` jobs on boot,
  retries `failed` jobs with backoff up to `queue.max_retries`, and can relaunch a dead engine
  (`restart_engine_on_fail`). `worker_loop` catches every exception so the worker never dies.
  Do not add code paths that can corrupt or bypass this file; go through the `jobs.py` API.
- **Heavy Python deps are imported lazily.** torch, diffusers, open_clip, requests, PIL,
  vtracer, rembg, realesrgan are imported inside the function that needs them, guarded so a
  missing package degrades gracefully instead of crashing import. Keep new heavy deps lazy and
  guarded; never import them at module top level.
- **Flat imports with a package fallback.** Modules do `import cfg` (flat) with a
  `from conductor import cfg` fallback. Match that in anything new.
- **Config is never fatal.** Because `cfg.DEFAULTS` covers every key, a missing config value
  falls back rather than erroring. If you add a config key, add its default too.
