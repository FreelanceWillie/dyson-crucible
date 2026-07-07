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
  const chips = q.slice(-8).map((j) => {
    const sp = j.status === 'running' ? '<span class="spinner" style="display:inline-block;vertical-align:middle"></span> ' : '';
    const cancel = (j.status === 'queued' || j.status === 'running') ? ` <a href="#" data-cancel="${j.id}">cancel</a>` : '';
    const tries = j.tries ? ` (retry ${j.tries})` : '';
    return `<span class="pill ${j.status}">${sp}${esc(j.kind)}:${esc(j.asset)} ${esc(j.status)}${tries}${cancel}</span>`;
  }).join(' ');
  strip.innerHTML = `<b>Queue</b> <span class="chip">${active.length} active</span> ${chips || '<span class="faint">idle</span>'} <button class="btn sm ghost" id="qclear">Clear finished</button>`;
  strip.querySelectorAll('[data-cancel]').forEach((a) => a.onclick = (e) => { e.preventDefault(); api.qCancel(a.dataset.cancel).then(() => toast('Cancelled')); });
  const c = strip.querySelector('#qclear'); if (c) { c.onclick = () => api.qClear().then(() => toast('Cleared')); }
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

export function mount() {
  on('resources', renderRes);
  on('queue', renderQueue);
  renderRes(); renderQueue();
}
