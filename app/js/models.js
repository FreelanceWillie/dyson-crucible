// models.js - Model Manager panel. Search LoRA / ControlNet models, download the
// primary file into ComfyUI, and list what is installed. Opened via emit('open','models').
import { api } from './api.js';
import { state, on, emit, toast, refreshState } from './state.js';

const MINE = 'models';
let kind = 'lora';   // 'lora' | 'controlnet'
let results = [];
let busy = false;

function modalEl() { return document.getElementById('modal'); }

function open() {
  const m = modalEl(); if (!m) { return; }
  render();
  const esc = (e) => { if (e.key === 'Escape') { close(); } };
  document.addEventListener('keydown', esc);
  m._escHandler = esc;
}

function close() {
  const m = modalEl(); if (!m) { return; }
  if (m._escHandler) { document.removeEventListener('keydown', m._escHandler); m._escHandler = null; }
  m.innerHTML = '';
  m.classList.remove('open');
}

function render() {
  const m = modalEl(); if (!m) { return; }
  m.classList.add('open');
  m.innerHTML = `
    <div class="box">
      <div class="hd row">
        <b>Model Manager</b>
        <span class="chip">${kind === 'lora' ? 'LoRA' : 'ControlNet'}</span>
        <span style="flex:1"></span>
        <button class="btn sm ghost" id="mm-close">&#10005;</button>
      </div>
      <div class="bd col" style="gap:14px">
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <input id="mm-q" class="in" placeholder="Search models" style="flex:1;min-width:180px" value="">
          <button class="btn sm ${kind === 'lora' ? '' : 'ghost'}" data-kind="lora">LoRA</button>
          <button class="btn sm ${kind === 'controlnet' ? '' : 'ghost'}" data-kind="controlnet">ControlNet</button>
          <button class="btn sm" id="mm-search">Search</button>
        </div>
        <div class="faint">Downloads go into ComfyUI's models folder. A ControlNet also needs a matching workflow before it can actually be used. You approve each download.</div>
        <div id="mm-results"></div>
        <div class="h">Installed</div>
        <div id="mm-installed"><span class="spinner"></span></div>
      </div>
    </div>`;

  m.querySelector('#mm-close').onclick = close;
  m.querySelectorAll('[data-kind]').forEach((b) => b.onclick = () => { kind = b.dataset.kind; results = []; render(); });
  const q = m.querySelector('#mm-q');
  const doSearch = () => search(q.value.trim());
  m.querySelector('#mm-search').onclick = doSearch;
  q.onkeydown = (e) => { if (e.key === 'Enter') { doSearch(); } };

  renderResults();
  loadInstalled();
}

function renderResults() {
  const box = document.getElementById('mm-results'); if (!box) { return; }
  if (busy) { box.innerHTML = '<div class="row"><span class="spinner"></span> <span class="faint">Searching...</span></div>'; return; }
  if (!results.length) { box.innerHTML = '<div class="faint">No results yet. Type a query and search.</div>'; return; }
  box.innerHTML = '<div class="grid" id="mm-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))"></div>';
  const g = box.querySelector('#mm-grid');
  results.forEach((r) => {
    const file = (r.files && r.files[0]) || null;
    const sz = file && file.sizeKB ? ` (${(file.sizeKB / 1024).toFixed(1)} MB)` : '';
    const card = document.createElement('div'); card.className = 'card col'; card.style.gap = '6px';
    card.innerHTML = `
      ${r.thumb ? `<img src="${esc(r.thumb)}" alt="" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px">` : `<div class="tile" style="aspect-ratio:1;display:grid;place-items:center" class="faint">${esc(r.kind || kind)}</div>`}
      <b style="font-size:14px">${esc(r.name)}</b>
      <div class="faint" style="font-size:12px">${esc(r.creator || '')}</div>
      ${file ? `<button class="btn sm" data-dl="1">Download${sz}</button>` : '<span class="faint">No file</span>'}`;
    if (file) {
      card.querySelector('[data-dl]').onclick = (e) => download(e.currentTarget, file);
    }
    g.appendChild(card);
  });
}

function loadInstalled() {
  const box = document.getElementById('mm-installed'); if (!box) { return; }
  api.modelsInstalled()
    .then((d) => {
      const loras = (d && d.loras) || [];
      const cn = (d && d.controlnets) || [];
      const chips = (arr) => arr.length ? arr.map((x) => `<span class="chip">${esc(x)}</span>`).join(' ') : '<span class="faint">none</span>';
      box.innerHTML = `
        <div class="col" style="gap:8px">
          <div><b>LoRA</b><div class="row" style="gap:6px;flex-wrap:wrap;margin-top:4px">${chips(loras)}</div></div>
          <div><b>ControlNet</b><div class="row" style="gap:6px;flex-wrap:wrap;margin-top:4px">${chips(cn)}</div></div>
        </div>`;
    })
    .catch((e) => { box.innerHTML = `<span class="faint">Could not read installed models: ${esc(e.message)}</span>`; });
}

function search(q) {
  if (!q) { toast('Type something to search', 'warn'); return; }
  busy = true; renderResults();
  api.modelsSearch(q, kind)
    .then((d) => { results = (d && d.results) || (Array.isArray(d) ? d : []); busy = false; renderResults(); })
    .catch((e) => { busy = false; results = []; renderResults(); toast('Search failed: ' + e.message, 'bad'); });
}

function download(btn, file) {
  btn.disabled = true;
  const label = btn.textContent;
  btn.innerHTML = '<span class="spinner"></span> Downloading...';
  toast('Download started. Large files can take a while.', '');
  api.modelDownload(file.downloadUrl, kind, file.name)
    .then(() => { toast('Downloaded ' + file.name, 'good'); loadInstalled(); btn.innerHTML = 'Done'; })
    .catch((e) => { toast('Download failed: ' + e.message, 'bad'); btn.disabled = false; btn.textContent = label; });
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

export function mount() {
  on('open', (n) => { if (n === MINE) { open(); } });
}
