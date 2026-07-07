# Art Conductor - Architecture

Internal reference for a developer (or the conductor LLM) working on the system.
Fully local, $0, plug-and-play SD1.5 art dashboard for a solo game dev.

---

## 1. What it does, end to end

The user drops style-reference images in `references/`, opens a browser dashboard,
describes a hero in plain language, and the system generates SD1.5 candidates that
match his reference style, ranks them by CLIP similarity to those references, lets
him pick winners and refine them by chatting, and finally vectorizes winners to SVG.

Everything runs on one Windows 11 laptop (RTX 3050 Ti 4GB, 16GB RAM, i7-11800H).
No paid APIs are required.

---

## 2. Data flow

```
   references/<set>/*.png ......... the user's taste (style + yardstick)
            |
            |  (IP-Adapter conditioning)      (CLIP embedding of the set)
            v                                          |
   briefs/<name>/brief.yaml  <---- brain.py <---- user feedback text
            |        (structured art brief; LLM edits it as a JSON patch)
            v
        gen.py  ----->  jobs.py  (self-healing queue)
                            |
                            v
                     comfyui.py  --HTTP-->  ComfyUI :8188
                            |               (SD1.5 + IP-Adapter workflow)
                            v
                  outputs/<name>/*.png   (candidate images)
                            |
                            v
                       rank.py  (CLIP: candidates vs reference set)
                            |
                            v
                   dashboard (server.py + app/index.html)
                            |  user picks winners
                            v
                    vectorize.py  ---->  vectors/<name>.svg
```

Single arrow of truth: the **brief** is the durable artifact. The brain never
generates images and never ranks them; it only edits the brief. Generation, ranking,
and vectorizing are deterministic given a brief plus a seed.

---

## 3. Modules and responsibilities

All live under `conductor/`. Signatures below are the intended contract; treat them
as the stable seams other modules and the server depend on.

### `cfg.py` - configuration
Loads and validates `config.yaml`. Exposes paths (references, briefs, outputs,
vectors), the ComfyUI endpoint, the brain selection, and generation defaults.
- `load_config(path="config.yaml") -> Config`
- `Config.brain` / `Config.comfyui` / `Config.paths` / `Config.gen` accessors

### `brief.py` - the durable art brief
Reads, writes, and patches `briefs/<name>/brief.yaml`. A brief holds the positive
prompt, negative prompt, `reference_set`, `ip_adapter_weight`, size, steps, cfg,
sampler, and seed policy. Applies JSON patches emitted by the brain.
- `load_brief(name) -> Brief`
- `save_brief(name, brief) -> None`
- `apply_patch(brief, patch: dict) -> Brief`  (clamps + validates fields)
- `new_brief(name, text) -> Brief`

### `brain.py` - the conductor brain (art-direction slot-filler)
Turns natural-language feedback into a small JSON patch against the current brief.
Pluggable backend: local Ollama (`qwen2.5:7b-instruct`) by default; also supports a
free Google AI Studio key (`gemini_api`) or the `claude` CLI. The brain outputs a
patch only; it does not see or judge images. See `docs/LLM_GUIDE.md`.
- `rewrite(brief, feedback: str) -> dict`  (returns the JSON patch)
- backend adapters: `_call_ollama(...)`, `_call_gemini(...)`, `_call_claude(...)`

### `comfyui.py` - engine client
Talks to the ComfyUI server over HTTP (default `127.0.0.1:8188`). Loads the workflow
graph `workflows/sd15_ipadapter.json`, injects prompt/seed/size/IP-Adapter fields,
submits it, polls status, and retrieves finished images. Can optionally auto-start
ComfyUI from `comfyui.exe` in config.
- `is_up() -> bool`
- `ensure_running() -> None`  (auto-start if configured)
- `submit(workflow: dict) -> prompt_id`
- `wait(prompt_id) -> list[Path]`  (returns saved image paths)

### `gen.py` - brief to workflow to candidates
Bridges a brief to concrete generation. Fills the workflow template from the brief,
picks seeds (batch of N candidates), and hands work to the queue.
- `build_workflow(brief, seed) -> dict`
- `enqueue_batch(name, brief, count) -> list[job_id]`

