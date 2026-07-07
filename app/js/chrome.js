// chrome.js - the app "chrome": Help overlay, Command palette, first-run
// Tutorial coach-marks, and global hotkeys. Owns #help, #palette, #btnHelp,
// #btnPalette, and window keydown. Talks to the rest of the app only through
// emit()/setView()/api - never reaches into other modules.
import { api } from './api.js';
import { state, on, emit, toast, setView } from './state.js';

// ----- plain-language copy for the help overlay + tutorial -------------------
const HELP_COPY = {
  resources: 'How hard your computer is working. If a bar turns red, pause the queue and let it cool down.',
  tree: 'Your styles and categories. Click one to focus everything on it.',
  main: 'The main stage. Start a hero, get surprised, or find a style here.',
  chat: 'Talk to the app in plain words. Ask for changes and it adjusts the next batch.',
  queue: 'Jobs waiting or running. You can pause, cancel, or clear finished ones.',
  settings: 'Change how art is made and switch the theme. Safe to explore.',
  doctor: 'Your setup checklist. Green means ready. It tells you exactly what to fix.',
};

const TUTORIAL_STEPS = [
  { sel: '[data-help="tree"]', title: 'Your styles live here', body: 'On the left are your styles and heroes. Click any of them to focus the whole app on it. Nothing breaks if you poke around.' },
  { sel: '[data-help="main"]', title: 'Start in the middle', body: 'This is where you begin. New Hero if you know what you want, Surprise Me for ideas, or Find a Style to discover a look.' },
  { sel: '[data-help="chat"]', title: 'Just ask', body: 'Type what you want in plain words here. Say "make it more metallic" and the next batch listens.' },
  { sel: '[data-help="queue"]', title: 'Watch it work', body: 'Jobs show up along the bottom. You can pause or cancel any time, so you are always in control.' },
  { sel: '[data-help="settings"]', title: 'Settings and theme', body: 'The gear opens settings. Change quality, switch to a light theme, or restart this tour whenever you like.' },
];

// ============================================================================
// HELP OVERLAY
// ============================================================================
function openHelp() {
  const help = document.getElementById('help');
  if (!help) { return; }
  drawHelp();
  help.classList.add('on');
  window.addEventListener('resize', drawHelp);
}
function closeHelp() {
  const help = document.getElementById('help');
  if (!help) { return; }
  help.classList.remove('on');
  window.removeEventListener('resize', drawHelp);
}
function drawHelp() {
  const help = document.getElementById('help');
  if (!help) { return; }
  const parts = ['<div class="faint" style="position:fixed;top:12px;left:50%;transform:translateX(-50%)">Click anywhere or press Esc to close help</div>'];
  document.querySelectorAll('[data-help]').forEach((n) => {
    const key = n.getAttribute('data-help');
    const copy = HELP_COPY[key];
    if (!copy) { return; }
    const r = n.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) { return; }
    parts.push(`<div class="helpbox" style="left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px"></div>`);
    // place callout inside if the box hugs the right edge, else just right of it
    const spaceRight = window.innerWidth - r.right;
    const cx = spaceRight > 260 ? r.right + 8 : Math.max(8, r.left - 248);
    const cy = Math.max(8, Math.min(r.top, window.innerHeight - 90));
    parts.push(`<div class="helpcallout" style="left:${cx}px;top:${cy}px"><b>${esc(niceName(key))}</b><div>${esc(copy)}</div></div>`);
  });
  help.innerHTML = parts.join('');
  help.onclick = closeHelp;
}
function niceName(key) { return ({ resources: 'Machine', tree: 'Styles', main: 'Main stage', chat: 'Chat', queue: 'Queue', settings: 'Settings', doctor: 'Setup' })[key] || key; }

// ============================================================================
// COMMAND PALETTE
// ============================================================================
function clickSettings() {
  const b = document.getElementById('btnSettings');
  if (b) { b.click(); } else { emit('open', 'settings'); }
}

const ACTIONS = [
  { name: 'New Hero', hint: 'Start a fresh character', run: newHero },
  { name: 'Surprise Me', hint: 'Explore many takes', run: () => setView('explore') },
  { name: 'Find a Style', hint: 'Rate until a look emerges', run: () => setView('taste') },
  { name: 'Generate', hint: 'Make a batch for the current hero', run: doGen },
  { name: 'Post-process', hint: 'Clean up and finish', run: () => emit('open', 'postprocess') },
  { name: 'Model Manager', hint: 'Install or pick models', run: () => emit('open', 'models') },
  { name: 'Web reference', hint: 'Pull reference images from the web', run: () => emit('open', 'webref') },
  { name: 'Settings', hint: 'Quality, theme, and more', run: clickSettings },
  { name: 'Help', hint: 'Show what everything does', run: openHelp },
  { name: 'Tutorial', hint: 'Take the guided tour', run: () => emit('open', 'tutorial') },
];

