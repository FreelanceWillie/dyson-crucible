// state.js - shared app state, a tiny event bus, and the poll loops.
// Modules read state, subscribe to changes, and never talk to each other directly.

import { api } from './api.js';

const listeners = {};
export function on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); }
// Variadic so multi-arg emitters work, e.g. setView('view', v, data) reaches
// on('view', (v, data)). Single-payload listeners simply ignore extra args.
export function emit(evt, ...args) { (listeners[evt] || []).forEach((f) => { try { f(...args); } catch (e) { console.error(e); } }); }

export const state = {
  assets: [],
  tree: [],
  brain: { name: '', ok: false, detail: '' },
  queue: [],
  resources: null,
  current: null,        // selected asset name
  currentCategory: null, // selected category path
  online: true,
  prevJobStatus: {},    // job id -> status, for detecting completions
};

// Shared in-app modal form. Replaces raw browser prompt() dialogs so naming a
// hero / style / category feels like the rest of the app (and drops the technical
// "short id" language). Returns a Promise of the trimmed field values, or null if
// cancelled. fields: [{label, placeholder?, value?, multiline?, required?}].
const _MODAL_IN = 'width:100%;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;color:inherit;font:inherit';
function _esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
export function askModal({ title, fields, submitLabel }) {
  return new Promise((resolve) => {
    const m = document.getElementById('modal');
    if (!m) { resolve(null); return; }
    m.classList.add('open');
    const rows = (fields || []).map((f, i) => `
      <div class="col" style="gap:4px">
        ${f.label ? `<label class="faint" for="am-${i}">${_esc(f.label)}</label>` : ''}
        ${f.multiline
          ? `<textarea id="am-${i}" rows="3" placeholder="${_esc(f.placeholder || '')}" style="${_MODAL_IN};resize:vertical">${_esc(f.value || '')}</textarea>`
          : `<input id="am-${i}" placeholder="${_esc(f.placeholder || '')}" value="${_esc(f.value || '')}" style="${_MODAL_IN}">`}
      </div>`).join('');
    m.innerHTML = `<div class="box"><div class="hd row"><b>${_esc(title || '')}</b><span style="flex:1"></span>`
      + `<button class="btn sm ghost" id="am-x">&#10005;</button></div>`
      + `<div class="bd col" style="gap:12px">${rows}`
      + `<div class="row"><button class="btn primary" id="am-go">${_esc(submitLabel || 'OK')}</button></div></div></div>`;
    const done = (val) => { m.classList.remove('open'); m.innerHTML = ''; document.removeEventListener('keydown', key); resolve(val); };
    const key = (e) => { if (e.key === 'Escape') { done(null); } };
    document.addEventListener('keydown', key);
    m.querySelector('#am-x').onclick = () => done(null);
    const submit = () => {
      const vals = (fields || []).map((f, i) => (document.getElementById('am-' + i).value || '').trim());
      if (fields && fields[0] && fields[0].required && !vals[0]) { document.getElementById('am-0').focus(); return; }
      done(vals);
    };
    m.querySelector('#am-go').onclick = submit;
    (fields || []).forEach((f, i) => {
      const el = document.getElementById('am-' + i);
      if (!el) { return; }
      el.onkeydown = (e) => {
        if (e.key === 'Enter' && !f.multiline) { e.preventDefault(); submit(); }
        else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); }
      };
    });
    const first = document.getElementById('am-0'); if (first) { first.focus(); }
  });
}

// Full-size image preview in the shared #modal. Click the backdrop or Close to
// dismiss. Used by any grid of thumbnails (candidates, Surprise Me tiles, ...).
export function showImage(url, caption) {
  const m = document.getElementById('modal');
  if (!m || !url) { return; }
  m.classList.add('open');
  m.innerHTML = `<div class="box"><div class="hd row"><b>Preview</b><span style="flex:1"></span>`
    + `<button class="btn sm ghost" id="img-x">&#10005;</button></div>`
    + `<div class="bd" style="text-align:center">`
    + `<img src="${_esc(url)}" alt="${_esc(caption || 'preview')}" style="max-width:100%;max-height:72vh;border-radius:8px">`
    + (caption ? `<div class="faint" style="margin-top:8px">${_esc(caption)}</div>` : '')
    + `</div></div>`;
  const close = () => { m.classList.remove('open'); m.innerHTML = ''; document.removeEventListener('keydown', key); };
  const key = (e) => { if (e.key === 'Escape') { close(); } };
  document.addEventListener('keydown', key);
  m.onclick = (e) => { if (e.target === m) { close(); } };
  const x = m.querySelector('#img-x'); if (x) { x.onclick = close; }
}

// toast helper (used everywhere)
export function toast(msg, kind) {
  const wrap = document.getElementById('toasts');
  if (!wrap) { return; }
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || '');
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => { el.classList.add('hide'); setTimeout(() => el.remove(), 300); }, 3200);
}

// pull /api/state (assets + tree + brain), emit 'state'
export async function refreshState() {
  try {
    const [s, cats] = await Promise.all([api.state(), api.categories().catch(() => ({ tree: [] }))]);
    state.assets = s.assets || [];
    state.brain = s.brain || state.brain;
    state.tree = cats.tree || [];
    state.queuePaused = !!s.queue_paused;
    state.online = true;
    emit('state', state);
  } catch (e) {
    state.online = false;
    emit('offline', e);
  }
}

// queue poll: emits 'queue', and 'jobdone' when any job flips to done
export async function refreshQueue() {
  try {
    const q = (await api.queue()).queue || [];
    q.forEach((j) => {
      const prev = state.prevJobStatus[j.id];
      if (prev && prev !== 'done' && j.status === 'done') { emit('jobdone', j); }
      state.prevJobStatus[j.id] = j.status;
    });
    state.queue = q;
    emit('queue', q);
  } catch (_) { /* keep last */ }
}

export async function refreshResources() {
  try { state.resources = await api.resources(); emit('resources', state.resources); }
  catch (_) { /* keep last */ }
}

// start the poll loops. ADAPTIVE + hidden-tab-paused so we barely touch the CPU
// when nothing is happening (the machine's compute should go to generation):
//  - poll fast (2s) only while a job is active; slow (6s) when idle.
//  - skip entirely while the tab is hidden.
let timers = [];
export function startPolling() {
  const busy = () => (state.queue || []).some((j) => j.status === 'running' || j.status === 'queued');
  const loop = (fn) => {
    const run = async () => {
      if (!document.hidden) { try { await fn(); } catch (_) {} }
      timers.push(setTimeout(run, busy() ? 2000 : 6000));
    };
    run();
  };
  loop(refreshQueue);
  loop(refreshResources);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { refreshQueue(); refreshResources(); } });
}
export function stopPolling() { timers.forEach(clearInterval); timers = []; }

export function selectAsset(name) { state.current = name; state.currentCategory = null; state.view = 'asset'; emit('select', name); emit('view', 'asset'); }
export function selectCategory(path) { state.currentCategory = path; emit('selectCat', path); }
// #main view router: 'home' | 'asset' | 'explore' | 'taste'. Modules that own the
// center pane render when state.view matches theirs.
state.view = 'home';
export function setView(v, data) { state.view = v; emit('view', v, data); }
