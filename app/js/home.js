// home.js - the default center view: the three entry points + the hero grid.
// Owns #main only when state.view === 'home'.
import { api } from './api.js';
import { state, on, toast, refreshState, selectAsset, setView } from './state.js';

const el = () => document.getElementById('main');

function render() {
  if (state.view !== 'home') { return; }
  const m = el(); if (!m) { return; }
  const assets = state.assets || [];
  m.innerHTML = `
    <div class="col" style="gap:18px">
      <div>
        <h1 style="margin:0 0 4px">Make game art by talking, rating, and picking.</h1>
        <div class="faint">Start one of three ways. Drop your style images in the references folder so it learns your look.</div>
      </div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
        <button class="card entry" data-e="new"><div style="font-size:26px">&#127917;</div><b>New Hero</b><div class="faint">You know the style and character.</div></button>
        <button class="card entry" data-e="surprise"><div style="font-size:26px">&#10024;</div><b>Surprise Me</b><div class="faint">A vague idea, many different takes.</div></button>
        <button class="card entry" data-e="find"><div style="font-size:26px">&#128302;</div><b>Find a Style</b><div class="faint">No idea yet. Rate until a look emerges.</div></button>
      </div>
      <div>
        <div class="h">Your heroes</div>
        ${assets.length ? `<div class="grid" id="homegrid"></div>` : `<div class="faint">No heroes yet. Pick an entry point above.</div>`}
      </div>
    </div>`;
  m.querySelectorAll('.entry').forEach((b) => b.onclick = () => entry(b.dataset.e));
  const g = m.querySelector('#homegrid');
  if (g) {
    assets.forEach((a) => {
      const t = document.createElement('button'); t.className = 'tile';
      t.innerHTML = `${a.thumb ? `<img src="${a.thumb}" alt="">` : `<div style="aspect-ratio:1;display:grid;place-items:center" class="faint">${a.candidateCount || 0}</div>`}<div style="padding:6px;font-size:13px">${esc(a.name)}</div>`;
      t.onclick = () => selectAsset(a.name);
      g.appendChild(t);
    });
  }
}

function entry(kind) {
  if (kind === 'surprise') { setView('explore'); return; }
  if (kind === 'find') { setView('taste'); return; }
  // new hero
  const name = prompt('Name this hero (short id, e.g. frost_knight):');
  if (!name) { return; }
  const desc = prompt('Describe it in plain words:') || '';
  api.newHero(name.trim(), desc.trim(), '')
    .then(() => refreshState())
    .then(() => selectAsset(name.trim()))
    .catch((e) => toast('Could not create: ' + e.message, 'bad'));
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

export function mount() {
  on('state', render);
  on('view', (v) => { if (v === 'home') { render(); } });
  render();
}