function newHero() {
  const name = prompt('Name this hero (short id, e.g. frost_knight):');
  if (!name) { return; }
  const desc = prompt('Describe it in plain words:') || '';
  api.newHero(name.trim(), desc.trim(), '')
    .then(() => setView('asset', name.trim()))
    .catch((e) => toast('Could not create: ' + e.message, 'bad'));
}
function doGen() {
  if (!state.current) { toast('Pick a hero first, then Generate.'); return; }
  api.gen(state.current).then(() => toast('Generating')).catch((e) => toast('Could not start: ' + e.message, 'bad'));
}

let palIndex = 0;
let palFiltered = ACTIONS.slice();

function openPalette() {
  const pal = document.getElementById('palette');
  if (!pal) { return; }
  palIndex = 0; palFiltered = ACTIONS.slice();
  pal.innerHTML = `
    <div class="box" style="max-width:560px" onclick="event.stopPropagation()">
      <div class="hd">Command palette <span class="faint" style="margin-left:auto">Esc to close</span></div>
      <div class="bd col" style="gap:10px">
        <input id="palInput" placeholder="Type a command..." autocomplete="off"
          style="width:100%;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;color:inherit">
        <div id="palList" class="col" style="gap:4px"></div>
        <div class="faint" style="border-top:1px solid var(--line);padding-top:8px">
          Shortcuts: <span class="kbd">Ctrl K</span> palette &middot; <span class="kbd">/</span> chat &middot;
          <span class="kbd">N</span> new &middot; <span class="kbd">S</span> surprise &middot;
          <span class="kbd">F</span> find style &middot; <span class="kbd">G</span> generate &middot;
          <span class="kbd">?</span> help &middot; <span class="kbd">Esc</span> close
        </div>
      </div>
    </div>`;
  pal.classList.add('on');
  pal.onclick = closePalette;
  const input = pal.querySelector('#palInput');
  input.oninput = () => { filterPalette(input.value); };
  input.onkeydown = paletteKeys;
  renderPalList();
  input.focus();
}
function closePalette() {
  const pal = document.getElementById('palette');
  if (pal) { pal.classList.remove('on'); pal.innerHTML = ''; }
}
function fuzzy(q, s) {
  q = q.toLowerCase(); s = s.toLowerCase();
  let i = 0;
  for (const ch of s) { if (i < q.length && ch === q[i]) { i++; } }
  return i === q.length;
}
function filterPalette(q) {
  palFiltered = q.trim() ? ACTIONS.filter((a) => fuzzy(q.trim(), a.name + ' ' + a.hint)) : ACTIONS.slice();
  palIndex = 0;
  renderPalList();
}
function renderPalList() {
  const list = document.getElementById('palList');
  if (!list) { return; }
  if (!palFiltered.length) { list.innerHTML = '<div class="faint">No matching commands.</div>'; return; }
  list.innerHTML = palFiltered.map((a, i) => `
    <button class="card" data-i="${i}" style="text-align:left;display:flex;gap:10px;align-items:center;${i === palIndex ? 'border-color:var(--sun)' : ''}">
      <b>${esc(a.name)}</b><span class="faint">${esc(a.hint)}</span>
    </button>`).join('');
  list.querySelectorAll('[data-i]').forEach((b) => {
    b.onmouseenter = () => { palIndex = Number(b.dataset.i); highlight(); };
    b.onclick = () => runPal(Number(b.dataset.i));
  });
}
function highlight() {
  document.querySelectorAll('#palList [data-i]').forEach((b) => {
    b.style.borderColor = Number(b.dataset.i) === palIndex ? 'var(--sun)' : '';
  });
}
function runPal(i) {
  const a = palFiltered[i];
  if (!a) { return; }
  closePalette();
  try { a.run(); } catch (e) { toast('Action failed: ' + e.message, 'bad'); }
}
function paletteKeys(e) {
  if (e.key === 'ArrowDown') { e.preventDefault(); palIndex = Math.min(palFiltered.length - 1, palIndex + 1); highlight(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); palIndex = Math.max(0, palIndex - 1); highlight(); }
  else if (e.key === 'Enter') { e.preventDefault(); runPal(palIndex); }
  else if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
}

