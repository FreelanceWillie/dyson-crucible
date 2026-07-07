// webref.js - Web reference fetch panel. Pulls reference images from the web into
// a category's references folder or the current asset. Private-use only.
// Opened via emit('open','webref').
import { api } from './api.js';
import { state, on, emit, toast, refreshState } from './state.js';

const MINE = 'webref';
let busy = false;
let last = null;   // { thumbs:[], note:'', into:'' }

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

// flatten the category tree into path options
function catOptions() {
  const out = [];
  const walk = (nodes) => {
    (nodes || []).forEach((n) => {
      if (n && n.path) { out.push(n.path); }
      if (n && n.children) { walk(n.children); }
    });
  };
  walk(state.tree || []);
  return out;
}

function render() {
  const m = modalEl(); if (!m) { return; }
  m.classList.add('open');
  const cats = catOptions();
  const hasAsset = !!state.current;
  const assetOpt = hasAsset ? `<option value="asset">Current asset: ${esc(state.current)}</option>` : '';
  const catOpts = cats.map((p) => `<option value="cat:${esc(p)}">Category: ${esc(p)}</option>`).join('');

  m.innerHTML = `
    <div class="box">
      <div class="hd row">
        <b>Web reference fetch</b>
        <span style="flex:1"></span>
        <button class="btn sm ghost" id="wr-close">&#10005;</button>
      </div>
      <div class="bd col" style="gap:14px">
        <div class="card" style="border-left:3px solid #c60"><b>For private reference only.</b> <span class="faint">Do not ship copyrighted or trademarked images in your game.</span></div>
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <input id="wr-q" class="in" placeholder="the main character from Thundercats" style="flex:1;min-width:220px">
          <input id="wr-n" class="in" type="number" min="1" max="40" value="8" style="width:70px" title="How many">
        </div>
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <label class="faint">Save into</label>
          <select id="wr-into" class="in" style="flex:1;min-width:200px">
            ${assetOpt}
            ${catOpts}
            ${(!hasAsset && !cats.length) ? '<option value="">No destinations available</option>' : ''}
          </select>
          <button class="btn sm" id="wr-go">Fetch</button>
        </div>
        <div id="wr-out"></div>
      </div>
    </div>`;

  m.querySelector('#wr-close').onclick = close;
  const q = m.querySelector('#wr-q');
  const go = () => fetchRefs();
  m.querySelector('#wr-go').onclick = go;
  q.onkeydown = (e) => { if (e.key === 'Enter') { go(); } };

  renderOut();
}

function selectedInto() {
  const m = modalEl(); if (!m) { return null; }
  const sel = m.querySelector('#wr-into');
  return sel ? sel.value : null;
}

// map the select value to the api 'into' argument
function resolveInto(val) {
  if (val === 'asset') { return { into: state.current, isAsset: true, category: null }; }
  if (val && val.indexOf('cat:') === 0) { const p = val.slice(4); return { into: p, isAsset: false, category: p }; }
  return null;
}

function fetchRefs() {
  const m = modalEl(); if (!m) { return; }
  const q = m.querySelector('#wr-q').value.trim();
  let n = parseInt(m.querySelector('#wr-n').value, 10);
  if (!n || n < 1) { n = 8; }
  const val = selectedInto();
  const dest = resolveInto(val);
  if (!q) { toast('Type a query', 'warn'); return; }
  if (!dest) { toast('Pick a destination', 'warn'); return; }

  busy = true; last = null; renderOut();
  api.webref(q, n, dest.into)
    .then((d) => {
      busy = false;
      const thumbs = (d && d.thumbs) || (d && d.urls) || [];
      last = { thumbs, note: (d && d.note) || '', dest };
      renderOut();
      if (!dest.isAsset) {
        toast('Saved ' + thumbs.length + ' references to ' + dest.category, 'good');
        refreshState();
      } else {
        toast('Fetched ' + thumbs.length + ' images for ' + dest.into, 'good');
      }
    })
    .catch((e) => { busy = false; last = null; renderOut(); toast('Fetch failed: ' + e.message, 'bad'); });
}

function renderOut() {
  const box = document.getElementById('wr-out'); if (!box) { return; }
  if (busy) { box.innerHTML = '<div class="row"><span class="spinner"></span> <span class="faint">Fetching references...</span></div>'; return; }
  if (!last) { box.innerHTML = '<div class="faint">Results will appear here.</div>'; return; }
  if (!last.thumbs.length) { box.innerHTML = '<div class="faint">No images found for that query.</div>'; return; }

  const grid = last.thumbs.map((u) => `<div class="tile"><img src="${esc(u)}" alt="" style="width:100%;aspect-ratio:1;object-fit:cover"></div>`).join('');
  const noteHtml = last.note ? `<div class="faint" style="margin-top:8px">${esc(last.note)}</div>` : '';

  // if destination is the current asset, offer ref tagging
  let tagHtml = '';
  if (last.dest && last.dest.isAsset) {
    tagHtml = `
      <div class="row" style="gap:8px;margin-top:10px;flex-wrap:wrap">
        <span class="faint">Tag these for the asset:</span>
        <button class="btn sm ghost" data-tag="style">As style refs</button>
        <button class="btn sm ghost" data-tag="subject">As subject refs</button>
      </div>`;
  }

  box.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(120px,1fr))">${grid}</div>${noteHtml}${tagHtml}`;

  if (last.dest && last.dest.isAsset) {
    box.querySelectorAll('[data-tag]').forEach((b) => b.onclick = () => tagRefs(b.dataset.tag, b));
  }
}

function tagRefs(role, btn) {
  if (!last || !last.dest || !last.dest.isAsset) { return; }
  btn.disabled = true;
  const refs = last.thumbs.map((u) => ({ url: u, role }));
  api.refs(last.dest.into, refs)
    .then(() => { toast('Tagged ' + refs.length + ' images as ' + role + ' refs', 'good'); refreshState(); })
    .catch((e) => { toast('Could not tag: ' + e.message, 'bad'); btn.disabled = false; });
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

export function mount() {
  on('open', (n) => { if (n === MINE) { open(); } });
}
