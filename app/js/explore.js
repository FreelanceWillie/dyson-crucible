// explore.js - Surprise Me / the mood board. Owns #main only when
// state.view === 'explore'. Give it a vague style phrase, it fans out into many
// different takes; like the ones you want, then combine them into one direction
// you can start a hero from.
import { api } from './api.js';
import { state, on, toast, refreshState, selectAsset, setView } from './state.js';

const ASSET = 'explore'; // the moodboard slot this view owns
const POLL_MS = 2000;

// per-mount view model, rebuilt each time we enter the view
let vm = null;
let pollTimer = null;

const el = () => document.getElementById('main');

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function stopPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function freshVm() {
  return {
    phrase: vm ? vm.phrase : '',
    n: vm ? vm.n : 10,
    category: vm ? vm.category : '',
    status: 'idle',   // idle | requesting | pending | done | failed | error
    error: '',
    takes: [],        // { label, prompt, url }
    picks: {},        // index -> { note }
    combined: null,   // { label, prompt } from synthesize
    combining: false,
  };
}

// flatten state.tree (nested {path, children}) into a list of category paths
function categoryOptions() {
  const out = [];
  const walk = (nodes) => {
    (nodes || []).forEach((node) => {
      if (!node) { return; }
      const path = node.path || node.name || '';
      if (path) { out.push(path); }
      walk(node.children);
    });
  };
  walk(state.tree);
  return out;
}

// ---- rendering ------------------------------------------------------------

function render() {
  if (state.view !== 'explore') { return; }
  const m = el(); if (!m) { return; }
  if (!vm) { vm = freshVm(); }

  const cats = categoryOptions();
  const catOpts = ['<option value="">(no category, just explore)</option>']
    .concat(cats.map((c) => `<option value="${esc(c)}"${c === vm.category ? ' selected' : ''}>${esc(c)}</option>`))
    .join('');

  m.innerHTML = `
    <div class="col" style="gap:16px">
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div>
          <h1 style="margin:0 0 4px">&#10024; Surprise Me</h1>
          <div class="faint">A vague style phrase, many different takes. Like the ones that click, then combine them.</div>
        </div>
        <button class="btn ghost sm" id="ex-back">&#8592; Back</button>
      </div>

      <div class="card col" style="gap:10px">
        <div class="row" style="gap:10px;flex-wrap:wrap;align-items:flex-end">
          <label class="col" style="gap:4px;flex:1;min-width:240px">
            <span class="faint">Style phrase</span>
            <input id="ex-phrase" type="text" placeholder="Dark Steampunk Heavy Vampire Diamond" value="${esc(vm.phrase)}">
          </label>
          <label class="col" style="gap:4px">
            <span class="faint">How many</span>
            <input id="ex-n" type="number" min="4" max="20" step="1" value="${vm.n}" style="width:80px">
          </label>
          <label class="col" style="gap:4px">
            <span class="faint">Into category</span>
            <select id="ex-cat">${catOpts}</select>
          </label>
          <button class="btn" id="ex-go">${vm.status === 'requesting' || vm.status === 'pending' ? '<span class="spinner"></span> Working' : '&#10024; Surprise Me'}</button>
        </div>
      </div>

      <div id="ex-board"></div>
      <div id="ex-combined"></div>
    </div>`;

  m.querySelector('#ex-back').onclick = () => { stopPoll(); setView('home'); };
  const phraseInput = m.querySelector('#ex-phrase');
  phraseInput.oninput = (e) => { vm.phrase = e.target.value; };
  m.querySelector('#ex-n').oninput = (e) => {
    let v = parseInt(e.target.value, 10);
    if (isNaN(v)) { v = 10; }
    vm.n = Math.max(4, Math.min(20, v));
  };
  m.querySelector('#ex-cat').onchange = (e) => { vm.category = e.target.value; };
  const go = m.querySelector('#ex-go');
  go.disabled = vm.status === 'requesting' || vm.status === 'pending';
  go.onclick = () => startExplore();

  renderBoard();
  renderCombined();
}

