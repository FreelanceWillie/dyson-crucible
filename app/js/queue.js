// queue.js - the top-bar resource meters + machine-reclaim controls, and the
// bottom queue strip (live jobs, cancel, clear). Owns #resbar and #queuestrip.
import { api } from './api.js';
import { state, on, toast } from './state.js';

function meter(label, pct, sub) {
  const cls = pct >= 90 ? 'bad' : pct >= 70 ? 'warn' : '';
  return `<span class="meter ${cls}" title="${label} ${sub || ''}">${label}<span class="bar"><i style="width:${Math.max(0, Math.min(100, pct || 0))}%"></i></span></span>`;
}

const clampPct = (v) => Math.max(0, Math.min(100, v || 0));

// A two-segment meter: the Dyson Crucible share (accent) + everything else (muted),
// each labelled with its % of the machine's max. Lets the user see whether the tool
// or some OTHER program is the resource sink.
function splitMeter(label, dcPct, otherPct, title) {
  const d = clampPct(dcPct), o = clampPct(otherPct);
  return `<span style="display:inline-flex;align-items:center;gap:5px" title="${title || ''}">`
    + `<span style="font-size:11px">${label}</span>`
    + `<span style="width:66px;height:8px;border-radius:4px;overflow:hidden;background:var(--line,#333);display:inline-flex">`
    + `<span style="width:${d}%;background:var(--accent,#6cf)"></span>`
    + `<span style="width:${o}%;background:var(--faint,#888)"></span></span>`
    + `<span style="font-size:11px;color:var(--faint,#888)">DC ${d.toFixed(0)}% &middot; other ${o.toFixed(0)}%</span>`
    + `</span>`;
}

function renderRes() {
  const r = state.resources; const bar = document.getElementById('resbar');
  if (!bar) { return; }
  if (!r) { bar.innerHTML = '<span class="faint">reading machine...</span>'; return; }
  const parts = [];
  // Engine (ComfyUI) status pill so he knows when he can actually generate.
  const eng = (r.engine && r.engine.state) || 'off';
  const engMap = {
    ready: ['&#9679; Engine ready', 'var(--good)', 'ComfyUI is up. Generation runs immediately.'],
    warming: ['&#9673; Engine warming up (first run ~1-2 min)', 'var(--accent,#6cf)',
      'ComfyUI is starting and loading the model. The first run of a session is slow; later ones are fast.'],
    off: ['&#9675; Engine off', 'var(--faint,#888)',
      'ComfyUI is not running. It starts automatically the first time you generate (~1-2 min).'],
  };
  const [engTxt, engCol, engTip] = engMap[eng] || engMap.off;
  parts.push(`<span class="chip" title="${engTip}" style="color:${engCol};border-color:${engCol}">${engTxt}</span>`);
  const dc = r.dc || null;
  if (r.cpu_pct != null) {
    if (dc) {
      const dcc = dc.cpu_pct || 0;
      parts.push(splitMeter('CPU', dcc, r.cpu_pct - dcc, `Dyson Crucible ${dcc.toFixed(0)}% of CPU, everything else ${(r.cpu_pct - dcc).toFixed(0)}%`));
    } else { parts.push(meter('CPU', r.cpu_pct)); }
  }
  if (r.ram) {
    if (dc) {
      const dcp = 100 * dc.ram_mb / (r.ram.total_mb || 1);
      parts.push(splitMeter('RAM', dcp, r.ram.pct - dcp,
        `Dyson Crucible ${(dc.ram_mb / 1024).toFixed(1)}GB, other ${((r.ram.used_mb - dc.ram_mb) / 1024).toFixed(1)}GB of ${(r.ram.total_mb / 1024).toFixed(1)}GB`));
    } else { parts.push(meter('RAM', r.ram.pct, `${(r.ram.used_mb / 1024).toFixed(1)}/${(r.ram.total_mb / 1024).toFixed(1)}GB`)); }
  }
  if (r.gpu) {
    parts.push(meter('GPU', r.gpu.util_pct, r.gpu.name));
    if (dc) {
      const dcv = 100 * dc.vram_mb / (r.gpu.vram_total_mb || 1);
      parts.push(splitMeter('VRAM', dcv, r.gpu.vram_pct - dcv,
        `Dyson Crucible ${dc.vram_mb}MB, other ${r.gpu.vram_used_mb - dc.vram_mb}MB of ${r.gpu.vram_total_mb}MB`));
    } else { parts.push(meter('VRAM', r.gpu.vram_pct, `${r.gpu.vram_used_mb}/${r.gpu.vram_total_mb}MB`)); }
  } else { parts.push('<span class="chip">CPU only</span>'); }
  const paused = r.queue_paused;
  parts.push(`<button class="btn sm" id="qpause">${paused ? '&#9654; Resume' : '&#10073;&#10073; Pause'}</button>`);
  parts.push(`<button class="btn sm bad" id="qpanic" title="Pause, stop the current gen, and free the GPU">&#9888; Reclaim machine</button>`);
  bar.innerHTML = parts.join('');
  const p = bar.querySelector('#qpause'); if (p) { p.onclick = () => (paused ? api.qResume() : api.qPause()).then(() => toast(paused ? 'Queue resumed' : 'Queue paused')); }
  const k = bar.querySelector('#qpanic'); if (k) { k.onclick = () => api.panic().then((r) => toast(r.vram_freed ? 'Machine reclaimed. GPU freed.' : 'Queue paused, gen stopped.', 'good')); }
}

