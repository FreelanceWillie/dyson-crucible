// taste.js - "Find a Style": taste discovery for someone who cannot articulate
// what he wants. Rate rounds of images 1-5 stars; the loved pool (>=4) steers the
// next round; save the emerged look as a Style. Owns #main only when view==='taste'.
import { api } from './api.js';
import { state, on, toast, refreshState, setView, askModal } from './state.js';

const el = () => document.getElementById('main');

// session-local view state (not shared app state)
let session = null;      // { id, phrase, round, loved, rounds }
let starting = false;
let errMsg = '';
let poll = null;         // interval id for the current round's render poll
let focused = -1;        // index of hovered/focused tile, for keyboard 1-5

function stopPoll() { if (poll) { clearInterval(poll); poll = null; } }

function isTaste() { return state.view === 'taste'; }

function curRound() {
  if (!session || !session.rounds || !session.rounds.length) { return null; }
  return session.rounds[session.rounds.length - 1];
}

function lovedCount() {
  const r = curRound();
  if (!r || !r.images) { return 0; }
  return r.images.filter((im) => (im.stars || 0) >= 4).length;
}

// --- server interaction -----------------------------------------------------

function begin() {
  if (starting) { return; }
  const m = el(); if (!m) { return; }
  const phrase = (m.querySelector('#tphrase') || {}).value || '';
  let n = parseInt((m.querySelector('#tcount') || {}).value, 10);
  if (!(n > 0)) { n = 8; }
  starting = true; errMsg = ''; render();
  api.tasteStart(phrase.trim(), n)
    .then((res) => {
      session = res.session || null;
      starting = false;
      render();
      startPolling();
    })
    .catch((e) => { starting = false; errMsg = friendly(e); render(); });
}

function nextRound() {
  if (!session) { return; }
  stopPoll();
  errMsg = ''; render();
  api.tasteNext(session.id)
    .then((res) => { session = res.session || session; focused = -1; render(); startPolling(); })
    .catch((e) => { errMsg = friendly(e); render(); startPolling(); });
}

// poll the round until every tile has rendered a url
function startPolling() {
  stopPoll();
  const tick = () => {
    if (!isTaste() || !session) { stopPoll(); return; }
    api.taste(session.id)
      .then((res) => {
        if (res && res.session) { session = res.session; }
        render();
        const r = curRound();
        const done = r && r.images && r.images.length && r.images.every((im) => im.url);
        if (done) { stopPoll(); }
      })
      .catch(() => { /* keep last; transient */ });
  };
  tick();
  poll = setInterval(tick, 2000);
}

function rate(im, stars) {
  if (!session || !im) { return; }
  im.stars = stars; // optimistic
  render();
  api.tasteRate(session.id, im.path, stars).catch((e) => toast('Rate failed: ' + friendly(e), 'bad'));
}

async function save() {
  if (!session) { return; }
  const vals = await askModal({
    title: 'Save this style', submitLabel: 'Save style',
    fields: [{ label: 'Name this style', placeholder: 'e.g. Brass Gloom, Cozy Pixel', required: true }],
  });
  if (!vals || !vals[0]) { return; }
  api.tasteSave(session.id, vals[0])
    .then(() => { toast('Saved as a Style'); return refreshState(); })
    .catch((e) => toast('Could not save: ' + friendly(e), 'bad'));
}

function reset() {
  stopPoll();
  session = null; starting = false; errMsg = ''; focused = -1;
  render();
}

function back() {
  reset();
  setView('home');
}

// --- rendering --------------------------------------------------------------

function friendly(e) {
  const msg = (e && e.message) ? e.message : String(e || 'error');
  if (msg === 'offline') { return 'The server is not responding.'; }
  return msg;
}

function comfyHint() {
  const b = state.brain || {};
  const bad = b.ok === false || /comfy|offline|not responding/i.test(errMsg);
  if (!bad) { return ''; }
  return `<div class="card" style="border-color:var(--line)">
    <div class="faint">The image engine looks offline. Start ComfyUI, then try Start again. ${esc(b.detail || '')}</div>
  </div>`;
}

function starcontrol(idx, im) {
  const cur = im.stars || 0;
  let s = '';
  for (let i = 1; i <= 5; i++) {
    s += `<span class="s ${i <= cur ? 'on' : ''}" data-star="${i}" data-tile="${idx}" title="${i} star${i > 1 ? 's' : ''}">&#9733;</span>`;
  }
  return `<span class="stars" role="radiogroup" aria-label="rate">${s}</span>`;
}

function tileHtml(idx, im) {
  const inner = im.url
    ? `<img src="${esc(im.url)}" alt="">`
    : `<div style="aspect-ratio:1;display:grid;place-items:center" class="faint"><span class="spinner"></span></div>`;
  return `<div class="tile" data-tile="${idx}" tabindex="0">
    ${inner}
    <div class="row" style="justify-content:center;padding:6px">${starcontrol(idx, im)}</div>
  </div>`;
}