// ============================================================================
// TUTORIAL - sequential coach-marks
// ============================================================================
let tutStep = 0;
function startTutorial() { tutStep = 0; drawTutorial(); window.addEventListener('resize', drawTutorial); }
function endTutorial(markDone) {
  const help = document.getElementById('help');
  if (help) { help.classList.remove('on'); help.innerHTML = ''; help.onclick = null; }
  window.removeEventListener('resize', drawTutorial);
  if (markDone) { try { localStorage.setItem('dc_tutorial_done', '1'); } catch (_) { /* ignore */ } }
}
function drawTutorial() {
  const help = document.getElementById('help'); // reuse the dim overlay layer
  if (!help) { return; }
  const step = TUTORIAL_STEPS[tutStep];
  const target = step && document.querySelector(step.sel);
  const parts = [];
  let cx = window.innerWidth / 2 - 140;
  let cy = window.innerHeight / 2 - 60;
  if (target) {
    const r = target.getBoundingClientRect();
    if (r.width || r.height) {
      parts.push(`<div class="helpbox" style="left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px"></div>`);
      const spaceRight = window.innerWidth - r.right;
      cx = spaceRight > 300 ? r.right + 10 : Math.max(10, r.left - 290);
      cy = Math.max(10, Math.min(r.top, window.innerHeight - 180));
    }
  }
  parts.push(`
    <div class="helpcallout" style="left:${cx}px;top:${cy}px;max-width:280px;pointer-events:auto">
      <div class="faint">Step ${tutStep + 1} of ${TUTORIAL_STEPS.length}</div>
      <b>${esc(step ? step.title : 'All set')}</b>
      <div style="margin:6px 0 10px">${esc(step ? step.body : '')}</div>
      <div class="row" style="justify-content:space-between">
        <button class="btn sm ghost" id="tutSkip">Skip</button>
        <div class="row">
          <button class="btn sm" id="tutPrev" ${tutStep === 0 ? 'disabled' : ''}>Back</button>
          <button class="btn sm primary" id="tutNext">${tutStep === TUTORIAL_STEPS.length - 1 ? 'Done' : 'Next'}</button>
        </div>
      </div>
    </div>`);
  help.innerHTML = parts.join('');
  help.classList.add('on');
  help.onclick = null; // tutorial is dismissed only via its buttons
  const q = (id) => help.querySelector(id);
  if (q('#tutSkip')) { q('#tutSkip').onclick = () => endTutorial(true); }
  if (q('#tutPrev')) { q('#tutPrev').onclick = () => { if (tutStep > 0) { tutStep--; drawTutorial(); } }; }
  if (q('#tutNext')) { q('#tutNext').onclick = () => {
    if (tutStep >= TUTORIAL_STEPS.length - 1) { endTutorial(true); }
    else { tutStep++; drawTutorial(); }
  }; }
}

// ============================================================================
// HOTKEYS
// ============================================================================
function topOverlayOpen() {
  return ['palette', 'help', 'settings', 'modal'].map((id) => document.getElementById(id))
    .find((n) => n && n.classList.contains('on'));
}
function closeTopOverlay() {
  const pal = document.getElementById('palette');
  if (pal && pal.classList.contains('on')) { closePalette(); return; }
  const help = document.getElementById('help');
  if (help && help.classList.contains('on')) { endTutorial(false); help.classList.remove('on'); help.innerHTML = ''; return; }
  const settings = document.getElementById('settings');
  if (settings && settings.classList.contains('on')) { settings.classList.remove('on'); return; }
  const modal = document.getElementById('modal');
  if (modal && modal.classList.contains('on')) { modal.classList.remove('on'); }
}
function typingInField(t) {
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
}
function onKey(e) {
  const typing = typingInField(e.target);
  const k = e.key;
  // Esc always works, even in fields
  if (k === 'Escape') { if (topOverlayOpen()) { e.preventDefault(); closeTopOverlay(); } return; }
  // Ctrl/Cmd-K works even while typing
  if ((e.ctrlKey || e.metaKey) && (k === 'k' || k === 'K')) { e.preventDefault(); openPalette(); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) { return; }
  if (k === '/') { e.preventDefault(); focusChat(); }
  else if (k === 'n' || k === 'N') { e.preventDefault(); newHero(); }
  else if (k === 's' || k === 'S') { e.preventDefault(); setView('explore'); }
  else if (k === 'f' || k === 'F') { e.preventDefault(); setView('taste'); }
  else if (k === 'g' || k === 'G') { e.preventDefault(); doGen(); }
  else if (k === '?') { e.preventDefault(); openHelp(); }
}
function focusChat() {
  const panel = document.getElementById('chatpanel');
  const input = panel && (panel.querySelector('textarea') || panel.querySelector('input'));
  if (input) { input.focus(); } else { toast('Chat is not open right now.'); }
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

export function mount() {
  const bh = document.getElementById('btnHelp'); if (bh) { bh.onclick = openHelp; }
  const bp = document.getElementById('btnPalette'); if (bp) { bp.onclick = openPalette; }
  document.addEventListener('keydown', onKey);
  on('open', (what) => { if (what === 'tutorial') { startTutorial(); } });
  // first-run tutorial
  let done = '1';
  try { done = localStorage.getItem('dc_tutorial_done'); } catch (_) { done = '1'; }
  if (!done) { setTimeout(startTutorial, 600); }
}