// checkpoint (model) downloads: tracked globally so progress shows in the strip
// even after the picker is closed, with a toast on finish.
let ckptDls = {};   // id -> { name, pct, done, ok, error }
let ckptPoll = null;

function watchCkpt() {
  if (ckptPoll) { return; }
  const tick = async () => {
    let d;
    try { d = await api.checkpoints(); } catch (_) { return; }
    const prog = d.progress || {};
    let anyActive = false;
    Object.keys(prog).forEach((id) => {
      const p = prog[id] || {};
      const name = ((d.catalog || []).find((c) => c.id === id) || {}).name || id;
      const prev = ckptDls[id];
      ckptDls[id] = { name, pct: p.pct || 0, done: !!p.done, ok: !!p.ok, error: p.error || '' };
      if (!p.done) { anyActive = true; }
      if (p.done && (!prev || !prev.done)) {  // completion transition -> toast once
        if (p.ok) { toast(name + ' downloaded. Now drawing with it.', 'good'); }
        else { toast(name + ' download failed. ' + (p.error || ''), 'bad'); }
      }
    });
    renderQueue();
    if (!anyActive && ckptPoll) { clearInterval(ckptPoll); ckptPoll = null; }
  };
  tick();
  ckptPoll = setInterval(tick, 2000);
}

function renderQueue() {
  const q = state.queue || []; const strip = document.getElementById('queuestrip');
  if (!strip) { return; }
  const active = q.filter((j) => j.status === 'queued' || j.status === 'running');
  const chips = q.slice(-8).map((j) => {
    const cancel = (j.status === 'queued' || j.status === 'running') ? ` <a href="#" data-cancel="${j.id}">cancel</a>` : '';
    const tries = j.tries ? ` (retry ${j.tries})` : '';
    let lead = '', tail = '';
    if (j.status === 'running') {
      lead = (typeof j.pct === 'number' ? ring(j.pct) : '<span class="spinner" style="display:inline-block;vertical-align:middle"></span>') + ' ';
      if (typeof j.eta === 'number') { tail = ` <span class="faint">~${fmtSecs(j.eta)} left</span>`; }
      else if (typeof j.elapsed === 'number') {
        // no learned duration yet (first/cold run): set expectations so it does not
        // look frozen -- the engine + model load take a minute or two the first time.
        const warm = j.elapsed > 12 ? ' <span class="faint">warming up the engine (first run ~1-2 min)</span>' : '';
        tail = ` <span class="faint">${fmtSecs(j.elapsed)}</span>${warm}`;
      }
    }
    // failed job -> a "why?" link that reveals the captured error
    let why = '';
    if (j.status === 'failed') { why = ` <a href="#" data-why="${j.id}" style="color:var(--bad)">why?</a>`; }
    return `<span class="pill ${j.status}" ${j.error ? `title="${esc(j.error).slice(0, 300)}"` : ''}>${lead}${esc(j.kind)}:${esc(j.asset)} ${esc(j.status)}${tries}${tail}${cancel}${why}</span>`;
  }).join(' ');
  // model downloads (checkpoints) show here too, so a multi-GB download is visible
  // app-wide even when the picker is closed.
  const dlChips = Object.values(ckptDls).filter((d) => !d.done).map((d) =>
    `<span class="pill running" title="Downloading a model">&#8681; ${esc(d.name)} ${Math.round(d.pct || 0)}%</span>`).join(' ');
  strip.innerHTML = `<b>Queue</b> <span class="chip">${active.length} active</span> ${dlChips} ${chips || (dlChips ? '' : '<span class="faint">idle</span>')} <button class="btn sm ghost" id="qdiag">Diagnostics</button> <button class="btn sm ghost" id="qclear">Clear finished</button>`;
  strip.querySelectorAll('[data-cancel]').forEach((a) => a.onclick = (e) => { e.preventDefault(); api.qCancel(a.dataset.cancel).then(() => toast('Cancelled')); });
  strip.querySelectorAll('[data-why]').forEach((a) => a.onclick = (e) => {
    e.preventDefault();
    const j = (state.queue || []).find((x) => x.id === a.dataset.why);
    if (j) { showError({ ...j, error: j.error || 'No error text was captured. Open Diagnostics (below) and send the full report.' }); }
  });
  const c = strip.querySelector('#qclear'); if (c) { c.onclick = () => api.qClear().then(() => toast('Cleared')); }
  const d = strip.querySelector('#qdiag'); if (d) { d.onclick = showDiagnostics; }
}

