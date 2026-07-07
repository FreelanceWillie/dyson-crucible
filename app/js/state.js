// state.js - shared app state, a tiny event bus, and the poll loops.
// Modules read state, subscribe to changes, and never talk to each other directly.

import { api } from './api.js';

const listeners = {};
export function on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); }
export function emit(evt, payload) { (listeners[evt] || []).forEach((f) => { try { f(payload); } catch (e) { console.error(e); } }); }

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

// start the poll loops; pause when the tab is hidden to save the machine
let timers = [];
export function startPolling() {
  const tick = (fn, ms) => {
    const run = () => { if (!document.hidden) { fn(); } };
    run();
    timers.push(setInterval(run, ms));
  };
  tick(refreshQueue, 2000);
  tick(refreshResources, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { refreshQueue(); refreshResources(); } });
}
export function stopPolling() { timers.forEach(clearInterval); timers = []; }

export function selectAsset(name) { state.current = name; state.currentCategory = null; state.view = 'asset'; emit('select', name); emit('view', 'asset'); }
export function selectCategory(path) { state.currentCategory = path; emit('selectCat', path); }
// #main view router: 'home' | 'asset' | 'explore' | 'taste'. Modules that own the
// center pane render when state.view matches theirs.
state.view = 'home';
export function setView(v, data) { state.view = v; emit('view', v, data); }
