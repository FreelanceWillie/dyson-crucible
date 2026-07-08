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
    launchEditor(urls, out);
  }, 2000);
}

// ----- timeline editor -------------------------------------------------------
let edFrames = [], edIdx = 0, edTimer = null, edFps = 8, edOnion = false, edGif = null;

function launchEditor(urls, out) {
  edGif = urls.find((u) => /\.gif($|\?)/i.test(u)) || null;
  edFrames = urls.filter((u) => /\.png($|\?)/i.test(u));
  if (!edFrames.length && edGif) { // idle with only a gif surfaced
    out.innerHTML = `<div class="h">Result</div><img src="${edGif}" style="max-width:320px;border-radius:8px"><div class="faint"><a href="${edGif}" download>Download GIF</a></div>`;
    return;
  }
  edIdx = 0; edOnion = false; stopPlay();
  renderEditor(out);
  playToggle(out, true);
}

function renderEditor(out) {
  const n = edFrames.length;
  out.innerHTML = `
    <div class="h">Timeline editor (${n} frames)</div>
    <div class="row" style="gap:16px;align-items:flex-start;flex-wrap:wrap">
      <div style="position:relative;width:288px;height:288px;background:#000;border-radius:8px;overflow:hidden">
        <img id="edOnion" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain;opacity:0;filter:hue-rotate(180deg)">
        <img id="edPlay" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain">
      </div>
      <div class="col" style="gap:10px;min-width:220px">
        <div class="row" style="gap:8px"><button class="btn sm" id="edPlayBtn">Pause</button>
          <button class="btn sm ghost" id="edPrev">&#9664;</button><button class="btn sm ghost" id="edNext">&#9654;</button></div>
        <label class="row" style="gap:8px"><span class="faint" style="width:48px">Speed</span>
          <input id="edFps" type="range" min="2" max="24" step="1" value="${edFps}"><span id="edFpsN" class="faint">${edFps} fps</span></label>
        <label class="row" style="gap:8px"><input type="checkbox" id="edOnionChk" ${edOnion ? 'checked' : ''}> <span>Onion skin (see previous frame)</span></label>
      </div>
    </div>

    <div class="h">Frames</div>
    <div class="row" id="edStrip" style="gap:6px;overflow-x:auto;padding-bottom:6px">
      ${edFrames.map((u, i) => `<div class="col" style="gap:2px;align-items:center;flex:0 0 auto">
        <img data-fi="${i}" src="${u}" style="width:64px;height:80px;object-fit:contain;background:#000;border-radius:4px;cursor:pointer;outline:${i === edIdx ? '2px solid var(--accent)' : 'none'}">
        <button class="btn sm ghost" data-del="${i}" style="padding:0 6px;font-size:11px">&times;</button></div>`).join('')}
    </div>

    <div class="h">Export</div>
    <div class="row" style="gap:12px;flex-wrap:wrap;align-items:center">
      <label class="row" style="gap:6px"><span class="faint">Smooth (in-betweens)</span>
        <select id="edTween"><option value="0">off</option><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></label>
      <label class="row" style="gap:6px"><span class="faint">Sheet columns</span><input id="edCols" type="number" min="0" value="0" style="width:56px;padding:4px"></label>
      <button class="btn" data-exp="gif">Export GIF</button>
      <button class="btn" data-exp="sheet">Export sprite sheet</button>
      <button class="btn" data-exp="zip">Download frames</button>
    </div>
    <div class="faint">Smooth adds crossfaded in-between frames (good for small motion; big pose jumps may ghost).</div>
    <div id="edExpOut"></div>`;

  const byId = (id) => out.querySelector('#' + id);
  byId('edPlayBtn').onclick = () => playToggle(out);
  byId('edPrev').onclick = () => { stopPlay(); edIdx = (edIdx - 1 + edFrames.length) % edFrames.length; paint(out); };
  byId('edNext').onclick = () => { stopPlay(); edIdx = (edIdx + 1) % edFrames.length; paint(out); };
  byId('edFps').oninput = (e) => { edFps = parseInt(e.target.value, 10); byId('edFpsN').textContent = edFps + ' fps'; if (edTimer) { playToggle(out, true); } };
  byId('edOnionChk').onchange = (e) => { edOnion = e.target.checked; paint(out); };
  out.querySelectorAll('[data-fi]').forEach((im) => im.onclick = () => { stopPlay(); edIdx = parseInt(im.dataset.fi, 10); paint(out); });
  out.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => {
    const i = parseInt(b.dataset.del, 10); edFrames.splice(i, 1);
    if (edIdx >= edFrames.length) { edIdx = 0; }
    if (!edFrames.length) { stopPlay(); out.innerHTML = '<div class="faint">All frames removed.</div>'; return; }
    renderEditor(out);
  });
  out.querySelectorAll('[data-exp]').forEach((b) => b.onclick = () => doExport(out, b.dataset.exp));
  paint(out);
}

function paint(out) {
  const play = out.querySelector('#edPlay'); const onion = out.querySelector('#edOnion');
  if (!play) { return; }
  play.src = edFrames[edIdx];
  if (onion) { onion.style.opacity = edOnion ? '0.4' : '0'; onion.src = edFrames[(edIdx - 1 + edFrames.length) % edFrames.length]; }
  out.querySelectorAll('[data-fi]').forEach((im) => im.style.outline = (parseInt(im.dataset.fi, 10) === edIdx) ? '2px solid var(--accent)' : 'none');
}

function stopPlay() { if (edTimer) { clearInterval(edTimer); edTimer = null; } }
function playToggle(out, force) {
  const btn = out.querySelector('#edPlayBtn');
  if (edTimer && !force) { stopPlay(); if (btn) { btn.textContent = 'Play'; } return; }
  stopPlay();
  edTimer = setInterval(() => { edIdx = (edIdx + 1) % edFrames.length; paint(out); }, 1000 / edFps);
  if (btn) { btn.textContent = 'Pause'; }
}

async function doExport(out, fmt) {
  const expOut = out.querySelector('#edExpOut');
  const tween = parseInt((out.querySelector('#edTween') || {}).value || '0', 10);
  const columns = parseInt((out.querySelector('#edCols') || {}).value || '0', 10);
  expOut.innerHTML = `<div class="faint">Exporting...</div>`;
  let r;
  try { r = await api.animateExport({ frames: edFrames, format: fmt, fps: edFps, columns, tween, loop: true }); }
  catch (e) { expOut.innerHTML = `<div class="faint" style="color:var(--bad)">Export failed: ${esc(e.message)}</div>`; return; }
  expOut.innerHTML = `<div class="row" style="gap:10px;align-items:center"><a class="btn sm" href="${r.url}" download>Download ${fmt}</a>
    ${/\.(gif|png)$/i.test(r.url) ? `<img src="${r.url}" style="max-height:120px;border-radius:6px;background:#000">` : ''}</div>`;
}

export function mount() {
  on('view', (v) => { if (v === 'animate') { render(); } });
  on('state', () => { if (state.view === 'animate') { render(); } });
}