### `jobs.py` - self-healing job queue
Durable queue persisted to `outputs/_jobs.json`. Each job has a state
(`queued -> running -> done | failed`), an attempt count, and its brief snapshot.
Self-healing: on startup any job stuck in `running` (from a crash) is requeued;
`failed` jobs retry with backoff up to a cap; the file is rewritten atomically so a
kill mid-write does not corrupt the queue. This is what lets the user close the lid
and come back.
- `enqueue(job) -> job_id`
- `next_job() -> Job | None`
- `mark(job_id, state, error=None) -> None`
- `recover() -> None`  (requeue orphaned `running` jobs on boot)

### `rank.py` - CLIP ranking (the user's taste)
Embeds the reference set and each candidate with open_clip, scores candidates by
cosine similarity to the mean reference embedding, and returns them sorted. This is
the human's taste encoded numerically, deliberately separate from the LLM.
- `embed_images(paths) -> Tensor`
- `reference_embedding(set_name) -> Tensor`  (cached)
- `rank_candidates(name, set_name) -> list[(path, score)]`

### `vectorize.py` - raster to vector
Turns a chosen PNG into a clean SVG: SD1.5 raster -> posterize (reduce colors) ->
vtracer -> optional svgo minify.
- `vectorize(png_path, out_path, colors=8) -> Path`

### `server.py` - dashboard
Python stdlib `http.server` serving `app/index.html` at `127.0.0.1:7860` plus a
small JSON API the page calls: create/refine brief, enqueue generation, poll job
state, list ranked candidates, pick winners, trigger vectorize.

### `app/index.html`
Single-page dashboard: describe, view ranked candidates, chat-refine, pick, vectorize.

### `workflows/sd15_ipadapter.json`
The ComfyUI graph: SD1.5 checkpoint + IP-Adapter (style from references) + sampler +
save. `gen.py` injects the brief's fields into this template.

---

## 4. Config surface (`config.yaml`)

Owned by another agent; documented here for callers.
- `brain.backend`: `ollama` | `gemini_api` | `claude`
- `brain.model`: e.g. `qwen2.5:7b-instruct`
- `brain.gemini_key` / `brain.gemini_model`: for the free Google AI Studio path
- `comfyui.host` / `comfyui.port`: default `127.0.0.1:8188`
- `comfyui.exe`: optional launcher path for auto-start
- `gen.count`: candidates per batch (default 4)
- `gen.size`, `gen.steps`, `gen.cfg`, `gen.sampler`: SD1.5 defaults
- `gen.ip_adapter_weight`: 0..1 style strength
- `paths.references` / `paths.briefs` / `paths.outputs` / `paths.vectors`

---

## 5. Self-healing queue behavior

- Queue state is the single file `outputs/_jobs.json`, written atomically (temp file +
  rename) so a crash mid-write never corrupts it.
- On boot, `jobs.recover()` requeues anything left in `running` (a prior crash).
- `failed` jobs retry with backoff up to a per-job attempt cap, then park as `failed`
  and surface in the dashboard rather than silently vanishing.
- Because each job carries a brief snapshot, a requeued job regenerates exactly what
  was asked, independent of later brief edits.

---

## 6. Extending the system

**Add a brain backend.** Implement `_call_<name>(brief, feedback) -> dict` in
`brain.py`, return the same JSON patch contract (see `docs/LLM_GUIDE.md`), and add the
name to `brain.backend` handling. Nothing else changes; the patch contract is the seam.

**Swap the workflow.** Replace or add a ComfyUI graph under `workflows/` and point
`gen.build_workflow` at it. Keep the injected field names (prompt, negative, seed,
size, ip_adapter_weight) consistent so `gen.py` can fill them.

**Add SDXL.** Possible but not recommended on 4GB VRAM. Add an SDXL workflow, gate it
behind a config flag, and lower batch size / resolution. Ranking and vectorizing are
model-agnostic and need no change. Keep SD1.5 the default for this hardware.