function showError(job) {
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:grid;place-items:center;padding:20px';
  box.innerHTML = `<div style="background:var(--bg,#111);border:1px solid var(--line,#333);border-radius:10px;max-width:680px;width:100%;padding:16px;max-height:80vh;overflow:auto">
    <div class="row" style="justify-content:space-between"><b>Why "${esc(job.kind)}:${esc(job.asset)}" failed</b><button class="btn sm ghost" id="ecx">Close</button></div>
    <pre style="white-space:pre-wrap;word-break:break-word;font-size:12px;margin-top:10px">${esc(job.error)}</pre>
    <button class="btn sm" id="ecopy" style="margin-top:8px">Copy</button></div>`;
  document.body.appendChild(box);
  box.querySelector('#ecx').onclick = () => box.remove();
  box.onclick = (e) => { if (e.target === box) { box.remove(); } };
  box.querySelector('#ecopy').onclick = () => { navigator.clipboard.writeText(job.error).then(() => toast('Copied', 'good')); };
}

async function showDiagnostics() {
  let d;
  try { d = await api.diagnostics(); } catch (e) { toast('Diagnostics unavailable: ' + e.message, 'bad'); return; }
  const text = d.text || JSON.stringify(d, null, 2);
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:grid;place-items:center;padding:20px';
  box.innerHTML = `<div style="background:var(--bg,#111);border:1px solid var(--line,#333);border-radius:10px;max-width:760px;width:100%;padding:16px;max-height:85vh;overflow:auto">
    <div class="row" style="justify-content:space-between"><b>Diagnostics</b><button class="btn sm ghost" id="dcx">Close</button></div>
    <div class="faint" style="margin:6px 0">Copy this and send it to whoever is helping you set up.</div>
    <pre style="white-space:pre-wrap;word-break:break-word;font-size:11px">${esc(text)}</pre>
    <button class="btn sm" id="dcopy" style="margin-top:8px">Copy all</button></div>`;
  document.body.appendChild(box);
  box.querySelector('#dcx').onclick = () => box.remove();
  box.onclick = (e) => { if (e.target === box) { box.remove(); } };
  box.querySelector('#dcopy').onclick = () => { navigator.clipboard.writeText(text).then(() => toast('Copied', 'good')); };
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

function fmtSecs(s) { s = Math.max(0, Math.round(s)); return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`; }

// A small SVG fill-ring showing pct (0..1).
function ring(pct) {
  const r = 7, C = 2 * Math.PI * r;
  const off = C * (1 - Math.max(0, Math.min(1, pct)));
  return `<svg width="16" height="16" viewBox="0 0 18 18" style="vertical-align:middle">
    <circle cx="9" cy="9" r="${r}" fill="none" stroke="currentColor" stroke-opacity="0.25" stroke-width="2.5"/>
    <circle cx="9" cy="9" r="${r}" fill="none" stroke="var(--accent,#6cf)" stroke-width="2.5" stroke-linecap="round"
      stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 9 9)"/></svg>`;
}

export function mount() {
  on('resources', renderRes);
  on('queue', renderQueue);
  on('ckptInstall', watchCkpt);   // a model download started -> show it in the strip
  renderRes(); renderQueue();
  // If a download is already running (e.g. page was reloaded mid-download), pick it up.
  api.checkpoints().then((d) => {
    if (Object.values(d.progress || {}).some((p) => p && !p.done)) { watchCkpt(); }
  }).catch(() => {});
}