function renderBoard() {
  const wrap = document.getElementById('ex-board');
  if (!wrap) { return; }

  if (vm.status === 'idle') {
    wrap.innerHTML = '<div class="faint">Type a phrase and hit Surprise Me. You will get one tile per direction.</div>';
    return;
  }
  if (vm.status === 'error') {
    wrap.innerHTML = `<div class="card"><b class="bad">Could not start.</b><div class="faint">${esc(vm.error || 'Something went wrong.')}</div></div>`;
    return;
  }

  const takes = vm.takes || [];
  const rendered = takes.filter((t) => t && t.url).length;
  const total = takes.length || vm.n;

  let head = '';
  if (vm.status === 'requesting') {
    head = '<div class="row" style="gap:8px;align-items:center"><span class="spinner"></span> <span class="faint">Asking the brain for directions...</span></div>';
  } else if (vm.status === 'pending') {
    const wait = state.queuePaused
      ? ' <span class="warn">The queue is paused. Click Resume up top to start it.</span>'
      : ' <span class="faint">Waiting on ComfyUI. First run warms up slowly. If nothing appears, check the health panel up top.</span>';
    head = `<div class="row" style="gap:8px;align-items:center"><span class="spinner"></span> <span class="chip">${rendered} of ${total} rendered</span>`
      + (rendered === 0 ? wait : '')
      + '</div>';
  } else if (vm.status === 'done') {
    head = `<div class="row" style="gap:8px;align-items:center"><span class="chip">${rendered} of ${total} rendered</span> <span class="faint">Done. Like the ones you want, then combine.</span></div>`;
  } else if (vm.status === 'failed') {
    const why = state.queuePaused
      ? 'The queue is paused. Click Resume in the top bar, then try again.'
      : 'ComfyUI may be down, check the health panel up top, then try again.';
    head = `<div class="card"><b class="warn">The board stalled.</b><div class="faint">${rendered} of ${total} rendered. ${why}</div></div>`;
  }

  if (!takes.length && (vm.status === 'done' || vm.status === 'failed')) {
    wrap.innerHTML = head + '<div class="faint">No directions came back. Try a richer phrase.</div>';
    return;
  }

  wrap.innerHTML = head + '<div class="grid" id="ex-grid" style="grid-template-columns:repeat(auto-fill,minmax(200px,1fr));margin-top:10px"></div>';
  const grid = wrap.querySelector('#ex-grid');

  takes.forEach((t, i) => {
    const picked = !!vm.picks[i];
    const cell = document.createElement('div');
    cell.className = 'tile col' + (picked ? ' picked' : '');
    cell.style.gap = '6px';
    const media = t && t.url
      ? `<img src="${esc(t.url)}" alt="${esc(t.label)}" style="width:100%;aspect-ratio:1;object-fit:cover">`
      : `<div style="aspect-ratio:1;display:grid;place-items:center"><span class="spinner"></span></div>`;
    cell.innerHTML = `
      ${media}
      <div class="col" style="gap:6px;padding:6px">
        <div class="row" style="justify-content:space-between;align-items:center;gap:6px">
          <b style="font-size:13px">${esc(t && t.label ? t.label : 'Direction ' + (i + 1))}</b>
          <button class="btn sm ${picked ? '' : 'ghost'}" data-pick="${i}">${picked ? '&#10003; Liked' : 'Like'}</button>
        </div>
        <input type="text" class="note" data-note="${i}" placeholder="what you like (optional)" value="${esc(vm.picks[i] ? vm.picks[i].note : '')}" style="font-size:12px"${picked ? '' : ' disabled'}>
      </div>`;
    grid.appendChild(cell);
  });

  grid.querySelectorAll('[data-pick]').forEach((b) => b.onclick = () => togglePick(parseInt(b.dataset.pick, 10)));
  grid.querySelectorAll('[data-note]').forEach((inp) => inp.oninput = (e) => {
    const i = parseInt(inp.dataset.note, 10);
    if (vm.picks[i]) { vm.picks[i].note = e.target.value; }
  });
}

function renderCombined() {
  const wrap = document.getElementById('ex-combined');
  if (!wrap) { return; }
  const pickCount = Object.keys(vm.picks).length;

  let bar = '';
  if (pickCount >= 1 || vm.combining) {
    bar = `<div class="card row" style="position:sticky;bottom:8px;justify-content:space-between;align-items:center;gap:10px">
      <span class="chip">${pickCount} liked</span>
      <button class="btn" id="ex-combine"${pickCount < 1 || vm.combining ? ' disabled' : ''}>${vm.combining ? '<span class="spinner"></span> Combining' : 'Combine picks'}</button>
    </div>`;
  }

  let result = '';
  if (vm.combined) {
    const c = vm.combined;
    result = `<div class="card col" style="gap:10px">
      <div class="h">Your combined direction</div>
      <b>${esc(c.label || 'Combined direction')}</b>
      <div class="faint" style="white-space:pre-wrap">${esc(c.prompt || '')}</div>
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <button class="btn" id="ex-start">Start a hero from this</button>
        ${vm.category ? '<button class="btn ghost" id="ex-savecat">Save as this category\'s style</button>' : ''}
        <button class="btn ghost" id="ex-further">Explore further</button>
      </div>
    </div>`;
  }

  wrap.innerHTML = bar + result;

  const cb = wrap.querySelector('#ex-combine'); if (cb) { cb.onclick = () => combinePicks(); }
  const st = wrap.querySelector('#ex-start'); if (st) { st.onclick = () => startHero(); }
  const sc = wrap.querySelector('#ex-savecat'); if (sc) { sc.onclick = () => saveAsCategory(); }
  const ef = wrap.querySelector('#ex-further'); if (ef) { ef.onclick = () => exploreFurther(); }
}