function render() {
  if (!isTaste()) { stopPoll(); return; }
  const m = el(); if (!m) { return; }

  // Intake screen (no session yet)
  if (!session) {
    m.innerHTML = `
      <div class="col" style="gap:16px;max-width:640px">
        <div class="row" style="justify-content:space-between;align-items:center">
          <h1 style="margin:0">Find a Style</h1>
          <button class="btn ghost sm" id="tback">&#8592; Back</button>
        </div>
        <div class="faint">Rate what you like. It learns and steers toward your taste. Save the look as a Style when it clicks.</div>
        ${errMsg ? `<div class="card" style="border-color:var(--bad)"><div class="faint">${esc(errMsg)}</div></div>` : ''}
        ${comfyHint()}
        <div class="col" style="gap:10px">
          <input id="tphrase" placeholder="leave blank for total surprise" ${starting ? 'disabled' : ''}>
          <div class="row" style="gap:10px;align-items:center">
            <label class="faint">How many</label>
            <input id="tcount" type="number" min="2" max="24" value="8" style="width:80px" ${starting ? 'disabled' : ''}>
            <button class="btn" id="tstart" ${starting ? 'disabled' : ''}>${starting ? '<span class="spinner"></span> Starting' : 'Start'}</button>
          </div>
        </div>
      </div>`;
    const s = m.querySelector('#tstart'); if (s) { s.onclick = begin; }
    const b = m.querySelector('#tback'); if (b) { b.onclick = back; }
    const p = m.querySelector('#tphrase');
    if (p && !starting) { p.onkeydown = (e) => { if (e.key === 'Enter') { begin(); } }; }
    return;
  }

  // Round screen
  const r = curRound();
  const roundNo = (session.round != null ? session.round : (session.rounds ? session.rounds.length : 1));
  const imgs = (r && r.images) ? r.images : [];
  const loved = lovedCount();
  const rendering = imgs.some((im) => !im.url);

  m.innerHTML = `
    <div class="col" style="gap:14px">
      <div class="row" style="justify-content:space-between;align-items:center">
        <div class="col" style="gap:2px">
          <h1 style="margin:0">Find a Style</h1>
          <div class="faint">Round ${esc(roundNo)} &middot; learning your taste${session.phrase ? ' &middot; "' + esc(session.phrase) + '"' : ''}</div>
        </div>
        <div class="row" style="gap:8px">
          <span class="chip">${loved} loved</span>
          <button class="btn ghost sm" id="tback">&#8592; Back</button>
        </div>
      </div>
      <div class="faint">Click stars, or hover a tile and press 1 to 5. Four stars or more feeds the next round.</div>
      ${errMsg ? `<div class="card" style="border-color:var(--bad)"><div class="faint">${esc(errMsg)}</div></div>` : ''}
      ${comfyHint()}
      ${imgs.length
        ? `<div class="grid" id="tgrid" style="grid-template-columns:repeat(auto-fill,minmax(180px,1fr))">
             ${imgs.map((im, i) => tileHtml(i, im)).join('')}
           </div>`
        : `<div class="row" style="gap:8px;align-items:center" class="faint"><span class="spinner"></span> <span class="faint">Painting the first round...</span></div>`}
      <div class="row" style="gap:10px">
        <button class="btn" id="tnext">Next round</button>
        <button class="btn ghost" id="tsave" ${loved ? '' : 'disabled'} title="${loved ? 'Save the loved images as a Style' : 'Love a few first (4+ stars)'}">Save as a Style</button>
        ${rendering ? '<span class="row" style="gap:6px;align-items:center"><span class="spinner"></span><span class="faint">rendering</span></span>' : ''}
      </div>
    </div>`;

  // wire tiles
  const grid = m.querySelector('#tgrid');
  if (grid) {
    grid.querySelectorAll('.s').forEach((sp) => {
      sp.onclick = () => {
        const ti = parseInt(sp.dataset.tile, 10);
        const st = parseInt(sp.dataset.star, 10);
        if (imgs[ti]) { rate(imgs[ti], st); }
      };
    });
    grid.querySelectorAll('.tile').forEach((t) => {
      const ti = parseInt(t.dataset.tile, 10);
      t.onmouseenter = () => { focused = ti; };
      t.onmouseleave = () => { if (focused === ti) { focused = -1; } };
      t.onfocus = () => { focused = ti; };
    });
  }
  const nx = m.querySelector('#tnext'); if (nx) { nx.onclick = nextRound; }
  const sv = m.querySelector('#tsave'); if (sv) { sv.onclick = save; }
  const bk = m.querySelector('#tback'); if (bk) { bk.onclick = back; }
}

// global 1-5 keyboard rating for the hovered/focused tile
function onKey(e) {
  if (!isTaste() || !session) { return; }
  if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) { return; }
  const n = parseInt(e.key, 10);
  if (!(n >= 1 && n <= 5)) { return; }
  const r = curRound();
  if (focused < 0 || !r || !r.images || !r.images[focused]) { return; }
  e.preventDefault();
  rate(r.images[focused], n);
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

// --- mount ------------------------------------------------------------------

export function mount() {
  on('view', (v) => {
    if (v === 'taste') { render(); }
    else { stopPoll(); }
  });
  on('state', () => { if (isTaste()) { render(); } });
  document.addEventListener('keydown', onKey);
  render();
}
