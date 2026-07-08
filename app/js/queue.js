// queue.js - the top-bar resource meters + machine-reclaim controls, and the
// bottom queue strip (live jobs, cancel, clear). Owns #resbar and #queuestrip.
import { api } from './api.js';
import { state, on, toast } from './state.js';

function meter(label, pct, sub) {
  const cls = pct >= 90 ? 'bad' : pct >= 70 ? 'warn' : '';
  return `<span class="meter ${cls}" title="${label} ${sub || ''}">${label}<span class="bar"><i style="width:${Math.max(0, Math.min(100, pct || 0))}%"></i></span></span>`;
}

function renderRes() {
  const r = state.resources; const bar = document.getElementById('resbar');
  if (!bar) { return; }
  if (!r) { bar.innerHTML = '<span class="faint">reading machine...</span>'; return; }
  const parts = [];
  if (r.cpu_pct != null) { parts.push(meter('CPU', r.cpu_pct)); }
  if (r.ram) { parts.push(meter('RAM', r.ram.pct, `${(r.ram.used_mb / 1024).toFixed(1)}/${(r.ram.total_mb / 1024).toFixed(1)}GB`)); }
  if (r.gpu) { parts.push(meter('GPU', r.gpu.util_pct, r.gpu.name));
    parts.push(meter('VRAM', r.gpu.vram_pct, `${r.gpu.vram_used_mb}/${r.gpu.vram_total_mb}MB`)); }
  else { parts.push('<span class="chip">CPU only</span>'); }
  const paused = r.queue_paused;
  parts.push(`<button class="btn sm" id="qpause">${paused ? '&#9654; Resume' : '&#10073;&#10073; Pause'}</button>`);
  parts.push(`<button class="btn sm bad" id="qpanic" title="Pause, stop the current gen, and free the GPU">&#9888; Reclaim machine</button>`);
  bar.innerHTML = parts.join('');
  const p = bar.querySelector('#qpause'); if (p) { p.onclick = () => (paused ? api.qResume() : api.qPause()).then(() => toast(paused ? 'Queue resumed' : 'Queue paused')); }
  const k = bar.querySelector('#qpanic'); if (k) { k.onclick = () => api.panic().then((r) => toast(r.vram_freed ? 'Machine reclaimed. GPU freed.' : 'Queue paused, gen stopped.', 'good')); }
}

function renderQueue() {
  const q = state.queue || []; const strip = document.getElementById('queuestrip');
  if (!strip) { return; }
  const active = q.filter((j) => j.status === 'queued' || j.status === 'running');
  const now = Date.now() / 1000;
  const chips = q.slice(-8).map((j) => {
    const sp = j.status === 'running' ? '<span class="spinner" style="display:inline-block;vertical-align:middle"></span> ' : '';
    const cancel = (j.status === 'queued' || j.status === 'running') ? ` <a href="#" data-cancel="${j.id}">cancel</a>` : '';
    const tries = j.tries ? ` (retry ${j.tries})` : '';
    // elapsed on a running job = "is it doing anything?"
    let elapsed = '';
    if (j.status === 'running' && j.created) { const s = Math.max(0, Math.round(now - j.created)); elapsed = ` <span class="faint">${s}s</span>`; }
    // failed job -> a "why?" link that reveals the captured error
    let why = '';
    if (j.status === 'failed' && j.error) { why = ` <a href="#" data-why="${j.id}" style="color:var(--bad)">why?</a>`; }
    return `<span class="pill ${j.status}" ${j.error ? `title="${esc(j.error).slice(0, 300)}"` : ''}>${sp}${esc(j.kind)}:${esc(j.asset)} ${esc(j.status)}${tries}${elapsed}${cancel}${why}</span>`;
  }).join(' ');
  strip.innerHTML = `<b>Queue</b> <span class="chip">${active.length} active</span> ${chips || '<span class="faint">idle</span>'} <button class="btn sm ghost" id="qdiag">Diagnostics</button> <button class="btn sm ghost" id="qclear">Clear finished</button>`;
  strip.querySelectorAll('[data-cancel]').forEach((a) => a.onclick = (e) => { e.preventDefault(); api.qCancel(a.dataset.cancel).then(() => toast('Cancelled')); });
  strip.querySelectorAll('[data-why]').forEach((a) => a.onclick = (e) => {
    e.preventDefault();
    const j = (state.queue || []).find((x) => x.id === a.dataset.why);
    if (j && j.error) { showError(j); }
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

export function mount() {
  on('resources', renderRes);
  on('queue', renderQueue);
  renderRes(); renderQueue();
}
