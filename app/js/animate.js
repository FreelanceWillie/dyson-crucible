// animate.js - the Animation art path. Owns #main when state.view === 'animate'.
// Two modes: pose keyframes (same hero, chosen poses) and idle loop (AnimateDiff).
import { api } from './api.js';
import { state, on, toast, setView } from './state.js';

const el = () => document.getElementById('main');
let mode = 'keyframes';
let hero = null;          // servable url of the chosen hero reference
let picked = new Set();   // chosen pose ids
let poses = [];
let pollTimer = null;

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

async function render() {
  if (state.view !== 'animate') { return; }
  const m = el(); if (!m) { return; }
  if (!poses.length) { try { poses = (await api.poses()).poses || []; } catch (_) {} }
  const assets = (state.assets || []).filter((a) => a.thumb);

  m.innerHTML = `
    <div class="col" style="gap:16px;max-width:900px">
      <div class="row" style="justify-content:space-between">
        <div><h1 style="margin:0">Animate</h1><div class="faint">Bring a hero to life. Runs locally.</div></div>
        <button class="btn sm ghost" id="animBack">Back</button>
      </div>

      <div class="row" style="gap:8px">
        <button class="btn ${mode === 'keyframes' ? 'primary' : ''}" data-mode="keyframes">Pose frames</button>
        <button class="btn ${mode === 'idle' ? 'primary' : ''}" data-mode="idle">Idle loop</button>
      </div>

      ${mode === 'keyframes' ? keyframesForm(assets) : idleForm()}

      <div class="row"><button class="btn primary" id="animGo">${mode === 'idle' ? 'Make idle loop' : 'Generate frames'}</button></div>
      <div id="animOut" class="col" style="gap:10px"></div>
    </div>`;

  m.querySelector('#animBack').onclick = () => setView('home');
  m.querySelectorAll('[data-mode]').forEach((b) => b.onclick = () => { mode = b.dataset.mode; render(); });

  if (mode === 'keyframes') {
    m.querySelectorAll('[data-hero]').forEach((b) => b.onclick = () => { hero = b.dataset.hero; render(); });
    m.querySelectorAll('[data-pose]').forEach((b) => b.onclick = () => {
      const id = b.dataset.pose;
      if (picked.has(id)) { picked.delete(id); } else { picked.add(id); }
      render();
    });
  }
  m.querySelector('#animGo').onclick = go;
}

function keyframesForm(assets) {
  return `
    <div class="h">1. Pick your hero</div>
    ${assets.length ? `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:8px">
      ${assets.map((a) => `<button class="tile ${hero === a.thumb ? 'sel' : ''}" data-hero="${a.thumb}" style="${hero === a.thumb ? 'outline:2px solid var(--accent)' : ''}">
        <img src="${a.thumb}" alt=""><div style="padding:3px;font-size:11px">${esc(a.name)}</div></button>`).join('')}
    </div>` : `<div class="faint">No heroes with a picked image yet. Make one first (New Hero), pick a favorite, then come back.</div>`}

    <div class="h">2. Choose poses (frames)</div>
    <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:8px">
      ${poses.map((p) => `<button class="tile ${picked.has(p.id) ? 'sel' : ''}" data-pose="${p.id}" style="${picked.has(p.id) ? 'outline:2px solid var(--accent)' : ''};background:#111">
        <img src="${p.url}" alt="" style="background:#000"><div style="padding:3px;font-size:11px">${esc(p.label)}</div></button>`).join('')}
    </div>
    <div class="faint">${picked.size} pose(s) selected. Same character, one frame per pose.</div>

    <div class="h">3. Describe it (optional)</div>
    <input id="animPrompt" placeholder="e.g. glowing cyan eyes, ice greatsword" style="width:100%;padding:8px" />
    <label class="row" style="gap:8px"><span class="faint" style="width:120px">Identity strength</span>
      <input id="animIdentity" type="range" min="0.3" max="1.0" step="0.05" value="0.7"></label>`;
}

function idleForm() {
  return `
    <div class="h">Describe the idle animation</div>
    <input id="animPrompt" placeholder="e.g. frost knight standing idle, subtle breathing, cape sway" style="width:100%;padding:8px" />
    <div class="row" style="gap:16px;flex-wrap:wrap">
      <label class="row" style="gap:8px"><span class="faint">Frames</span>
        <select id="animFrames" style="padding:6px"><option>8</option><option selected>16</option></select></label>
      <label class="row" style="gap:8px"><span class="faint">Size</span>
        <select id="animSize" style="padding:6px"><option value="384">384 (fits 4GB better)</option><option value="512" selected>512</option></select></label>
    </div>
    <div class="faint">Idle loops use AnimateDiff. On a 4GB GPU this exceeds VRAM and pages to system RAM, so it works but is slow. Keep frames low / size 384 for speed.</div>`;
}

async function go() {
  const m = el();
  const promptEl = m.querySelector('#animPrompt');
  const payload = { mode, asset: 'animation', prompt: promptEl ? promptEl.value : '' };
  if (mode === 'keyframes') {
    if (!hero) { toast('Pick a hero first', 'bad'); return; }
    if (!picked.size) { toast('Pick at least one pose', 'bad'); return; }
    payload.hero = hero;
    payload.poses = [...picked];
    const id = m.querySelector('#animIdentity');
    if (id) { payload.identity = parseFloat(id.value); }
  } else {
    payload.frames = parseInt(m.querySelector('#animFrames').value, 10);
    payload.size = parseInt(m.querySelector('#animSize').value, 10);
  }
  const out = m.querySelector('#animOut');
  out.innerHTML = `<div class="faint">Starting... the first run may download a feature pack. This can take a while.</div>`;
  let res;
  try { res = await api.animate(payload); }
  catch (e) { out.innerHTML = `<div class="faint" style="color:var(--bad)">Failed: ${esc(e.message)}</div>`; return; }
  pollResult(res.job, out);
}

function pollResult(job, out) {
  if (pollTimer) { clearInterval(pollTimer); }
  let dots = 0;
  pollTimer = setInterval(async () => {
    let r;
    try { r = await api.animateResult(job); } catch (_) { return; }
    if (r.status === 'running' || r.status === 'queued') {
      dots = (dots + 1) % 4;
      out.innerHTML = `<div class="faint">Rendering${'.'.repeat(dots)} (local generation, please wait)</div>`;
      return;
    }
    clearInterval(pollTimer); pollTimer = null;
    if (r.status === 'failed') { out.innerHTML = `<div class="faint" style="color:var(--bad)">Failed: ${esc(r.error || 'unknown')}</div>`; return; }
    const urls = r.urls || [];
    if (!urls.length) { out.innerHTML = `<div class="faint">Done, but no output found.</div>`; return; }
    out.innerHTML = `<div class="h">Result</div><div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px">
      ${urls.map((u) => `<a href="${u}" download><img src="${u}" alt="" style="width:100%;border-radius:6px;background:#000"></a>`).join('')}
    </div><div class="faint">Click any image (or the GIF) to save it.</div>`;
  }, 2000);
}

export function mount() {
  on('view', (v) => { if (v === 'animate') { render(); } });
  on('state', () => { if (state.view === 'animate') { render(); } });
}