// ---- actions --------------------------------------------------------------

function togglePick(i) {
  if (vm.picks[i]) { delete vm.picks[i]; }
  else { vm.picks[i] = { note: '' }; }
  renderBoard();
  renderCombined();
}

async function startExplore() {
  if (!vm.phrase.trim()) { toast('Type a style phrase first.', 'warn'); return; }
  stopPoll();
  vm.status = 'requesting';
  vm.error = '';
  vm.takes = [];
  vm.picks = {};
  vm.combined = null;
  render();
  try {
    await api.explore(vm.phrase.trim(), vm.n, vm.category || '', ASSET);
    vm.status = 'pending';
    renderBoard();
    startPoll();
  } catch (e) {
    vm.status = 'error';
    vm.error = e && e.message === 'offline' ? 'The server is offline.' : (e ? e.message : 'unknown error');
    render();
  }
}

function startPoll() {
  stopPoll();
  const tick = async () => {
    if (state.view !== 'explore') { stopPoll(); return; }
    let board;
    try { board = await api.moodboard(ASSET); }
    catch (_) { return; } // transient, keep polling
    applyBoard(board);
    const s = board && board.status;
    if (s === 'done' || s === 'failed' || s === 'none') { stopPoll(); }
  };
  tick();
  pollTimer = setInterval(tick, POLL_MS);
}

// merge server moodboard into vm.takes without dropping picks/notes
function applyBoard(board) {
  if (!board) { return; }
  const takes = board.takes || board.directions || [];
  vm.takes = takes.map((t, i) => ({
    label: t.label || t.name || ('Direction ' + (i + 1)),
    prompt: t.prompt || '',
    url: t.url || t.thumb || '',
  }));
  const s = board.status;
  if (s === 'done') { vm.status = 'done'; }
  else if (s === 'failed') { vm.status = 'failed'; }
  else if (s === 'none') { vm.status = vm.takes.length ? 'done' : 'failed'; }
  else { vm.status = 'pending'; }
  renderBoard();
  renderCombined();
}

async function combinePicks() {
  const picks = Object.keys(vm.picks).map((k) => {
    const i = parseInt(k, 10);
    const t = vm.takes[i] || {};
    return { label: t.label || ('Direction ' + (i + 1)), prompt: t.prompt || '', note: vm.picks[i].note || '' };
  });
  if (!picks.length) { return; }
  vm.combining = true;
  renderCombined();
  try {
    const res = await api.synthesize(vm.phrase.trim(), picks, vm.category || '');
    const dir = res && (res.direction || res);
    vm.combined = { label: (dir && dir.label) || 'Combined direction', prompt: (dir && dir.prompt) || '' };
    if (vm.category) { toast('Saved as the style for ' + vm.category + '.'); }
  } catch (e) {
    toast('Could not combine: ' + (e && e.message === 'offline' ? 'server offline' : (e ? e.message : 'error')), 'bad');
  } finally {
    vm.combining = false;
    renderCombined();
  }
}

function startHero() {
  if (!vm.combined) { return; }
  const name = prompt('Name this hero (short id, e.g. frost_knight):');
  if (!name || !name.trim()) { return; }
  const id = name.trim();
  stopPoll();
  api.newHero(id, vm.combined.prompt || '', vm.category || '')
    .then(() => refreshState())
    .then(() => selectAsset(id))
    .catch((e) => toast('Could not create: ' + (e ? e.message : 'error'), 'bad'));
}

function saveAsCategory() {
  // synthesize already persisted the style server-side when a category was passed.
  if (!vm.category) { return; }
  toast('Style saved for ' + vm.category + '.');
}

function exploreFurther() {
  if (!vm.combined) { return; }
  vm.phrase = vm.combined.prompt || vm.phrase;
  vm.combined = null;
  render();
  startExplore();
}

// ---- lifecycle ------------------------------------------------------------

export function mount() {
  on('view', (v) => {
    if (v === 'explore') {
      if (!vm) { vm = freshVm(); }
      render();
    } else {
      stopPoll();
    }
  });
  if (state.view === 'explore') { vm = freshVm(); render(); }
}
